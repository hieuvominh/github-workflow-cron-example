"""Helpers for a one-URL review draft run."""

import urllib.parse

from lxml import html as lxml_html


REVIEW_HOSTS = {
    "techradar.com", "www.techradar.com",
    "theverge.com", "www.theverge.com",
    "ign.com", "www.ign.com",
    "cnet.com", "www.cnet.com",
    "techguide.com.au", "www.techguide.com.au",
}


def validate_manual_review_url(raw_url):
    parsed = urllib.parse.urlsplit(raw_url.strip())
    if (parsed.scheme != "https" or parsed.hostname not in REVIEW_HOSTS
            or parsed.username or parsed.password or parsed.port or not parsed.path.strip("/")):
        raise ValueError("Manual review URL must be an HTTPS article URL from a configured review source")
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
