from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import feedparser
from strands import tool

RSS_FEEDS = [
    "https://gcaptain.com/feed/",
    "https://www.maritime-executive.com/feed",
    "https://www.hellenicshippingnews.com/feed/",
]

# Individual search terms for broader GDELT coverage
DISRUPTION_TERMS = [
    "Houthi",
    "Red Sea attack",
    "Suez canal",
    "Panama canal drought",
    "port strike",
    "shipping disruption",
    "vessel attack",
    "maritime security",
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
    """Search GDELT historical news archive with multiple queries."""
    from gdeltdoc import Filters, GdeltDoc

    try:
        dep_dt = datetime.fromisoformat(date.replace("+00:00", "")[:10])
    except (ValueError, TypeError):
        return []

    end = dep_dt.strftime("%Y-%m-%d")
    start = (dep_dt - timedelta(days=14)).strftime("%Y-%m-%d")

    # Build search queries: region-specific + general disruption terms
    queries = [
        f"{region} shipping",
        f"{region} disruption",
        region,
    ]
    for term in DISRUPTION_TERMS:
        if term.lower() != region.lower():
            queries.append(term)

    seen_urls: set[str] = set()
    results: list[dict[str, str]] = []
    gd = GdeltDoc()

    for query in queries:
        if len(results) >= 15:
            break

        try:
            f = Filters(keyword=query, start_date=start, end_date=end)
            articles = gd.article_search(f)
        except Exception:
            continue

        if articles is None or len(articles) == 0:
            continue

        for _, row in articles.iterrows():
            url = str(row.get("url", ""))
            if url in seen_urls:
                continue
            seen_urls.add(url)

            title = str(row.get("title", ""))
            # Only include articles relevant to maritime/shipping
            text = title.lower()
            maritime_terms = [
                "ship", "port", "vessel", "maritime", "cargo", "container",
                "canal", "strait", "houthi", "red sea", "suez", "panama",
                "freight", "tanker", "route", "delay", "disruption",
                region.lower(),
            ]
            if not any(t in text for t in maritime_terms):
                continue

            results.append({
                "source": "GDELT",
                "title": title,
                "date": str(row.get("seendate", "")),
                "summary": title[:300],
                "url": url,
            })

    return results[:15]


@tool
def search_maritime_news(region: str, keywords: str, date: str = "") -> str:
    """Search maritime news for disruptions affecting a region.

    Uses live RSS feeds for current assessments, or GDELT historical archive
    for backtesting with historical dates.

    Args:
        region: Geographic region to search (e.g., "Red Sea", "Suez", "Panama")
        keywords: Additional search terms (e.g., "closure", "strike", "storm")
        date: Optional departure date (YYYY-MM-DD) for historical search
    """
    kw_list = [k.strip() for k in keywords.split(",")]

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
