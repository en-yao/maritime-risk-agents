"""Scrape gCaptain monthly archives for backtest period.

Usage:
    uv run python -m eval.scrape_news

Saves articles to eval/data/news_feed.json for backtest use.
Simulates the RSS feed the live system subscribes to.

Current period: Oct 2023 - Feb 2024 (Panama Canal drought restrictions).
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent / "data"
MONTHLY_URL = "https://gcaptain.com/{year}/{month:02d}/page/{page}/"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; research-bot)"}
MONTHS = [(2023, m) for m in range(10, 13)] + [(2024, m) for m in range(1, 3)]  # Oct 2023 - Feb 2024
MAX_PAGES = 30


def parse_date(date_str: str) -> str | None:
    """Parse gCaptain date format to YYYY-MM-DD."""
    try:
        dt = datetime.strptime(date_str.strip(), "%B %d, %Y")
        return dt.strftime("%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def scrape_month(year: int, month: int) -> list[dict[str, str]]:
    """Scrape all articles from a gCaptain monthly archive."""
    articles: list[dict[str, str]] = []

    for page in range(1, MAX_PAGES + 1):
        url = MONTHLY_URL.format(year=year, month=month, page=page)

        try:
            resp = httpx.get(url, timeout=15.0, follow_redirects=True, headers=HEADERS)
            if resp.status_code == 404:
                break
            resp.raise_for_status()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            print(f"    Page {page}: Error {e}")
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        found = 0

        for article_div in soup.find_all("div", class_="article"):
            date_span = article_div.find("span", class_="date")
            headline_link = article_div.find("a", class_="headline")
            summary_p = article_div.find("p")

            if not date_span or not headline_link:
                continue

            date = parse_date(date_span.get_text(strip=True))
            if not date:
                continue

            title = headline_link.get_text(strip=True)
            url_str = headline_link.get("href", "")
            summary = summary_p.get_text(strip=True)[:300] if summary_p else ""

            articles.append({
                "source": "gCaptain",
                "title": title,
                "date": date,
                "summary": summary,
                "url": url_str,
            })
            found += 1

        print(f"    Page {page}: {found} articles")
        if found == 0:
            break

        time.sleep(1)

    return articles


def main() -> None:
    all_articles: list[dict[str, str]] = []

    for year, month in MONTHS:
        print(f"Scraping {year}-{month:02d}...")
        articles = scrape_month(year, month)
        all_articles.extend(articles)
        print(f"  Subtotal: {len(articles)} articles")

    all_articles.sort(key=lambda a: a["date"])

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    output_path = DATA_DIR / "news_feed.json"
    output_path.write_text(json.dumps(all_articles, indent=2))
    print(f"\nTotal: {len(all_articles)} articles saved to {output_path}")


if __name__ == "__main__":
    main()
