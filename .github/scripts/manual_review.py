"""Helpers for a one-URL TechRadar review draft run."""

import urllib.parse

from lxml import html as lxml_html


def validate_manual_review_url(raw_url):
    parsed = urllib.parse.urlsplit(raw_url.strip())
    if (parsed.scheme != "https" or parsed.hostname not in {"techradar.com", "www.techradar.com"}
            or parsed.username or parsed.password or parsed.port or not parsed.path.strip("/")):
        raise ValueError("Manual review URL must be an HTTPS TechRadar article URL")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def source_article_title(downloaded):
    document = lxml_html.fromstring(downloaded)
    for value in document.xpath(
        "//meta[@property='og:title']/@content | //h1/text() | //title/text()"
    ):
        title = " ".join(value.split()).strip()
        if title:
            return title
    raise ValueError("Source article has no title")
