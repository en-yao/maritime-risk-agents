from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import feedparser
from strands import tool

RSS_FEEDS = [
    "https://gcaptain.com/feed/",
    "https://www.maritime-executive.com/feed",
    "https://www.hellenicshippingnews.com/feed/",
]


def _search_rss(region: str, keywords: list[str]) -> list[dict[str, str]]:
    """Search live RSS feeds for current disruptions."""
    search_terms = [t.lower() for t in [region, *keywords]]
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

    return results


def _search_gdelt(region: str, keywords: list[str], date: str) -> list[dict[str, str]]:
    """Search GDELT historical news archive for a specific date range."""
    from gdeltdoc import Filters, GdeltDoc

    query = f"{region} {' '.join(keywords)}"

    # Search 7 days before the departure date
    try:
        dt = datetime.fromisoformat(date)
    except (ValueError, TypeError):
        dt = datetime.now(timezone.utc)

    end = dt.strftime("%Y-%m-%d")
    start_dt = dt.replace(day=max(1, dt.day - 7))
    start = start_dt.strftime("%Y-%m-%d")

    try:
        f = Filters(keyword=query, start_date=start, end_date=end)
        gd = GdeltDoc()
        articles = gd.article_search(f)
    except Exception:
        return []

    results: list[dict[str, str]] = []
    if articles is not None and len(articles) > 0:
        for _, row in articles.head(10).iterrows():
            results.append({
                "source": "GDELT",
                "title": str(row.get("title", "")),
                "date": str(row.get("seendate", "")),
                "summary": str(row.get("title", ""))[:300],
                "url": str(row.get("url", "")),
            })

    return results


@tool
def search_maritime_news(region: str, keywords: str, date: str = "") -> str:
    """Search maritime news for disruptions affecting a region.

    Uses live RSS feeds for current assessments, or GDELT historical archive
    when a date is provided for backtesting.

    Args:
        region: Geographic region to search (e.g., "Red Sea", "Suez", "Panama")
        keywords: Additional search terms (e.g., "closure", "strike", "storm")
        date: Optional departure date (YYYY-MM-DD) for historical search
    """
    kw_list = [k.strip() for k in keywords.split(",")]

    # Use GDELT for historical dates, RSS for live
    backtest_mode = os.environ.get("BACKTEST_MODE", "").lower() == "true"
    if backtest_mode and date:
        results = _search_gdelt(region, kw_list, date)
    else:
        results = _search_rss(region, kw_list)

    if not results:
        return json.dumps(
            {"region": region, "disruptions": [], "message": "No disruptions found"}
        )

    return json.dumps({"region": region, "disruptions": results})
