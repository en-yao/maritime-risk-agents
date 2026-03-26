from __future__ import annotations

import os
from typing import Any

import json

import structlog
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from pydantic import ValidationError
from strands import Agent
from strands.models import AnthropicModel

from maritime_risk.agents.news import search_maritime_news
from maritime_risk.agents.routes import calculate_alternative_route, calculate_route
from maritime_risk.schemas import ShipmentAssessment
from maritime_risk.tools.weather import check_weather

logger = structlog.get_logger()

SYSTEM_PROMPT = """\
You are a maritime shipping risk assessor. Given a shipment origin and \
destination, assess delay risk and suggest reroutes if warranted.

Follow this reasoning loop:

1. Call calculate_route to get the planned route geometry and distance.
2. For routes >5000nm between Europe and Asia, ALWAYS check Suez Canal \
and Red Sea specifically — these are critical chokepoints.
3. For each leg, check search_maritime_news with the region/ports relevant \
to that leg. Also call check_weather for key waypoints.
4. For each leg, estimate the expected delay in days (not just a risk level). \
Use evidence from news and weather to justify the estimate.
5. If ANY leg has disruption signals, ALWAYS call calculate_alternative_route \
for comparison — even if you ultimately recommend proceeding. Show the \
transit time delta so the user can decide.
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


def _create_model() -> AnthropicModel:
    """Create Anthropic model."""
    return AnthropicModel(
        model_id=os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001"),
        max_tokens=int(os.environ.get("ANTHROPIC_MAX_TOKENS", "2048")),
    )


def create_orchestrator() -> Agent:
    """Create the maritime risk orchestrator agent."""
    return Agent(
        tools=[search_maritime_news, calculate_route, calculate_alternative_route, check_weather],
        model=_create_model(),
        system_prompt=SYSTEM_PROMPT,
    )


# --- AgentCore Runtime Entrypoint ---


def _build_app() -> BedrockAgentCoreApp:
    """Build the AgentCore app. Deferred to avoid ddtrace/Starlette conflict."""
    app = BedrockAgentCoreApp()

    @app.entrypoint
    def handler(
        payload: dict[str, str], context: dict[str, str],
    ) -> dict[str, object]:
        """Handle incoming AgentCore invocation."""
        prompt = payload.get("prompt", "")
        if not prompt:
            return {
                "error": "missing_prompt",
                "message": "Request must include a 'prompt' field.",
            }

        logger.info("assessment_request", prompt_preview=prompt[:80])
        agent = create_orchestrator()
        result = agent(prompt)

        # Extract text from agent response
        message = result.message
        if isinstance(message, dict):
            for block in message.get("content", []):
                if isinstance(block, dict) and "text" in block:
                    message = block["text"]
                    break

        # Validate against schema if JSON output
        try:
            text = str(message)
            json_start = text.find("{")
            json_end = text.rfind("}") + 1
            if json_start >= 0 and json_end > json_start:
                parsed = json.loads(text[json_start:json_end])
                assessment = ShipmentAssessment.model_validate(parsed)
                logger.info("assessment_complete", validated=True)
                return {"assessment": assessment.model_dump()}
        except (json.JSONDecodeError, ValidationError) as e:
            logger.warning("assessment_validation_failed", error=str(e))

        logger.info("assessment_complete", validated=False)
        return {"assessment": message}

    return app


if __name__ == "__main__":
    _build_app().run()
