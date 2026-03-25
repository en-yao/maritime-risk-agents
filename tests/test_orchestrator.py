from __future__ import annotations

from maritime_risk.orchestrator import SYSTEM_PROMPT, create_orchestrator


def test_orchestrator_has_all_tools() -> None:
    """Verify orchestrator is wired with all 4 tools."""
    agent = create_orchestrator()
    tool_names = {t["name"] for t in agent.tool_registry.get_all_tool_specs()}
    assert "search_maritime_news" in tool_names
    assert "calculate_route" in tool_names
    assert "calculate_alternative_route" in tool_names
    assert "check_weather" in tool_names


def test_orchestrator_system_prompt() -> None:
    """Verify system prompt contains key instructions."""
    assert "calculate_route" in SYSTEM_PROMPT
    assert "search_maritime_news" in SYSTEM_PROMPT
    assert "check_weather" in SYSTEM_PROMPT
    assert "calculate_alternative_route" in SYSTEM_PROMPT
    assert "Do NOT hallucinate" in SYSTEM_PROMPT


def test_orchestrator_has_four_tools() -> None:
    agent = create_orchestrator()
    tools = agent.tool_registry.get_all_tool_specs()
    assert len(tools) == 4
