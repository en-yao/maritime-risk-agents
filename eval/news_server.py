"""Local RSS server that serves scraped gCaptain archive as an RSS feed.

Usage:
    uv run python -m eval.news_server

Serves articles from eval/data/news_feed.json as RSS on localhost:8765.
Supports a ?before=YYYY-MM-DD query parameter to simulate checking
the feed on a specific date (returns articles on or before that date).

For backtest, set: NEWS_RSS_FEEDS=http://localhost:8765/feed
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

DATA_PATH = Path(__file__).parent / "data" / "news_feed.json"
PORT = 8765


def load_articles() -> list[dict[str, str]]:
    if DATA_PATH.exists():
        return json.loads(DATA_PATH.read_text())  # type: ignore[no-any-return]
    return []


def build_rss(articles: list[dict[str, str]]) -> str:
    """Build RSS XML from article list."""
    items = []
    for article in articles[:20]:
        date = article.get("date", "")
        try:
            dt = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            pub_date = dt.strftime("%a, %d %b %Y %H:%M:%S +0000")
        except (ValueError, TypeError):
            pub_date = ""

        items.append(
            f"<item>"
            f"<title>{_escape(article.get('title', ''))}</title>"
            f"<description>{_escape(article.get('summary', ''))}</description>"
            f"<link>{_escape(article.get('url', ''))}</link>"
            f"<pubDate>{pub_date}</pubDate>"
            f"</item>"
        )

    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0">'
        "<channel>"
        "<title>gCaptain Maritime News (Archive)</title>"
        f"{''.join(items)}"
        "</channel>"
        "</rss>"
    )


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


class FeedHandler(BaseHTTPRequestHandler):
    articles: list[dict[str, str]] = []

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query)

        # Filter articles by ?before=YYYY-MM-DD
        before = params.get("before", ["9999-12-31"])[0]
        filtered = [a for a in self.articles if a.get("date", "") <= before]

        # Return most recent 20
        filtered.sort(key=lambda a: a.get("date", ""), reverse=True)
        rss = build_rss(filtered[:20])

        self.send_response(200)
        self.send_header("Content-Type", "application/rss+xml")
        self.end_headers()
        self.wfile.write(rss.encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:
        pass  # suppress logging


def main() -> None:
    FeedHandler.articles = load_articles()
    print(f"Loaded {len(FeedHandler.articles)} articles from {DATA_PATH}")
    print(f"RSS server running on http://localhost:{PORT}/feed")
    print(f"Set: NEWS_RSS_FEEDS=http://localhost:{PORT}/feed")
    print("Use ?before=YYYY-MM-DD to simulate historical date")

    server = HTTPServer(("localhost", PORT), FeedHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped.")


if __name__ == "__main__":
    main()
