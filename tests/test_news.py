from __future__ import annotations

import json
from unittest.mock import patch

from maritime_risk.agents.news import search_maritime_news


def _mock_feed(entries: list[dict[str, str]]) -> object:
    class Entry(dict):  # type: ignore[type-arg]
        def __init__(self, data: dict[str, str]) -> None:
            super().__init__(data)
            self.__dict__.update(data)

    class MockFeed:
        def __init__(self) -> None:
            self.entries = [Entry(e) for e in entries]

    return MockFeed()


def test_search_finds_matching_articles() -> None:
    entries = [
        {
            "title": "Houthi attacks disrupt Red Sea shipping",
            "summary": "Multiple vessels rerouted via Cape of Good Hope",
            "published": "Mon, 01 Jul 2024 12:00:00 GMT",
            "link": "https://example.com/article1",
        },
        {
            "title": "New container terminal opens in Singapore",
            "summary": "Capacity expansion at PSA terminals",
            "published": "Tue, 02 Jul 2024 12:00:00 GMT",
            "link": "https://example.com/article2",
        },
    ]

    with patch("maritime_risk.agents.news.feedparser.parse", return_value=_mock_feed(entries)):
        result = json.loads(search_maritime_news.__wrapped__("Red Sea", "disruption"))

    assert result["region"] == "Red Sea"
    assert len(result["disruptions"]) == 3  # one match per RSS feed
    assert "Houthi" in result["disruptions"][0]["title"]


def test_search_no_matches() -> None:
    entries = [
        {
            "title": "New container terminal opens in Singapore",
            "summary": "Capacity expansion at PSA terminals",
            "published": "Tue, 02 Jul 2024 12:00:00 GMT",
            "link": "https://example.com/article2",
        },
    ]

    with patch("maritime_risk.agents.news.feedparser.parse", return_value=_mock_feed(entries)):
        result = json.loads(search_maritime_news.__wrapped__("Suez", "closure"))

    assert result["disruptions"] == []
    assert "No disruptions found" in result["message"]


def test_search_returns_json() -> None:
    entries = [
        {
            "title": "Panama Canal draft restrictions tightened",
            "summary": "Drought reduces daily transits to 24",
            "published": "Wed, 03 Jul 2024 12:00:00 GMT",
            "link": "https://example.com/article3",
        },
    ]

    with patch("maritime_risk.agents.news.feedparser.parse", return_value=_mock_feed(entries)):
        raw = search_maritime_news.__wrapped__("Panama", "restriction")

    parsed = json.loads(raw)
    assert isinstance(parsed, dict)
    assert "disruptions" in parsed
