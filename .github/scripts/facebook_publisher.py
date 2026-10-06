"""Publish article hero photos to a Facebook Page, then comment with the link.

The Page token is read only from the environment. A small local outbox records
unsent links so a later crawler run can retry without publishing the CMS article
again. GitHub Actions restores and saves that outbox between runs.
"""

import json
from functools import lru_cache
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_SITE_URL = "https://www.byterminal.com"
DEFAULT_GRAPH_VERSION = "v26.0"
DEFAULT_STATE_FILE = ".facebook-share-state.json"
_attempted_photos = set()
_attempted_comments = set()


def configured():
    return bool(
        os.environ.get("FACEBOOK_PAGE_ID", "").strip()
        and os.environ.get("FACEBOOK_PAGE_ACCESS_TOKEN", "").strip()
    )


def public_article_url(cms_result, article):
    """Use a trusted CMS URL, or build the public route from its saved slug."""
    site_url = os.environ.get("SITE_PUBLIC_URL", DEFAULT_SITE_URL).strip().rstrip("/")
    site = urllib.parse.urlsplit(site_url)
    if site.scheme != "https" or not site.hostname or site.path not in ("", "/"):
        raise ValueError("SITE_PUBLIC_URL must be an HTTPS site origin")

    for field in ("canonicalUrl", "publicUrl", "url"):
        candidate = str(cms_result.get(field) or "").strip()
        if not candidate:
            continue
        parsed = urllib.parse.urlsplit(candidate)
        if parsed.scheme == "https" and parsed.hostname == site.hostname:
            return candidate

    slug = str(cms_result.get("slug") or "").strip()
    category = str(article.get("categorySlug") or "").strip()
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", slug):
        raise ValueError("CMS response is missing a valid article slug")
    if not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", category):
        raise ValueError("Article is missing a valid public category")
    return f"{site_url}/{category}/{slug}"


def _state_path():
    return Path(os.environ.get("FACEBOOK_SHARE_STATE_FILE", DEFAULT_STATE_FILE))


def _load_state():
    path = _state_path()
    if not path.exists():
        return {"pending": {}, "posted": {}}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("pending"), dict) or not isinstance(data.get("posted"), dict):
        raise ValueError(f"Invalid Facebook share state: {path}")
    return data


def _save_state(state):
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def _facebook_error_detail(error, token):
    """Show useful Graph error fields without dumping its raw response or token."""
    try:
        payload = json.loads(error.read(16_384).decode("utf-8", errors="replace"))
    except (OSError, ValueError):
        return f"Facebook API HTTP {error.code}"
    details = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(details, dict):
        return f"Facebook API HTTP {error.code}"

    parts = [f"Facebook API HTTP {error.code}"]
    for field in ("type", "code", "error_subcode", "message", "error_user_msg", "fbtrace_id"):
        value = details.get(field)
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            continue
        value = str(value)
        if token:
            value = value.replace(token, "[REDACTED]")
            value = value.replace(urllib.parse.quote(token, safe=""), "[REDACTED]")
        value = re.sub(r"(?i)(access[_-]?token\s*[=:]\s*)\S+", r"\1[REDACTED]", value)
        value = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", value)
        value = re.sub(r"\bEAA[A-Za-z0-9_-]{12,}\b", "[REDACTED]", value)
        value = " ".join(value.split())[:500]
        if value:
            parts.append(f"{field}={value}")
    return "; ".join(parts)


def _graph_settings():
    page_id = os.environ["FACEBOOK_PAGE_ID"].strip()
    version = os.environ.get("FACEBOOK_GRAPH_VERSION", DEFAULT_GRAPH_VERSION).strip()
    if not re.fullmatch(r"\d+", page_id) or not re.fullmatch(r"v\d+\.\d+", version):
        raise ValueError("Invalid Facebook Page ID or Graph API version")
    return page_id, version


def _graph_get(version, path, token):
    request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(_facebook_error_detail(error, token)) from error
    if not isinstance(result, dict):
        raise RuntimeError("Facebook API returned an invalid token lookup response")
    return result


@lru_cache(maxsize=4)
def _resolve_page_token(page_id, version, configured_token):
    """Use a Page token, or exchange an assigned User/System User token for one."""
    try:
        identity = _graph_get(version, "me?fields=id,name", configured_token)
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError(f"Could not identify Facebook token: {error}") from error
    if str(identity.get("id") or "") == page_id:
        return configured_token

    try:
        page = _graph_get(version, f"{page_id}?fields=id,name,access_token", configured_token)
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError(
            f"Secret is not a token for Page {page_id}, and Meta could not provide its Page token: {error}"
        ) from error
    candidate = page.get("access_token")
    if str(page.get("id") or "") != page_id or not isinstance(candidate, str) or not candidate:
        raise RuntimeError(
            f"Secret is not a token for Page {page_id}, and Meta did not return its Page token"
        )
    try:
        page_identity = _graph_get(version, "me?fields=id,name", candidate)
    except (OSError, ValueError, RuntimeError) as error:
        raise RuntimeError(f"Meta returned a Page token that could not be verified: {error}") from error
    if str(page_identity.get("id") or "") != page_id:
        raise RuntimeError("Meta returned a token for a different Page; share remains pending")
    print(f"    Facebook resolved Page token for Page {page_id}")
    return candidate


def _graph_post(path, fields, token, version):
    request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{path}",
        data=urllib.parse.urlencode(fields).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            result = json.loads(response.read())
    except urllib.error.HTTPError as error:
        raise RuntimeError(_facebook_error_detail(error, token)) from error
    if not isinstance(result, dict):
        raise RuntimeError("Facebook API returned an invalid publish response")
    return result


def _post_photo(image_url, caption):
    page_id, version = _graph_settings()
    configured_token = os.environ["FACEBOOK_PAGE_ACCESS_TOKEN"].strip()
    token = _resolve_page_token(page_id, version, configured_token)
    result = _graph_post(
        f"{page_id}/photos",
        {"url": image_url, "caption": caption, "published": "true"},
        token,
        version,
    )
    photo_id = str(result.get("id") or "").strip()
    if not re.fullmatch(r"\d+", photo_id):
        raise RuntimeError("Facebook photo response has no valid photo ID")
    post_id = str(result.get("post_id") or "").strip()
    if post_id and not re.fullmatch(r"\d+_\d+", post_id):
        post_id = ""  # The photo was posted; look up its feed post before commenting.
    return {"photo_id": photo_id, "post_id": post_id, "comment_id": ""}


def _post_comment(post_id, article_url):
    page_id, version = _graph_settings()
    configured_token = os.environ["FACEBOOK_PAGE_ACCESS_TOKEN"].strip()
    token = _resolve_page_token(page_id, version, configured_token)
    result = _graph_post(
        f"{post_id}/comments",
        {"message": f"Read the full article: {article_url}"},
        token,
        version,
    )
    comment_id = str(result.get("id") or "").strip()
    if not comment_id:
        raise RuntimeError("Facebook comment response has no comment ID")
    return comment_id


def _photo_post_id(photo_id):
    page_id, version = _graph_settings()
    configured_token = os.environ["FACEBOOK_PAGE_ACCESS_TOKEN"].strip()
    token = _resolve_page_token(page_id, version, configured_token)
    result = _graph_get(version, f"{photo_id}?fields=post_id", token)
    post_id = str(result.get("post_id") or "").strip()
    if not re.fullmatch(r"\d+_\d+", post_id):
        raise RuntimeError("Facebook photo is published but its post ID is not available yet")
    return post_id


def flush_pending_shares():
    """Publish each photo once, then independently retry its first comment."""
    if not configured():
        return
    try:
        state = _load_state()
    except (OSError, ValueError) as error:
        print(f"    Facebook outbox unavailable: {error}")
        return
    for url, item in list(state["pending"].items()):
        if url in _attempted_photos:
            continue
        if not isinstance(item, dict) or not item.get("image_url"):
            print(f"    Facebook share pending for {url}: older queue entry has no hero image; not posting a link-only story")
            continue
        _attempted_photos.add(url)
        try:
            photo = _post_photo(item["image_url"], item["caption"])
        except Exception as error:
            print(f"    Facebook photo pending for {url}: {error}")
            continue
        state["posted"][url] = photo
        del state["pending"][url]
        try:
            _save_state(state)
        except OSError as error:
            print(f"    Facebook photo posted but state could not be saved: {error}; check Page before retrying")
            return
        print(f"    Facebook photo shared: {url} (photo {photo['photo_id']})")

    for url, photo in list(state["posted"].items()):
        if not isinstance(photo, dict) or photo.get("comment_id") or url in _attempted_comments:
            continue  # Legacy link posts are already complete; never re-publish them.
        _attempted_comments.add(url)
        try:
            post_id = photo.get("post_id") or _photo_post_id(photo["photo_id"])
            if not photo.get("post_id"):
                photo["post_id"] = post_id
                _save_state(state)
            comment_id = _post_comment(post_id, url)
        except Exception as error:
            print(f"    Facebook comment pending for {url}: {error}")
            continue
        photo["comment_id"] = comment_id
        try:
            _save_state(state)
        except OSError as error:
            print(f"    Facebook comment posted but state could not be saved: {error}; check Page before retrying")
            return
        print(f"    Facebook shared with article link in comment: {url} (post {post_id})")


def enqueue_published_article(cms_result, article):
    """Queue a CMS article only after the CMS confirms it is published."""
    if not configured() or cms_result.get("ok") is not True or cms_result.get("status") != "published":
        return
    try:
        url = public_article_url(cms_result, article)
        image_url = str(article.get("heroImageUrl") or "").strip()
        parsed_image = urllib.parse.urlsplit(image_url)
        if parsed_image.scheme != "https" or parsed_image.hostname != "raw.githubusercontent.com":
            print(f"    Facebook skipped for {url}: no public GitHub hero image")
            return
        state = _load_state()
        if url not in state["posted"] and url not in state["pending"]:
            title = str(article.get("title") or "").strip()
            description = str(article.get("seoDescription") or article.get("excerpt") or "").strip()
            state["pending"][url] = {
                "image_url": image_url,
                "caption": "\n\n".join(part for part in (title, description) if part),
            }
            _save_state(state)
        flush_pending_shares()
    except (OSError, ValueError) as error:
        print(f"    Facebook share could not be queued: {error}")
