"""Isolated draft-only trial for three additional review publishers."""

import copy
import re
import urllib.parse

from lxml import html as lxml_html


TRIAL_FEEDS = {
    "gsmarena": "https://www.gsmarena.com/rss-news-reviews.php3",
    "pcgamer": "https://www.pcgamer.com/rss/",
    "wccftech": "https://wccftech.com/feed/",
}
TRIAL_HOSTS = {
    "gsmarena": {"gsmarena.com", "www.gsmarena.com"},
    "pcgamer": {"pcgamer.com", "www.pcgamer.com"},
    "wccftech": {"wccftech.com", "www.wccftech.com"},
}
GSMARENA_REVIEW_PATH = re.compile(r"^/[^/]+-review-(\d+)\.php$", re.I)


def trial_source_name(url):
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port:
            return None
    except ValueError:
        return None
    return next(
        (name for name, hosts in TRIAL_HOSTS.items() if parsed.hostname in hosts),
        None,
    )


def trial_feed_urls(source):
    if source == "all":
        return list(TRIAL_FEEDS.values())
    if source not in TRIAL_FEEDS:
        raise ValueError("Unknown review trial source")
    return [TRIAL_FEEDS[source]]


def is_trial_review_url(url, source="all"):
    name = trial_source_name(url)
    if not name or (source != "all" and name != source):
        return False
    path = urllib.parse.urlsplit(url).path.lower()
    if name == "gsmarena":
        return GSMARENA_REVIEW_PATH.fullmatch(path) is not None
    if name == "pcgamer":
        return path.rstrip("/").split("/")[-1].endswith("-review")
    return path.startswith("/review/") and len(path.strip("/").split("/")) == 2


def validate_trial_review_url(raw_url, source="all"):
    parsed = urllib.parse.urlsplit(raw_url.strip())
    clean = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))
    if not is_trial_review_url(clean, source):
        raise ValueError("Use an HTTPS review article URL from the selected trial source")
    return clean


def trial_article_body_node(document, article_url):
    name = trial_source_name(article_url)
    if name == "gsmarena":
        nodes = document.xpath('//*[@id="review-body"]')
    elif name == "wccftech":
        nodes = document.xpath(
            '//main//div[contains(concat(" ", normalize-space(@class), " "), " post ")]'
        )
    else:
        nodes = []  # PC Gamer already uses the standard #article-body selector.
    return nodes[0] if nodes else None


def _trim_body_images(body, maximum):
    for image in body.xpath(".//img")[maximum:]:
        parent = image.getparent()
        if parent is None:
            continue
        if parent.tag in {"a", "picture", "figure"} and not " ".join(parent.itertext()).strip():
            grandparent = parent.getparent()
            if grandparent is not None:
                grandparent.remove(parent)
        else:
            parent.remove(image)


def _gsmarena_pages(document, article_url):
    parsed = urllib.parse.urlsplit(article_url)
    match = GSMARENA_REVIEW_PATH.fullmatch(parsed.path)
    if not match:
        return []
    page_path = re.compile(re.escape(parsed.path[:-4]) + r"p(\d+)\.php$", re.I)
    pages = {}
    for href in document.xpath('//a[@href]/@href'):
        target = urllib.parse.urlsplit(urllib.parse.urljoin(article_url, href))
        number = page_path.fullmatch(target.path)
        if target.hostname == parsed.hostname and target.scheme == "https" and number:
            pages[int(number.group(1))] = urllib.parse.urlunsplit(
                (target.scheme, target.netloc, target.path, "", "")
            )
    if len(pages) > 11 or sorted(pages) != list(range(2, len(pages) + 2)):
        raise ValueError("GSMArena review pagination is incomplete or too large")
    return [pages[number] for number in sorted(pages)]


def prepare_trial_review_html(downloaded, article_url, fetch_page):
    """Merge GSMArena review pages, then trim publisher commerce/UI material."""
    name = trial_source_name(article_url)
    document = lxml_html.fromstring(downloaded)
    if name == "gsmarena":
        body = trial_article_body_node(document, article_url)
        if body is None:
            raise ValueError("GSMArena review body is missing")
        _trim_body_images(body, 2)
        for page_url in _gsmarena_pages(document, article_url):
            page_html = fetch_page(page_url)
            if not page_html:
                raise ValueError(f"Could not download GSMArena review page: {page_url}")
            page = lxml_html.fromstring(page_html)
            page_body = trial_article_body_node(page, page_url)
            if page_body is None:
                raise ValueError(f"GSMArena review page has no article body: {page_url}")
            _trim_body_images(page_body, 2)
            for child in page_body:
                body.append(copy.deepcopy(child))
    elif name == "pcgamer":
        body = document.xpath('//*[@id="article-body"]')
        if not body:
            raise ValueError("PC Gamer review body is missing")
        for node in document.xpath(
            '//*[@id="article-body"]//aside[contains(concat(" ", normalize-space(@class), " "), " ecom-root ")]'
        ):
            node.getparent().remove(node)
        _trim_body_images(body[0], 12)
    elif name == "wccftech":
        body = trial_article_body_node(document, article_url)
        if body is None:
            raise ValueError("Wccftech review body is missing")
        blocked = {"story-share", "related-story", "content-ad", "author-notice", "story-notice"}
        for node in list(body.xpath(".//*[@class]")):
            if node.getparent() is not None and set((node.get("class") or "").split()) & blocked:
                node.getparent().remove(node)
        for node in list(body.xpath("./p")):
            wording = " ".join(" ".join(node.itertext()).split()).lower()
            if "can be bought from the following links" not in wording:
                continue
            next_node = node.getnext()
            if next_node is not None and next_node.tag in {"ul", "ol"}:
                body.remove(next_node)
            body.remove(node)
        _trim_body_images(body, 12)
    return lxml_html.tostring(document, encoding="unicode", method="html")
