#!/usr/bin/env python3
"""Save a URL's content as markdown to obsidian/read/."""

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlparse

import requests
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from readability import Document

SAVE_DIR = Path.home() / "obsidian" / "read"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Sites that need a real browser (JS rendering) to get content
JS_REQUIRED_DOMAINS = {"twitter.com", "x.com", "old.reddit.com", "substack.com"}


def detect_source(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if "twitter.com" in domain or "x.com" in domain:
        return "twitter"
    if "news.ycombinator.com" in domain:
        return "hackernews"
    if "reddit.com" in domain:
        return "reddit"
    if "github.com" in domain:
        return "github"
    if "youtube.com" in domain or "youtu.be" in domain:
        return "youtube"
    if "arxiv.org" in domain:
        return "arxiv"
    return "blog"


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:80].strip("-")


def needs_browser(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    return any(d in domain for d in JS_REQUIRED_DOMAINS)


def fetch(url: str) -> str:
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.text


def fetch_with_browser(url: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent=HEADERS["User-Agent"],
            viewport={"width": 1280, "height": 900},
            locale="en-US",
        )
        page = ctx.new_page()
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        # wait for content to render
        page.wait_for_timeout(5000)
        html = page.content()
        browser.close()
    return html


def to_old_reddit(url: str) -> str:
    """Convert any reddit URL to old.reddit.com for simpler HTML."""
    return re.sub(r"https?://(www\.)?reddit\.com", "https://old.reddit.com", url)


def extract_reddit(html: str) -> tuple[str, str]:
    """Custom extraction for Reddit threads via old.reddit.com."""
    soup = BeautifulSoup(html, "html.parser")

    # post title
    title_el = soup.find("a", class_="title")
    title = title_el.get_text().strip() if title_el else "[no title]"

    # post link (if it's a link post, not self post)
    parts = []
    if title_el and title_el.get("href", "").startswith("http"):
        link = title_el["href"]
        if "reddit.com" not in link:
            parts.append(f"**Link:** {link}\n")

    # post self-text
    post_body = soup.find("div", class_="expando")
    if post_body:
        body_md = post_body.find("div", class_="md")
        if body_md:
            text = body_md.get_text(separator=" ").strip()
            if text:
                parts.append(f"{text}\n")

    parts.append("---\n")

    # comments — old reddit nests div.comment inside each other
    def walk_comments(container, depth=0):
        comments = container.find_all("div", class_="comment", recursive=False)
        for comment in comments:
            author_el = comment.find("a", class_="author")
            author = author_el.get_text() if author_el else "[deleted]"
            body_el = comment.find("div", class_="md")
            if not body_el:
                continue
            text = body_el.get_text(separator=" ").strip()
            prefix = "  " * depth + ("> " if depth else "")
            parts.append(f"{prefix}**{author}:** {text}\n")
            # recurse into child comments
            child = comment.find("div", class_="child")
            if child:
                walk_comments(child, depth + 1)

    comment_area = soup.find("div", class_="commentarea")
    if comment_area:
        sitetable = comment_area.find("div", class_="sitetable")
        if sitetable:
            walk_comments(sitetable)

    return title, "\n".join(parts).strip()


def extract_hn(html: str) -> tuple[str, str]:
    """Custom extraction for Hacker News threads."""
    soup = BeautifulSoup(html, "html.parser")
    # get the submission title and link
    titleline = soup.find("span", class_="titleline")
    title = titleline.get_text() if titleline else "[no title]"
    link = titleline.find("a")["href"] if titleline and titleline.find("a") else ""

    parts = []
    if link and not link.startswith("item"):
        parts.append(f"**Link:** {link}\n")

    # get comments
    comment_rows = soup.find_all("tr", class_="athing comtr")
    for row in comment_rows:
        user_el = row.find("a", class_="hnuser")
        user = user_el.text if user_el else "?"
        indent_el = row.find("td", class_="ind")
        indent = 0
        if indent_el:
            img = indent_el.find("img")
            if img and img.get("width"):
                indent = int(img["width"]) // 40  # each indent level is 40px
        text_el = row.find("div", class_=re.compile(r"commtext"))
        if not text_el:
            continue
        text = text_el.get_text(separator=" ").strip()
        prefix = "  " * indent + ("> " if indent else "")
        parts.append(f"{prefix}**{user}:** {text}\n")

    return title.strip(), "\n".join(parts).strip()


def extract(html: str, url: str) -> tuple[str, str]:
    doc = Document(html, url=url)
    title = doc.title()
    content_html = doc.summary()
    # clean up with beautifulsoup
    soup = BeautifulSoup(content_html, "html.parser")
    # remove empty tags
    for tag in soup.find_all():
        if not tag.get_text(strip=True) and tag.name not in ("img", "br", "hr"):
            tag.decompose()
    content_md = md(str(soup), heading_style="ATX", strip=["img"])
    # clean up excessive newlines
    content_md = re.sub(r"\n{3,}", "\n\n", content_md)
    return title.strip(), content_md.strip()


def save(title: str, content: str, url: str, source: str, tags: list[str]) -> Path:
    date = datetime.now().strftime("%Y-%m-%d")
    slug = slugify(title) if title else slugify(urlparse(url).path)
    filename = f"{date}-{slug}.md"
    filepath = SAVE_DIR / filename

    # avoid overwriting
    counter = 1
    while filepath.exists():
        filepath = SAVE_DIR / f"{date}-{slug}-{counter}.md"
        counter += 1

    tag_str = "\n".join(f"  - {t}" for t in tags) if tags else ""
    tags_line = f"tags:\n{tag_str}" if tag_str else "tags: []"
    frontmatter = f"""---
title: "{title}"
url: {url}
source: {source}
saved: {date}
{tags_line}
---"""

    filepath.write_text(f"{frontmatter}\n\n{content}\n")
    return filepath


def main():
    parser = argparse.ArgumentParser(description="Save a URL to obsidian/read/")
    parser.add_argument("url", help="URL to save")
    parser.add_argument("--tags", "-t", nargs="*", default=[], help="Tags to add")
    args = parser.parse_args()

    url = args.url
    source = detect_source(url)

    fetch_url = to_old_reddit(url) if source == "reddit" else url

    use_browser = needs_browser(fetch_url)
    print(f"Fetching {fetch_url}..." + (" (using browser)" if use_browser else ""))
    try:
        html = fetch_with_browser(fetch_url) if use_browser else fetch(fetch_url)
    except Exception as e:
        print(f"Error fetching: {e}", file=sys.stderr)
        sys.exit(1)

    print("Extracting content...")
    if source == "hackernews":
        title, content = extract_hn(html)
    elif source == "reddit":
        title, content = extract_reddit(html)
    else:
        title, content = extract(html, fetch_url)

    filepath = save(title, content, url, source, args.tags)
    print(f"Saved: {filepath}")

    # Open the file in Obsidian
    relative_path = filepath.relative_to(SAVE_DIR.parent)
    obsidian_uri = f"obsidian://open?vault=obsidian&file={quote(str(relative_path))}"
    subprocess.run(["open", obsidian_uri])


if __name__ == "__main__":
    main()
