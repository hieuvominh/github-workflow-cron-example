"""Share newly published BYTERMINAL articles to a Facebook Page.

The Page token is read only from the environment. A small local outbox records
unsent links so a later crawler run can retry without publishing the CMS article
again. GitHub Actions restores and saves that outbox between runs.
"""

import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request


DEFAULT_SITE_URL = "https://www.byterminal.com"
DEFAULT_GRAPH_VERSION = "v26.0"
DEFAULT_STATE_FILE = ".facebook-share-state.json"


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


def _post_link(url, message):
    page_id = os.environ["FACEBOOK_PAGE_ID"].strip()
    token = os.environ["FACEBOOK_PAGE_ACCESS_TOKEN"].strip()
    version = os.environ.get("FACEBOOK_GRAPH_VERSION", DEFAULT_GRAPH_VERSION).strip()
    if not re.fullmatch(r"\d+", page_id) or not re.fullmatch(r"v\d+\.\d+", version):
        raise ValueError("Invalid Facebook Page ID or Graph API version")
    data = urllib.parse.urlencode({"message": message, "link": url}).encode("utf-8")
    request = urllib.request.Request(
        f"https://graph.facebook.com/{version}/{page_id}/feed",
        data=data,
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
    post_id = str(result.get("id") or "").strip()
    if not post_id:
        raise RuntimeError("Facebook API response has no post ID")
    return post_id


def flush_pending_shares():
    """Retry saved links; a Facebook failure never invalidates a CMS publish."""
    if not configured():
        return
    try:
        state = _load_state()
    except (OSError, ValueError) as error:
        print(f"    Facebook outbox unavailable: {error}")
        return
    for url, item in list(state["pending"].items()):
        try:
            post_id = _post_link(url, item["message"])
        except Exception as error:
            print(f"    Facebook share pending for {url}: {error}")
            continue
        state["posted"][url] = post_id
        del state["pending"][url]
        try:
            _save_state(state)
        except OSError as error:
            print(f"    Facebook share state could not be saved: {error}")
        print(f"    Facebook shared: {url} (post {post_id})")


def enqueue_published_article(cms_result, article):
    """Queue a CMS article only after the CMS confirms it is published."""
    if not configured() or cms_result.get("ok") is not True or cms_result.get("status") != "published":
        return
    try:
        url = public_article_url(cms_result, article)
        state = _load_state()
        if url not in state["posted"] and url not in state["pending"]:
            title = str(article.get("title") or "").strip()
            excerpt = str(article.get("excerpt") or "").strip()
            state["pending"][url] = {"message": "\n\n".join(part for part in (title, excerpt) if part)}
            _save_state(state)
        flush_pending_shares()
    except (OSError, ValueError) as error:
        print(f"    Facebook share could not be queued: {error}")
