from __future__ import annotations

import os
from typing import Any

import structlog
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent
from strands.models import AnthropicModel

from maritime_risk.agents.news import search_maritime_news
from maritime_risk.agents.routes import calculate_alternative_route, calculate_route
from maritime_risk.tools.weather import check_weather

logger = structlog.get_logger()

SYSTEM_PROMPT = """\
You are a maritime shipping risk assessor. Given a shipment origin and \
destination, assess delay risk and suggest reroutes if warranted.

Follow this reasoning loop — do NOT execute all steps for every shipment:

1. Call calculate_route to get the planned route geometry and legs.
2. For each leg, decide which signals to check:
   - Call search_maritime_news with the region/ports relevant to that leg.
   - If news returns a disruption, call check_weather for that region too.
   - If news is clean, skip weather for that leg.
3. Combine all signals. If compound risk is high, call \
calculate_alternative_route to get alternatives avoiding the affected passages.
4. Output a structured assessment with risk per leg, reroute options (if any), \
recommendation, and confidence. Cite evidence for every factor.

Do NOT hallucinate data. If a source returns no results, report that \
explicitly. Only recommend reroutes when the risk justifies the added \
transit time.\
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
        logger.info("assessment_complete")
        return {"assessment": result.message}

    return app


if __name__ == "__main__":
    _build_app().run()
