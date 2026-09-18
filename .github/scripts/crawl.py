import base64
import hashlib
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from io import BytesIO
from zoneinfo import ZoneInfo

import trafilatura
from lxml import etree as lxml_etree
from lxml import html as lxml_html
from PIL import Image, ImageOps, UnidentifiedImageError

from gemini_rewriter import rewrite_article


CATEGORY_SLUG = os.environ["CATEGORY_SLUG"]
RAW_FEED_URLS = (
    os.environ.get("FEED_URLS", "").strip()
    or os.environ.get("FEED_URL", "").strip()
)
SOURCE_FEED_URLS = list(
    dict.fromkeys(
        value.strip()
        for value in re.split(r"[,;\r\n]+", RAW_FEED_URLS)
        if value.strip()
    )
)
BYTEKORA_URL = os.environ["BYTEKORA_URL"]
INGEST_SECRET = os.environ["INGEST_SECRET"]
MEDIA_REPO = os.environ["MEDIA_REPO"]
MEDIA_BRANCH = os.environ.get("MEDIA_BRANCH", "main")
MEDIA_TOKEN = os.environ["MEDIA_TOKEN"]
PUBLISHED_TODAY_ONLY = os.environ.get("PUBLISHED_TODAY_ONLY", "false").lower() == "true"
CONTENT_TIMEZONE = os.environ.get("CONTENT_TIMEZONE", "Asia/Bangkok")
FEED_MAX_PAGES = max(1, int(os.environ.get("FEED_MAX_PAGES", "1")))
MAX_ARTICLES = max(0, int(os.environ.get("MAX_ARTICLES", "10")))
RAW_SOURCE_VERTICAL_RULES = os.environ.get("SOURCE_VERTICAL_RULES", "").strip()
IMAGE_MAX_WIDTH = max(320, int(os.environ.get("IMAGE_MAX_WIDTH", "1600")))
IMAGE_MAX_HEIGHT = max(320, int(os.environ.get("IMAGE_MAX_HEIGHT", "1600")))
IMAGE_WEBP_QUALITY = min(95, max(60, int(os.environ.get("IMAGE_WEBP_QUALITY", "84"))))
IMAGE_DOWNLOAD_MAX_BYTES = 30_000_000
IMAGE_UPLOAD_MAX_BYTES = 15_000_000
LAZY_IMAGE_ATTRIBUTES = (
    "src",
    "data-src",
    "data-lazy-src",
    "data-original",
    "data-hi-res-src",
)
IMAGE_SIZE_SUFFIX = re.compile(r"-\d{2,5}(?:[x-]\d{1,4})?$")
LEFTOVER_ENTITY = re.compile(r"&(?:#\d{2,6}|#x[0-9a-fA-F]{2,6}|[a-zA-Z]{2,10});")
ARTICLE_LD_TYPES = {
    "Article",
    "NewsArticle",
    "ReportageNewsArticle",
    "BlogPosting",
    "LiveBlogPosting",
    "TechArticle",
}

PROMOTIONAL_LABELS = {
    "advertisement",
    "advertorial",
    "brand studio",
    "branded content",
    "partner content",
    "partner post",
    "paid content",
    "promoted",
    "promotion",
    "sponsored",
    "sponsored content",
    "sponsored post",
}
PROMOTIONAL_URL_PARTS = (
    "/advertorial/",
    "/brand-studio/",
    "/branded-content/",
    "/partner-content/",
    "/paid-content/",
    "/promoted/",
    "/sponsored/",
    "/sponsored-content/",
)

Image.MAX_IMAGE_PIXELS = 50_000_000


def parse_source_vertical_rules(raw_rules):
    rules = {}
    for raw_rule in re.split(r"[,;\r\n]+", raw_rules or ""):
        raw_rule = raw_rule.strip()
        if not raw_rule:
            continue
        host, separator, raw_verticals = raw_rule.partition("=")
        if not separator or not host.strip() or not raw_verticals.strip():
            raise SystemExit(
                "Invalid SOURCE_VERTICAL_RULES entry. "
                "Use host=vertical|vertical, for example www.ign.com=games"
            )
        rules[host.strip().lower()] = {
            vertical.strip().lower()
            for vertical in raw_verticals.split("|")
            if vertical.strip()
        }
    return rules


SOURCE_VERTICAL_RULES = parse_source_vertical_rules(RAW_SOURCE_VERTICAL_RULES)

if not SOURCE_FEED_URLS:
    print(
        f"Skipped {CATEGORY_SLUG}: configure "
        "FEED_URLS in the workflow"
    )
    raise SystemExit(0)

if not MEDIA_REPO or "/" not in MEDIA_REPO or not MEDIA_TOKEN:
    raise SystemExit("MEDIA_REPO or MEDIA_TOKEN is missing")


def tag_name(element):
    return element.tag.rsplit("}", 1)[-1]


def element_text(element):
    return re.sub(r"\s+", " ", "".join(element.itertext())).strip()


def normalized_text(value):
    return re.sub(r"\W+", " ", value.lower()).strip()


def source_vertical_allowed(downloaded, article_url):
    host = urllib.parse.urlsplit(article_url).netloc.lower().split(":", 1)[0]
    allowed_verticals = SOURCE_VERTICAL_RULES.get(host)
    if not allowed_verticals:
        return True

    try:
        document = lxml_html.fromstring(downloaded)
    except (TypeError, ValueError, lxml_html.etree.ParserError):
        print(f"    Source vertical could not be read for {host}")
        return False

    verticals = {
        value.strip().lower()
        for value in document.xpath(
            "//meta[translate(@name, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', "
            "'abcdefghijklmnopqrstuvwxyz')='vertical']/@content"
        )
        if value.strip()
    }
    if not verticals:
        print(f"    Source vertical is missing for {host}")
        return False

    if verticals.isdisjoint(allowed_verticals):
        print(
            f"    Source vertical rejected for {host}: "
            f"{', '.join(sorted(verticals))}"
        )
        return False

    print(
        f"    Source vertical accepted for {host}: "
        f"{', '.join(sorted(verticals))}"
    )
    return True


def youtube_embed_url(value):
    if not value:
        return None
    parsed = urllib.parse.urlsplit(value)
    host = parsed.netloc.lower().split(":", 1)[0]
    video_id = None
    if host in ("youtu.be", "www.youtu.be"):
        video_id = parsed.path.strip("/").split("/", 1)[0]
    elif host in ("youtube.com", "www.youtube.com", "m.youtube.com"):
        if parsed.path.startswith("/embed/"):
            video_id = parsed.path.split("/embed/", 1)[1].split("/", 1)[0]
        elif parsed.path == "/watch":
            video_id = urllib.parse.parse_qs(parsed.query).get("v", [None])[0]
        elif parsed.path.startswith("/shorts/"):
            video_id = parsed.path.split("/shorts/", 1)[1].split("/", 1)[0]
    if not video_id or not re.fullmatch(r"[A-Za-z0-9_-]{6,20}", video_id):
        return None
    return f"https://www.youtube.com/embed/{video_id}"


def decoded_text(value):
    """Undo a second layer of HTML escaping.

    lxml already resolves entities once. Some publishers escape twice, so
    "Honor&amp;#039;s" survives parsing as the literal "Honor&#039;s". Only
    unescape when an entity is still present, to leave text that genuinely
    contains an ampersand alone.
    """
    value = value or ""
    if LEFTOVER_ENTITY.search(value):
        return html.unescape(value)
    return value


def image_source(element):
    for attribute in LAZY_IMAGE_ATTRIBUTES:
        value = (element.get(attribute) or "").strip()
        if value and not value.lower().startswith("data:"):
            return value
    raw_srcset = element.get("srcset") or element.get("data-srcset") or ""
    for candidate in raw_srcset.split(","):
        value = candidate.strip().split(" ", 1)[0]
        if value and not value.lower().startswith("data:"):
            return value
    return None


def image_identity(source_url):
    """Collapse CDN resize variants of one image onto a single key.

    og:image and the in-body <img> rarely agree on the exact URL: WordPress
    appends ?resize=1200,828 and Future CDN appends -1920-80 before the
    extension. Both still point at the same upload, so compare the file stem
    with query strings and trailing size suffixes removed.
    """
    path = urllib.parse.urlsplit(source_url).path
    name = path.rsplit("/", 1)[-1].lower()
    stem = name.rpartition(".")[0] or name
    previous = None
    while previous != stem:
        previous = stem
        stem = IMAGE_SIZE_SUFFIX.sub("", stem)
    return stem or source_url.lower()


def json_ld_image(document):
    for blob in document.xpath("//script[@type='application/ld+json']/text()"):
        try:
            data = json.loads(blob)
        except (ValueError, TypeError):
            continue
        pending = [data]
        while pending:
            node = pending.pop()
            if isinstance(node, list):
                pending.extend(node)
                continue
            if not isinstance(node, dict):
                continue
            if "@graph" in node:
                pending.extend(
                    node["@graph"]
                    if isinstance(node["@graph"], list)
                    else [node["@graph"]]
                )
                continue
            node_type = node.get("@type")
            if isinstance(node_type, list):
                node_type = node_type[0] if node_type else ""
            if node_type not in ARTICLE_LD_TYPES:
                continue
            image = node.get("image")
            if isinstance(image, list):
                image = image[0] if image else None
            if isinstance(image, dict):
                image = image.get("url")
            if isinstance(image, str) and image.strip():
                return image.strip()
    return None


def meta_content(document, attribute, name):
    return document.xpath(
        f"//meta[translate(@{attribute}, "
        "'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz')"
        f"='{name}']/@content"
    )


def hero_source_url(document):
    """Every supported publisher exposes the lead image as a social card.

    Site-specific class names were tried first and proved unmaintainable: the
    one selector this crawler carried stopped matching when its publisher
    redesigned, silently and with no other symptom.
    """
    for attribute, name in (
        ("property", "og:image"),
        ("property", "og:image:url"),
        ("name", "twitter:image"),
        ("name", "twitter:image:src"),
    ):
        for value in meta_content(document, attribute, name):
            value = (value or "").strip()
            if value and not value.lower().startswith("data:"):
                return value
    return json_ld_image(document)


def hero_figure_details(document, identity):
    """Recover alt text and caption by locating the hero inside the article."""
    for figure in document.xpath("//article//figure | //main//figure | //figure"):
        images = figure.xpath(".//img")
        if not images:
            continue
        source = image_source(images[0])
        if not source or image_identity(source) != identity:
            continue
        captions = figure.xpath(".//figcaption")
        return (
            decoded_text(images[0].get("alt"))[:500],
            decoded_text(element_text(captions[0]) if captions else "")[:1_000],
        )
    return "", ""


def extract_page_media(downloaded, article_url):
    try:
        document = lxml_html.fromstring(downloaded)
    except (TypeError, ValueError, lxml_html.etree.ParserError):
        return None, []

    hero = None
    source = hero_source_url(document)
    if source:
        absolute_source = urllib.parse.urljoin(article_url, source)
        identity = image_identity(absolute_source)
        alt, caption = hero_figure_details(document, identity)
        hero = {
            "type": "pending_image",
            "source": absolute_source,
            "alt": alt,
            "caption": caption,
        }
    else:
        for figure in document.xpath("//article//figure | //main//figure"):
            images = figure.xpath(".//img")
            figure_source = image_source(images[0]) if images else None
            if not figure_source:
                continue
            captions = figure.xpath(".//figcaption")
            hero = {
                "type": "pending_image",
                "source": urllib.parse.urljoin(article_url, figure_source),
                "alt": decoded_text(images[0].get("alt"))[:500],
                "caption": decoded_text(
                    element_text(captions[0]) if captions else ""
                )[:1_000],
            }
            break
    if not hero:
        print(f"    Hero image not found for {article_url}")

    videos = []
    seen_video_urls = set()

    def add_video(node, provider, embed_url, title=""):
        if not embed_url or embed_url in seen_video_urls:
            return
        seen_video_urls.add(embed_url)
        previous_paragraphs = node.xpath("preceding::p[normalize-space()][1]")
        videos.append(
            {
                "type": "pending_video",
                "provider": provider,
                "url": embed_url,
                "title": title[:300] or "Embedded video",
                "sourceUrl": article_url,
                "afterText": (
                    element_text(previous_paragraphs[0])
                    if previous_paragraphs
                    else ""
                ),
            }
        )

    jw_players = document.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), "
        "' wp-block-techcrunch-jw-player-embed ') and "
        "not(contains(concat(' ', normalize-space(@class), ' '), "
        "' jw-player-inline-promo '))]"
    )
    for player in jw_players:
        script_sources = player.xpath(".//script/@src")
        script_text = "\n".join(player.xpath(".//script/text()"))
        library_id = next(
            (
                match.group(1)
                for source in script_sources
                if (match := re.search(r"/libraries/([A-Za-z0-9_-]+)\.js", source))
            ),
            None,
        )
        media_match = re.search(
            r"cdn\.jwplayer\.com/v2/media/([A-Za-z0-9_-]+)",
            script_text,
        )
        if library_id and media_match:
            media_id = media_match.group(1)
            add_video(
                player,
                "jwplayer",
                f"https://cdn.jwplayer.com/players/{media_id}-{library_id}.html",
                "TechCrunch video",
            )

    for iframe in document.xpath("//iframe[@src]"):
        embed_url = youtube_embed_url(iframe.get("src"))
        if embed_url:
            add_video(
                iframe,
                "youtube",
                embed_url,
                iframe.get("title") or "YouTube video",
            )

    youtube_links = document.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), "
        "' wp-block-embed-youtube ')]//a[@href]"
    )
    for link in youtube_links:
        embed_url = youtube_embed_url(link.get("href"))
        if embed_url:
            add_video(link, "youtube", embed_url, element_text(link))

    return hero, videos


def add_page_media(blocks, hero, videos):
    if hero:
        blocks.insert(0, hero)

    for video in videos:
        insertion_index = len(blocks)
        anchor = normalized_text(video.pop("afterText", ""))
        if anchor:
            for index in range(len(blocks) - 1, -1, -1):
                block = blocks[index]
                if block.get("type") not in ("paragraph", "heading"):
                    continue
                candidate = normalized_text(block.get("text", ""))
                if candidate == anchor or candidate in anchor or anchor in candidate:
                    insertion_index = index + 1
                    break
        blocks.insert(insertion_index, video)
    return blocks


def feed_page_url(feed_url, page):
    if page == 1:
        return feed_url
    parsed = urllib.parse.urlsplit(feed_url)
    query = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
    query["paged"] = [str(page)]
    return urllib.parse.urlunsplit(
        (
            parsed.scheme,
            parsed.netloc,
            parsed.path,
            urllib.parse.urlencode(query, doseq=True),
            parsed.fragment,
        )
    )


def parse_published_at(value):
    if not value:
        return None
    try:
        published_at = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        try:
            published_at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if published_at.tzinfo is None:
        published_at = published_at.replace(tzinfo=timezone.utc)
    return published_at


def parse_feed(root):
    items = root.findall(".//item")
    if items:
        return [
            {
                "title": item.findtext("title"),
                "url": item.findtext("link"),
                "published_at": parse_published_at(item.findtext("pubDate")),
                "labels": [
                    (category.text or "").strip()
                    for category in item.findall("category")
                    if (category.text or "").strip()
                ],
            }
            for item in items
        ]

    namespace = {"atom": "http://www.w3.org/2005/Atom"}
    articles = []
    for entry in root.findall("atom:entry", namespace):
        links = entry.findall("atom:link", namespace)
        link = next(
            (
                candidate
                for candidate in links
                if candidate.get("rel", "alternate") == "alternate"
            ),
            links[0] if links else None,
        )
        published = entry.findtext("atom:published", namespaces=namespace)
        updated = entry.findtext("atom:updated", namespaces=namespace)
        articles.append(
            {
                "title": entry.findtext("atom:title", namespaces=namespace),
                "url": link.get("href") if link is not None else None,
                "published_at": parse_published_at(published or updated),
                "labels": [
                    (category.get("term") or category.get("label") or "").strip()
                    for category in entry.findall("atom:category", namespace)
                    if (category.get("term") or category.get("label") or "").strip()
                ],
            }
        )
    return articles


def promotional_reason(title, url, labels):
    normalized_labels = {
        re.sub(r"\s+", " ", str(label).strip().lower())
        for label in labels or []
    }
    blocked_labels = sorted(normalized_labels.intersection(PROMOTIONAL_LABELS))
    if blocked_labels:
        return f"feed label: {', '.join(blocked_labels)}"

    normalized_url = urllib.parse.unquote(url or "").lower()
    blocked_url_part = next(
        (part for part in PROMOTIONAL_URL_PARTS if part in normalized_url),
        None,
    )
    if blocked_url_part:
        return f"URL path: {blocked_url_part}"

    normalized_title = re.sub(r"\s+", " ", title or "").strip().lower()
    promotional_title = r"(?:sponsored(?: post)?|promoted|advertorial|partner content|branded content|paid content)"
    if re.match(rf"^\[{promotional_title}\]\s*", normalized_title) or re.match(
        rf"^{promotional_title}\s*[:|-]\s*",
        normalized_title,
    ):
        return "promotional title prefix"
    return None


def parse_feed_document(data, page_url):
    try:
        return ET.fromstring(data)
    except ET.ParseError as strict_error:
        decoded = data.decode("utf-8", errors="replace")
        cleaned = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", decoded)
        cleaned = re.sub(
            r"&(?!#\d+;|#x[0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9._:-]*;)",
            "&amp;",
            cleaned,
        )
        try:
            return ET.fromstring(cleaned.encode("utf-8"))
        except ET.ParseError:
            parser = lxml_etree.XMLParser(
                recover=True,
                no_network=True,
                resolve_entities=False,
            )
            recovered = lxml_etree.fromstring(cleaned.encode("utf-8"), parser=parser)
            if recovered is None or recovered.tag.rsplit("}", 1)[-1] not in ("rss", "feed"):
                preview = re.sub(r"\s+", " ", cleaned[:240]).strip()
                raise ET.ParseError(
                    f"Invalid RSS/Atom response from {page_url}; "
                    f"strict error: {strict_error}; response starts with: {preview!r}"
                ) from strict_error
            print(f"    Recovered malformed XML from {page_url}")
            return recovered


def fetch_feed_root(page_url):
    last_error = None
    content_type = "unavailable"
    for attempt in range(1, 4):
        feed_request = urllib.request.Request(
            page_url,
            headers={
                "User-Agent": "BYTERMINALBot/0.1 (+https://bytekora.com)",
                "Accept": "application/rss+xml, application/atom+xml, application/xml;q=0.9, text/xml;q=0.8",
            },
        )
        try:
            with urllib.request.urlopen(feed_request, timeout=30) as response:
                data = response.read()
                content_type = response.headers.get("Content-Type", "unknown")
            root = parse_feed_document(data, page_url)
            return root
        except (urllib.error.URLError, TimeoutError, ET.ParseError, lxml_etree.XMLSyntaxError) as error:
            last_error = error
            if attempt < 3:
                delay = attempt * 5
                print(
                    f"    Feed read attempt {attempt}/3 failed for {page_url}: {error}; "
                    f"retrying in {delay}s"
                )
                time.sleep(delay)
                continue
            print(
                f"    Feed response could not be parsed after 3 attempts; "
                f"content type: {content_type}"
            )
    raise last_error or RuntimeError(f"Unable to read feed {page_url}")


def collect_articles():
    target_date = datetime.now(ZoneInfo(CONTENT_TIMEZONE)).date()
    articles = []
    seen_urls = set()

    for feed_url in SOURCE_FEED_URLS:
        print(f"Reading feed: {feed_url}")
        pages_read = 0
        scanned_item_count = 0
        source_article_count = 0
        for page in range(1, FEED_MAX_PAGES + 1):
            page_url = feed_page_url(feed_url, page)
            try:
                root = fetch_feed_root(page_url)
            except (urllib.error.URLError, TimeoutError, ET.ParseError, lxml_etree.XMLSyntaxError) as error:
                print(f"    Feed page skipped: {page_url}: {error}")
                break

            page_articles = parse_feed(root)
            if not page_articles:
                break
            pages_read += 1
            scanned_item_count += len(page_articles)

            reached_older_article = False
            for article in page_articles:
                title = article["title"]
                url = article["url"]
                published_at = article["published_at"]
                if not title or not url or url in seen_urls:
                    continue

                promotion = promotional_reason(title, url, article.get("labels", []))
                if promotion:
                    print(f"Skipped promotional feed item ({promotion}): {title} — {url}")
                    continue

                if PUBLISHED_TODAY_ONLY:
                    if not published_at:
                        print(f"Skipped undated feed item: {url}")
                        continue
                    local_date = published_at.astimezone(ZoneInfo(CONTENT_TIMEZONE)).date()
                    if local_date < target_date:
                        reached_older_article = True
                        continue
                    if local_date > target_date:
                        continue

                seen_urls.add(url)
                articles.append(
                    (
                        title,
                        url,
                        published_at.isoformat() if published_at else None,
                    )
                )
                source_article_count += 1

            if not PUBLISHED_TODAY_ONLY or reached_older_article:
                break

        scope = f" for {target_date.isoformat()}" if PUBLISHED_TODAY_ONLY else ""
        print(
            f"    Found {source_article_count} eligible article(s){scope}; "
            f"scanned {scanned_item_count} item(s) across {pages_read} page(s)"
        )

    articles.sort(
        key=lambda article: parse_published_at(article[2])
        or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    print(
        f"Found {len(articles)} unique eligible article(s) "
        f"across {len(SOURCE_FEED_URLS)} configured feed(s)"
    )
    if MAX_ARTICLES:
        if len(articles) > MAX_ARTICLES:
            print(f"Processing the newest {MAX_ARTICLES} article(s) due to MAX_ARTICLES")
        return articles[:MAX_ARTICLES]

    return articles


def article_exists(source_url):
    query = urllib.parse.urlencode({"externalSourceID": source_url})
    request = urllib.request.Request(
        f"{BYTEKORA_URL.rstrip('/')}/api/crawler/posts?{query}",
        headers={"Authorization": f"Bearer {INGEST_SECRET}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
            return bool(result.get("exists"))
    except urllib.error.HTTPError as error:
        if error.code in (404, 405):
            print("    Duplicate preflight is unavailable; POST fallback will be used")
            return False
        raise


def optimize_image(image_data, content_type):
    extension_by_format = {
        "GIF": "gif",
        "JPEG": "jpg",
        "PNG": "png",
        "WEBP": "webp",
    }

    try:
        with Image.open(BytesIO(image_data)) as source:
            source_format = (source.format or "").upper()
            original_extension = extension_by_format.get(source_format)
            if not original_extension:
                return None

            if getattr(source, "is_animated", False):
                if len(image_data) > IMAGE_UPLOAD_MAX_BYTES:
                    return None
                return image_data, original_extension

            image = ImageOps.exif_transpose(source)
            original_dimensions = image.size
            image.thumbnail(
                (IMAGE_MAX_WIDTH, IMAGE_MAX_HEIGHT),
                Image.Resampling.LANCZOS,
            )
            was_resized = image.size != original_dimensions

            has_alpha = image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            )
            image = image.convert("RGBA" if has_alpha else "RGB")
            output = BytesIO()
            image.save(
                output,
                format="WEBP",
                quality=IMAGE_WEBP_QUALITY,
                method=6,
            )
            optimized_data = output.getvalue()

            if len(optimized_data) > IMAGE_UPLOAD_MAX_BYTES:
                return None
            if was_resized or len(optimized_data) < len(image_data):
                return optimized_data, "webp"
            if len(image_data) <= IMAGE_UPLOAD_MAX_BYTES:
                return image_data, original_extension
            return None
    except (Image.DecompressionBombError, UnidentifiedImageError, OSError):
        print(f"    Unsupported or unsafe image skipped ({content_type})")
        return None


def encoded_url(value):
    """Percent-encode non-ASCII path characters.

    Publishers do put raw Unicode in upload paths (an em dash in a 9to5Mac
    filename, for one). urlopen encodes the request line as ASCII, so such a
    URL raises UnicodeEncodeError and the image is dropped.
    """
    split = urllib.parse.urlsplit(value)
    return urllib.parse.urlunsplit(
        (
            split.scheme,
            split.netloc.encode("idna").decode("ascii")
            if not split.netloc.isascii()
            else split.netloc,
            urllib.parse.quote(split.path, safe="/%"),
            urllib.parse.quote(split.query, safe="=&/%?+,:"),
            split.fragment,
        )
    )


def upload_image(source_url, article_url):
    absolute_url = encoded_url(urllib.parse.urljoin(article_url, source_url))
    request = urllib.request.Request(
        absolute_url,
        headers={"User-Agent": "BYTERMINALBot/0.1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        content_type = response.headers.get_content_type().lower()
        image_data = response.read(IMAGE_DOWNLOAD_MAX_BYTES + 1)

    if len(image_data) > IMAGE_DOWNLOAD_MAX_BYTES:
        return None

    optimized = optimize_image(image_data, content_type)
    if not optimized:
        return None
    image_data, extension = optimized

    digest = hashlib.sha256(image_data).hexdigest()
    media_path = f"articles/{digest[:2]}/{digest}.{extension}"
    encoded_path = urllib.parse.quote(media_path, safe="/")
    api_url = f"https://api.github.com/repos/{MEDIA_REPO}/contents/{encoded_path}"
    api_headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {MEDIA_TOKEN}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "BYTERMINALBot/0.1",
    }

    exists_request = urllib.request.Request(
        f"{api_url}?ref={urllib.parse.quote(MEDIA_BRANCH)}",
        headers=api_headers,
    )
    try:
        with urllib.request.urlopen(exists_request, timeout=30):
            pass
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        upload_request = urllib.request.Request(
            api_url,
            data=json.dumps(
                {
                    "message": f"Add crawled image {digest[:12]}",
                    "content": base64.b64encode(image_data).decode(),
                    "branch": MEDIA_BRANCH,
                }
            ).encode(),
            headers={**api_headers, "Content-Type": "application/json"},
            method="PUT",
        )
        with urllib.request.urlopen(upload_request, timeout=60):
            pass

    branch = urllib.parse.quote(MEDIA_BRANCH, safe="")
    return (
        f"https://raw.githubusercontent.com/{MEDIA_REPO}/"
        f"{branch}/{encoded_path}"
    )


def extract_blocks(downloaded, article_url):
    hero, videos = extract_page_media(downloaded, article_url)
    extracted_xml = trafilatura.extract(
        downloaded,
        url=article_url,
        include_comments=False,
        include_tables=False,
        include_images=True,
        include_formatting=True,
        favor_precision=True,
        output_format="xml",
    )
    if not extracted_xml:
        return []

    document = ET.fromstring(extracted_xml)
    main = next(
        (node for node in document.iter() if tag_name(node) == "main"),
        document,
    )
    blocks = []
    seen_image_sources = set()
    if hero:
        seen_image_sources.add(image_identity(hero["source"]))

    def walk(node):
        kind = tag_name(node)
        if kind == "head":
            text = element_text(node)
            if text:
                level = node.get("rend", "h2")
                blocks.append(
                    {
                        "type": "heading",
                        "level": level if level in ("h2", "h3") else "h2",
                        "text": text[:500],
                    }
                )
            return
        if kind in ("p", "quote", "item"):
            text = element_text(node)
            if text:
                prefix = "• " if kind == "item" else ""
                blocks.append(
                    {
                        "type": "paragraph",
                        "text": (prefix + text)[:10_000],
                    }
                )
            for image in (
                child for child in node.iter() if tag_name(child) == "graphic"
            ):
                walk(image)
            return
        if kind == "graphic":
            source = node.get("src")
            if source:
                absolute_source = urllib.parse.urljoin(article_url, source)
                identity = image_identity(absolute_source)
                if identity in seen_image_sources:
                    return
                seen_image_sources.add(identity)
                blocks.append(
                    {
                        "type": "pending_image",
                        "source": absolute_source,
                        "alt": decoded_text(
                            node.get("alt") or node.get("title")
                        )[:500],
                        "caption": decoded_text(node.get("title"))[:1_000],
                    }
                )
            return
        for child in node:
            walk(child)

    walk(main)
    return add_page_media(blocks, hero, videos)


articles = collect_articles()
if not articles:
    scope = f" for today in {CONTENT_TIMEZONE}" if PUBLISHED_TODAY_ONLY else ""
    print(f"No RSS/Atom articles found{scope}")
    raise SystemExit(0)

for number, (title, url, published_at) in enumerate(articles, 1):
    if article_exists(url):
        print(f"{number:02}. Skipped duplicate: {url}")
        continue

    downloaded = trafilatura.fetch_url(url)
    if downloaded and not source_vertical_allowed(downloaded, url):
        print(f"{number:02}. Skipped outside {CATEGORY_SLUG}: {url}")
        continue
    blocks = extract_blocks(downloaded, url) if downloaded else []
    text_blocks = [
        block["text"]
        for block in blocks
        if block["type"] in ("paragraph", "heading")
    ]
    clean_text = "\n\n".join(text_blocks)

    if len(clean_text) < 200:
        print(f"{number:02}. Skipped: could not extract enough content from {url}")
        continue

    try:
        title, excerpt, blocks, editorial_metadata = rewrite_article(
            title,
            url,
            blocks,
            CATEGORY_SLUG,
            published_at,
        )
    except Exception as error:
        print(f"{number:02}. Skipped: Gemini rewrite failed for {url}: {error}")
        continue

    uploaded_blocks = []
    image_count = 0
    seen_hosted_images = set()
    for block in blocks:
        if block["type"] == "pending_video":
            uploaded_blocks.append(
                {
                    "type": "video",
                    "provider": block["provider"],
                    "url": block["url"],
                    "title": block["title"],
                    "sourceUrl": block["sourceUrl"],
                }
            )
            continue
        if block["type"] != "pending_image":
            uploaded_blocks.append(block)
            continue
        if image_count >= 20:
            continue
        try:
            hosted_url = upload_image(block["source"], url)
        except Exception as error:
            print(f"    Image skipped: {error}")
            continue
        if hosted_url:
            if hosted_url in seen_hosted_images:
                print(f"    Duplicate image skipped: {block['source']}")
                continue
            seen_hosted_images.add(hosted_url)
            uploaded_blocks.append(
                {
                    "type": "image",
                    "url": hosted_url,
                    "alt": block["alt"],
                    "caption": block["caption"],
                    "sourceUrl": urllib.parse.urljoin(url, block["source"]),
                }
            )
            image_count += 1

    uploaded_blocks.append(
        {
            "type": "paragraph",
            "text": f"Original source: {url}",
        }
    )

    article = {
        "externalSourceID": url,
        "title": title[:180],
        "excerpt": excerpt,
        "blocks": uploaded_blocks[:500],
        "categorySlug": CATEGORY_SLUG,
        **editorial_metadata,
        "publish": True,
    }

    post_request = urllib.request.Request(
        f"{BYTEKORA_URL.rstrip('/')}/api/crawler/posts",
        data=json.dumps(article).encode(),
        headers={
            "Authorization": f"Bearer {INGEST_SECRET}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    with urllib.request.urlopen(post_request, timeout=30) as response:
        result = json.loads(response.read())

    print(
        f"{number:02}. {title}\n"
        f"    {url}\n"
        f"    Extracted: {len(clean_text)} characters, {image_count} images\n"
        f"    CMS: {result}"
    )
    time.sleep(1)
