"""Rewrite and optionally publish an article described by a local text file.

Run with: python .github/scripts/local_post.py draft.txt
Use --dry-run to inspect the Gemini result without uploading or publishing.
"""

import argparse
import base64
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request


REPO_ROOT = Path(__file__).resolve().parents[2]
MAX_IMAGE_BYTES = 30_000_000
MAX_IMAGE_UPLOAD_BYTES = 15_000_000
MAX_IMAGES = 20
VALID_CATEGORIES = {
    "ai",
    "phones",
    "computing",
    "gadgets",
    "gaming",
    "guides",
    "reviews",
    "news",
}
VALID_CONTENT_TYPES = {"news", "guide", "review", "opinion"}
MARKDOWN_IMAGE = re.compile(
    r'^!\[(?P<alt>[^\]]*)\]\((?P<url>https?://\S+?)(?:\s+"(?P<caption>[^"]*)")?\)$'
)


@dataclass
class Draft:
    title: str
    source_url: str
    category: str
    content_type: str
    blocks: list


def load_local_env():
    """Load private local credentials without overriding shell environment."""
    env_path = REPO_ROOT / ".secrets" / "local-post.env"
    if not env_path.exists():
        return
    for line_number, line in enumerate(
        env_path.read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            raise ValueError(f"Invalid setting in {env_path.name} on line {line_number}")
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def checked_url(value, field_name):
    parsed = urllib.parse.urlsplit(value.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(f"{field_name} must be a complete http(s) URL: {value!r}")
    return value.strip()


def parse_image_spec(value, is_hero=False):
    parts = [part.strip() for part in value.split("|", 2)]
    source_url = checked_url(parts[0], "Image URL")
    alt = parts[1] if len(parts) > 1 else ""
    caption = parts[2] if len(parts) > 2 else ""
    return {
        "type": "pending_image",
        "source": source_url,
        "alt": alt[:500],
        "caption": caption[:1_000],
        "isHero": is_hero,
    }


def parse_body(body):
    blocks = []
    paragraph_lines = []

    def flush_paragraph():
        if paragraph_lines:
            text = " ".join(part.strip() for part in paragraph_lines if part.strip())
            if text:
                blocks.append({"type": "paragraph", "text": text})
            paragraph_lines.clear()

    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line:
            flush_paragraph()
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            blocks.append(
                {
                    "type": "heading",
                    "level": "h2" if len(heading.group(1)) == 1 else "h3",
                    "text": heading.group(2).strip(),
                }
            )
            continue
        markdown_image = MARKDOWN_IMAGE.fullmatch(line)
        if markdown_image:
            flush_paragraph()
            blocks.append(
                {
                    "type": "pending_image",
                    "source": checked_url(markdown_image.group("url"), "Image URL"),
                    "alt": markdown_image.group("alt")[:500],
                    "caption": (markdown_image.group("caption") or "")[:1_000],
                    "isHero": False,
                }
            )
            continue
        body_image = re.fullmatch(r"Image:\s*(https?://\S+(?:\s*\|.*)?)", line, re.I)
        if body_image:
            flush_paragraph()
            blocks.append(parse_image_spec(body_image.group(1)))
            continue
        paragraph_lines.append(line)
    flush_paragraph()
    return blocks


def parse_draft(text):
    """Parse a simple header plus Markdown-like plain-text body."""
    lines = text.lstrip("\ufeff").splitlines()
    separator = next(
        (index for index, line in enumerate(lines) if line.strip() == "---"),
        None,
    )
    if separator is None:
        raise ValueError("Draft must have a header section followed by a line containing ---")

    fields = {}
    header_images = []
    for line_number, line in enumerate(lines[:separator], 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise ValueError(f"Invalid header on line {line_number}: use Name: value")
        key, value = line.split(":", 1)
        key = key.strip().casefold().replace("_", " ")
        value = value.strip()
        if key in {"image", "image url", "hero image"}:
            if not value:
                raise ValueError(f"Image URL is empty on line {line_number}")
            header_images.append(parse_image_spec(value, is_hero=True))
        elif key in {"title", "source url", "url", "category", "content type"}:
            if key in fields:
                raise ValueError(f"Duplicate header field: {key}")
            fields[key] = value
        else:
            raise ValueError(f"Unknown header field on line {line_number}: {key}")

    title = fields.get("title", "").strip()
    source_url = checked_url(fields.get("source url", fields.get("url", "")), "Source URL")
    category = fields.get("category", "").strip().casefold()
    content_type = fields.get("content type", "").strip().casefold()
    if not title:
        raise ValueError("Draft is missing Title:")
    if category not in VALID_CATEGORIES:
        raise ValueError(
            "Category must be one of: " + ", ".join(sorted(VALID_CATEGORIES))
        )
    if content_type and content_type not in VALID_CONTENT_TYPES:
        raise ValueError(
            "Content type must be one of: " + ", ".join(sorted(VALID_CONTENT_TYPES))
        )
    if category == "reviews":
        content_type = "review"
    if content_type == "review" and category != "reviews":
        raise ValueError("Content type 'review' requires Category: reviews")

    body_blocks = parse_body("\n".join(lines[separator + 1 :]))
    text_blocks = [block for block in body_blocks if block["type"] in ("paragraph", "heading")]
    text_length = sum(len(block["text"]) for block in text_blocks)
    if text_length < 200:
        raise ValueError(
            f"Article content is too short ({text_length} characters; minimum is 200)"
        )
    if not any(block["type"] == "paragraph" for block in text_blocks):
        raise ValueError("Draft needs at least one paragraph of article content")

    return Draft(
        title=title[:500],
        source_url=source_url,
        category=category,
        content_type=content_type,
        blocks=header_images + body_blocks,
    )


def ssl_context():
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def request_url(url, *, method="GET", headers=None, data=None, timeout=30):
    request = urllib.request.Request(
        url,
        data=data,
        headers=headers or {},
        method=method,
    )
    return urllib.request.urlopen(request, timeout=timeout, context=ssl_context())


def check_source_url(source_url):
    try:
        request = urllib.request.Request(
            source_url,
            headers={"User-Agent": "Mozilla/5.0 BYTERMINAL local article checker"},
            method="HEAD",
        )
        with urllib.request.urlopen(request, timeout=15, context=ssl_context()) as response:
            print(f"Source URL: HTTP {response.status}")
    except Exception as error:
        # The user supplies the article text, so an anti-bot response should
        # not block an otherwise valid draft or trigger a second source scrape.
        print(f"Warning: source URL could not be verified ({error}); using supplied text")


def validate_and_optimize_images(blocks):
    from PIL import Image, UnidentifiedImageError
    from io import BytesIO

    image_cache = {}
    pending_images = [block for block in blocks if block.get("type") == "pending_image"]
    if len(pending_images) > MAX_IMAGES:
        raise ValueError(f"Draft contains {len(pending_images)} images; maximum is {MAX_IMAGES}")
    for block in pending_images:
        source = block["source"]
        if source in image_cache:
            continue
        request = urllib.request.Request(
            source,
            headers={"User-Agent": "BYTERMINALBot/0.1"},
        )
        with urllib.request.urlopen(request, timeout=30, context=ssl_context()) as response:
            content_type = response.headers.get_content_type().lower()
            if not content_type.startswith("image/"):
                raise ValueError(f"Image URL returned {content_type}, not an image: {source}")
            data = response.read(MAX_IMAGE_BYTES + 1)
        if len(data) > MAX_IMAGE_BYTES:
            raise ValueError(f"Image is larger than {MAX_IMAGE_BYTES} bytes: {source}")
        try:
            image = Image.open(BytesIO(data))
            image.verify()
            image = Image.open(BytesIO(data))
            has_alpha = image.mode in ("RGBA", "LA") or (
                image.mode == "P" and "transparency" in image.info
            )
            image_mode = "RGBA" if has_alpha else "RGB"
            if image.width > 1600 or image.height > 1600:
                image.thumbnail((1600, 1600), Image.Resampling.LANCZOS)
                output = BytesIO()
                image.convert(image_mode).save(output, format="WEBP", quality=84, method=6)
                optimized, extension = output.getvalue(), "webp"
            elif len(data) > MAX_IMAGE_UPLOAD_BYTES:
                output = BytesIO()
                image.convert(image_mode).save(output, format="WEBP", quality=84, method=6)
                optimized, extension = output.getvalue(), "webp"
            else:
                extension = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif"}.get(image.format)
                if not extension:
                    output = BytesIO()
                    image.convert(image_mode).save(output, format="WEBP", quality=84, method=6)
                    optimized, extension = output.getvalue(), "webp"
                else:
                    optimized = data
            if len(optimized) > MAX_IMAGE_UPLOAD_BYTES:
                raise ValueError(f"Image is too large after optimization: {source}")
        except (Image.DecompressionBombError, UnidentifiedImageError, OSError) as error:
            raise ValueError(f"Image cannot be decoded safely: {source}: {error}") from error
        image_cache[source] = (optimized, extension)
        print(f"Image OK: {source} ({image.width}×{image.height}, {len(optimized)} bytes)")
    return image_cache


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"Missing {name}; configure it in .secrets/local-post.env")
    return value


def check_duplicate(bytekora_url, ingest_secret, source_url):
    query = urllib.parse.urlencode({"externalSourceID": source_url})
    url = f"{bytekora_url.rstrip('/')}/api/crawler/posts?{query}"
    try:
        with request_url(url, headers={"Authorization": f"Bearer {ingest_secret}"}) as response:
            result = json.loads(response.read())
            if result.get("exists"):
                raise ValueError(f"Bytekora already has this source URL: {source_url}")
            print("Duplicate check: not found")
    except urllib.error.HTTPError as error:
        if error.code in (404, 405):
            print("Warning: duplicate preflight is unavailable; CMS POST will decide")
            return
        raise


def upload_image_to_media_repo(source_url, image_data, extension, media_repo, media_token, branch):
    digest = hashlib.sha256(image_data).hexdigest()
    media_path = f"articles/{digest[:2]}/{digest}.{extension}"
    encoded_path = urllib.parse.quote(media_path, safe="/")
    api_url = f"https://api.github.com/repos/{media_repo}/contents/{encoded_path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {media_token}",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "BYTERMINALBot/0.1",
    }
    try:
        with request_url(
            f"{api_url}?ref={urllib.parse.quote(branch)}", headers=headers
        ):
            pass
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        payload = {
            "message": f"Add manually prepared article image {digest[:12]}",
            "content": base64.b64encode(image_data).decode("ascii"),
            "branch": branch,
        }
        with request_url(
            api_url,
            method="PUT",
            data=json.dumps(payload).encode("utf-8"),
            headers={**headers, "Content-Type": "application/json"},
            timeout=60,
        ):
            pass
    return f"https://raw.githubusercontent.com/{media_repo}/{urllib.parse.quote(branch, safe='')}/{encoded_path}"


def publish_draft(draft, rewritten, image_cache, bytekora_url, ingest_secret, media_repo, media_token, branch):
    title, excerpt, blocks, metadata = rewritten
    uploaded_blocks = []
    seen_images = set()
    image_count = 0
    for block in blocks:
        if block.get("type") != "pending_image":
            uploaded_blocks.append(block)
            continue
        if image_count >= MAX_IMAGES:
            print(f"Image limit reached ({MAX_IMAGES}); remaining images omitted")
            continue
        source = block["source"]
        optimized, extension = image_cache[source]
        hosted_url = upload_image_to_media_repo(
            source, optimized, extension, media_repo, media_token, branch
        )
        if hosted_url in seen_images:
            continue
        seen_images.add(hosted_url)
        uploaded_blocks.append(
            {
                "type": "image",
                "url": hosted_url,
                "alt": block.get("alt", ""),
                "caption": block.get("caption", ""),
                "sourceUrl": source,
            }
        )
        image_count += 1

    article = {
        "sourceUrl": draft.source_url,
        "title": title,
        "excerpt": excerpt,
        "blocks": uploaded_blocks[:500],
        "categorySlug": draft.category,
        **metadata,
        "publish": True,
    }
    url = f"{bytekora_url.rstrip('/')}/api/crawler/posts"
    try:
        with request_url(
            url,
            method="POST",
            data=json.dumps(article, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {ingest_secret}",
                "Content-Type": "application/json",
            },
            timeout=30,
        ) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"CMS rejected article (HTTP {error.code}): {response_body[:4000]}"
        ) from error
    return result, article


def configure_rewriter(draft):
    os.environ["CATEGORY_SLUG"] = draft.category
    os.environ.pop("CONTENT_TYPE_LOCK", None)
    os.environ["DYNAMIC_PRIMARY_CATEGORY"] = "false"
    if draft.content_type:
        os.environ["CONTENT_TYPE_LOCK"] = draft.content_type
    if draft.category == "reviews" or draft.content_type == "review":
        os.environ["CONTENT_TYPE_LOCK"] = "review"
        os.environ["DYNAMIC_PRIMARY_CATEGORY"] = "true"
    if not os.environ.get("GEMINI_API_KEYS") and not os.environ.get("GEMINI_API_KEYS_FILE"):
        os.environ["GEMINI_API_KEYS_FILE"] = "api_keys.txt"


def preview(rewritten):
    title, excerpt, blocks, metadata = rewritten
    print("\nGemini draft preview")
    print(f"Title: {title}")
    print(f"Excerpt: {excerpt}")
    print("SEO keywords: " + ", ".join(metadata.get("seoKeywords", [])))
    print(f"Content blocks: {len(blocks)}")
    print(f"Images retained: {sum(block.get('type') == 'pending_image' for block in blocks)}")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Rewrite and publish a local article draft")
    parser.add_argument("draft", help="Path to the .txt draft file")
    parser.add_argument("--dry-run", action="store_true", help="Run Gemini and preflight checks, but do not upload or publish")
    parser.add_argument("--yes", action="store_true", help="Publish without the confirmation prompt")
    args = parser.parse_args(argv)

    os.chdir(REPO_ROOT)
    load_local_env()
    try:
        draft_path = Path(args.draft).expanduser().resolve()
        draft = parse_draft(draft_path.read_text(encoding="utf-8-sig"))
        print(f"Draft: {draft_path.name}")
        print(f"Input title: {draft.title}")
        print(f"Source URL: {draft.source_url}")
        print(f"Category: {draft.category}; content type: {draft.content_type or 'Gemini chooses'}")
        check_source_url(draft.source_url)

        bytekora_url = required_env("BYTEKORA_URL")
        ingest_secret = required_env("BYTEKORA_INGEST_SECRET")
        media_repo = required_env("MEDIA_REPO")
        media_branch = os.environ.get("MEDIA_BRANCH", "main").strip() or "main"
        check_duplicate(bytekora_url, ingest_secret, draft.source_url)

        # Skip duplicate drafts before downloading their images or using Gemini.
        image_cache = validate_and_optimize_images(draft.blocks)
        media_token = required_env("MEDIA_TOKEN") if image_cache and not args.dry_run else (
            os.environ.get("MEDIA_TOKEN", "") if image_cache else ""
        )

        if not args.dry_run and not args.yes and not sys.stdin.isatty():
            raise ValueError("Use --yes to publish non-interactively, or --dry-run to preview")

        configure_rewriter(draft)
        from gemini_rewriter import rewrite_article

        rewritten = rewrite_article(
            draft.title,
            draft.source_url,
            draft.blocks,
            draft.category,
        )
        preview(rewritten)
        if args.dry_run:
            print("Dry run complete: no image uploads or CMS post were made.")
            return 0
        if not args.yes:
            answer = input("Upload images and publish this article to Bytekora? [y/N] ").strip().casefold()
            if answer not in {"y", "yes"}:
                print("Cancelled; no images were uploaded and no article was posted.")
                return 0
        result, article = publish_draft(
            draft,
            rewritten,
            image_cache,
            bytekora_url,
            ingest_secret,
            media_repo,
            media_token,
            media_branch,
        )
        print(f"Published: {article['title']}")
        print(f"CMS response: {json.dumps(result, ensure_ascii=False)}")
        return 0
    except Exception as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
