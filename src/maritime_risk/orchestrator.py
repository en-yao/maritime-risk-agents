from __future__ import annotations

import os

import uuid
from collections.abc import AsyncGenerator

import structlog
from ag_ui.core import (
    EventType,
    RunAgentInput,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ToolCallEndEvent,
    ToolCallStartEvent,
)
from bedrock_agentcore.runtime import AGUIApp
from strands import Agent
from strands.hooks import HookProvider
from strands.models import AnthropicModel

from maritime_risk.agents.news import search_maritime_news
from maritime_risk.agents.routes import calculate_alternative_route, calculate_route
from maritime_risk.tools.weather import check_weather

logger = structlog.get_logger()

SYSTEM_PROMPT = """\
You are a maritime shipping operational delay assessor. Given a shipment \
origin and destination, assess operational delay risk and suggest reroutes \
if warranted.

SCOPE — assess these operational disruptions:
- Port congestion and berth availability
- Weather delays (storms, wind, visibility)
- Canal restrictions or closures (draft limits, maintenance, reduced transits)
- Port strikes and labor actions
- Equipment and infrastructure failures

DELAY ESTIMATION RULES — use these to quantify delays from news signals:
- Canal transit reductions (e.g., "transits cut from 36 to 18/day"): vessels \
queue for slots. Estimate 3-7 day waiting delay depending on reduction severity.
- Port strikes: if active, estimate 2-5 day delay per day of strike plus \
1-3 days for backlog clearance after resolution.
- Draft restrictions at canals: vessels may need to lighten cargo or wait \
for tides. Estimate 1-3 day delay.
- Port congestion (elevated dwell times): estimate 1-4 day delay based on \
reported congestion levels.
- Record auction prices for canal slots (e.g., "$4M for a slot"): indicates \
extreme demand. Estimate 5-10 day delay for vessels without priority slots.

OUT OF SCOPE — flag but do NOT assess:
- Military conflict or armed attacks (escalate to security team)
- Piracy or terrorism threats (escalate to security team)
- Sanctions or embargoes (escalate to compliance team)
If news mentions war, attacks, or military activity on a route leg, set \
that leg's risk to "escalate" and note: "Security risk — defer to security \
team. Operational delay assessment not applicable."

Follow this reasoning loop:

1. Call calculate_route to get the planned route geometry and distance.
2. For routes >5000nm between Europe and Asia, ALWAYS check Suez Canal \
and Red Sea specifically — these are critical chokepoints.
3. For each leg, check search_maritime_news with the region/ports relevant \
to that leg. Also call check_weather for key waypoints.
4. For each leg, estimate the expected OPERATIONAL delay in days (not just \
a risk level). Use evidence from news and weather to justify the estimate. \
Do not estimate delays from security threats — those are out of scope.
5. If ANY leg has operational disruption signals, ALWAYS call \
calculate_alternative_route for comparison — even if you ultimately \
recommend proceeding. Show the transit time delta so the user can decide. \
Only recommend rerouting if the predicted delay exceeds the alternative \
route's transit time delta. Otherwise recommend proceeding and cite the \
comparison.
6. Output a structured assessment as JSON with this format:
{
  "shipment_id": "<vessel_name>_<departure_date>",
  "overall_risk": "low" | "medium" | "high",
  "predicted_delay_days": <number>,
  "leg_risks": [{"leg": "<origin> to <destination>", "risk_level": "low"|"medium"|"high", "delay_days_estimate": <number>, "confidence": <0-1>, "factors": ["<evidence>"]}],
  "reroute_options": [{"route": "<description>", "transit_days": <number>, "delta_vs_planned": <number>, "residual_risk": "low"|"medium"|"high", "rationale": "<why>"}],
  "recommendation": "<proceed|reroute|hold>: <explanation>",
  "confidence": <0-1>
}

Do NOT hallucinate data. If a source returns no results, report that \
explicitly. Cite evidence for every factor.\
"""


def _get_secret(secret_id: str) -> str:
    """Fetch secret from AWS Secrets Manager. Falls back to env var."""
    env_key = secret_id.rsplit("/", 1)[-1].upper().replace("-", "_")
    env_val = os.environ.get(env_key, "")
    if env_val:
        return env_val

    try:
        import boto3

        client = boto3.client(
            "secretsmanager",
            region_name=os.environ.get("AWS_REGION", "ap-southeast-1"),
        )
        return str(client.get_secret_value(SecretId=secret_id)["SecretString"])
    except Exception as e:
        logger.warning("secret_fetch_failed", secret_id=secret_id, error=str(e))
        return ""


def _create_model() -> AnthropicModel:
    """Create Anthropic model."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        api_key = _get_secret("maritime-risk/anthropic-api-key")
        if api_key:
            os.environ["ANTHROPIC_API_KEY"] = api_key

    return AnthropicModel(
        model_id=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=int(os.environ.get("ANTHROPIC_MAX_TOKENS", "2048")),
    )


def create_orchestrator(
    hooks: list[HookProvider] | None = None,
) -> Agent:
    """Create the maritime risk orchestrator agent."""
    return Agent(
        tools=[search_maritime_news, calculate_route, calculate_alternative_route, check_weather],
        model=_create_model(),
        system_prompt=SYSTEM_PROMPT,
        hooks=hooks or [],
    )


# --- AG-UI Event Helpers ---


def _extract_prompt(run_input: RunAgentInput) -> str:
    """Extract the last user message text from RunAgentInput."""
    for msg in reversed(run_input.messages):
        if hasattr(msg, "role") and msg.role == "user":
            content = msg.content
            return content if isinstance(content, str) else ""
    return ""


# --- AgentCore Runtime Entrypoint ---


def _build_app() -> AGUIApp:
    """Build the AgentCore AG-UI app."""
    app = AGUIApp()

    @app.entrypoint
    async def handler(run_input: RunAgentInput) -> AsyncGenerator[object, None]:
        """Stream AG-UI events for a maritime risk assessment."""
        prompt = _extract_prompt(run_input)
        if not prompt:
            yield RunErrorEvent(
                type=EventType.RUN_ERROR,
                message="No user message found in request.",
                code="INVALID_INPUT",
            )
            return

        run_id = str(uuid.uuid4())
        message_id = str(uuid.uuid4())
        logger.info("assessment_request", prompt_preview=prompt[:80])

        yield RunStartedEvent(
            type=EventType.RUN_STARTED,
            thread_id=run_input.thread_id,
            run_id=run_id,
        )

        agent = create_orchestrator()
        in_text = False

        try:
            async for event in agent.stream_async(prompt):
                # Text chunks from the model
                if "data" in event:
                    text = event["data"]
                    if isinstance(text, str) and text:
                        if not in_text:
                            yield TextMessageStartEvent(
                                type=EventType.TEXT_MESSAGE_START,
                                message_id=message_id,
                                role="assistant",
                            )
                            in_text = True
                        yield TextMessageContentEvent(
                            type=EventType.TEXT_MESSAGE_CONTENT,
                            message_id=message_id,
                            delta=text,
                        )

                # Tool call start
                elif "tool_use" in event:
                    tool = event["tool_use"]
                    yield ToolCallStartEvent(
                        type=EventType.TOOL_CALL_START,
                        tool_call_id=str(tool.get("toolUseId", "")),
                        tool_call_name=str(tool.get("name", "")),
                    )

                # Tool call result
                elif "tool_result" in event:
                    result = event["tool_result"]
                    yield ToolCallEndEvent(
                        type=EventType.TOOL_CALL_END,
                        tool_call_id=str(result.get("toolUseId", "")),
                    )

            if in_text:
                yield TextMessageEndEvent(
                    type=EventType.TEXT_MESSAGE_END,
                    message_id=message_id,
                )

        except Exception as e:
            logger.error("assessment_failed", error=str(e))
            yield RunErrorEvent(
                type=EventType.RUN_ERROR,
                message=str(e),
                code="AGENT_ERROR",
            )
            return

        logger.info("assessment_complete")
        yield RunFinishedEvent(
            type=EventType.RUN_FINISHED,
            thread_id=run_input.thread_id,
            run_id=run_id,
        )

    return app


if __name__ == "__main__":
    _build_app().run()
