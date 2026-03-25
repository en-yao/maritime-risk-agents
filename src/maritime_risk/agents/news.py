from __future__ import annotations

import json
from datetime import datetime, timezone

import feedparser
from strands import tool

RSS_FEEDS = [
    "https://gcaptain.com/feed/",
    "https://www.maritime-executive.com/feed",
    "https://www.hellenicshippingnews.com/feed/",
]


@tool
def search_maritime_news(region: str, keywords: str) -> str:
    """Search maritime news RSS feeds for disruptions affecting a region.

    Args:
        region: Geographic region to search (e.g., "Red Sea", "Suez", "Panama")
        keywords: Additional search terms (e.g., "closure", "strike", "storm")
    """
    search_terms = [t.lower() for t in [region, *keywords.split(",")]]
    results: list[dict[str, str]] = []

    for feed_url in RSS_FEEDS:
        feed = feedparser.parse(feed_url)
        for entry in feed.entries[:20]:
            title = entry.get("title", "")
            summary = entry.get("summary", "")
            text = f"{title} {summary}".lower()

            if any(term.strip() in text for term in search_terms):
                published = entry.get("published", "")
                if hasattr(entry, "published_parsed") and entry.published_parsed:
                    t = entry.published_parsed
                    published = datetime(
                        t[0], t[1], t[2], t[3], t[4], t[5], tzinfo=timezone.utc
                    ).isoformat()

                results.append({
                    "source": feed_url,
                    "title": title,
                    "date": published,
                    "summary": summary[:300],
                    "url": entry.get("link", ""),
                })

    if not results:
        return json.dumps({"region": region, "disruptions": [], "message": "No disruptions found"})

    return json.dumps({"region": region, "disruptions": results})
