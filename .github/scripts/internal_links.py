"""Small, reusable Computing article candidates for internal links."""

import json
import os
import re
import time
import urllib.request
from pathlib import Path


SNAPSHOT_MAX_AGE_SECONDS = 24 * 60 * 60
TOKEN_RE = re.compile(r"[a-z0-9]{3,}")
STOPWORDS = {
    "about", "after", "from", "have", "into", "more", "that", "their",
    "them", "this", "with", "your", "best", "review", "news", "guide",
    "computing", "technology", "latest", "will", "what", "when", "where",
}
PATH_RE = re.compile(r"^/computing/[a-z0-9-]+$")


def _valid_candidates(value):
    if not isinstance(value, list) or len(value) > 50:
        return False
    return all(
        isinstance(item, dict)
        and isinstance(item.get("id"), int)
        and isinstance(item.get("title"), str)
        and bool(item["title"].strip())
        and isinstance(item.get("path"), str)
        and bool(PATH_RE.fullmatch(item["path"]))
        and isinstance(item.get("keywords"), list)
        for item in value
    )


def load_computing_snapshot(path, cms_url, secret, now=None):
    """Fetch at most 50 thin CMS records once per day; never use stale targets."""
    now = time.time() if now is None else now
    snapshot_path = Path(path)
    if snapshot_path.exists():
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
            age = now - float(snapshot["fetchedAt"])
            if 0 <= age < SNAPSHOT_MAX_AGE_SECONDS and _valid_candidates(snapshot["candidates"]):
                print(f"    Internal link snapshot hit: {len(snapshot['candidates'])} candidates")
                return snapshot["candidates"]
        except (ValueError, TypeError, KeyError, OSError):
            pass

    request = urllib.request.Request(
        f"{cms_url.rstrip('/')}/api/crawler/posts?linkCandidates=computing",
        headers={"Authorization": f"Bearer {secret}"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        data = json.load(response)
    if data.get("category") != "computing" or not _valid_candidates(data.get("candidates")):
        raise RuntimeError("CMS returned invalid Computing link candidates")

    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = snapshot_path.with_suffix(snapshot_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps({"fetchedAt": now, "candidates": data["candidates"]}, ensure_ascii=False),
        encoding="utf-8",
    )
    os.replace(temporary, snapshot_path)
    print(f"    Internal link snapshot refreshed: {len(data['candidates'])} candidates")
    return data["candidates"]


def rank_candidates(candidates, title, text, source_url="", limit=8):
    """Rank a bounded snapshot locally; only the best few reach Gemini."""
    title_tokens = set(TOKEN_RE.findall(title.lower())) - STOPWORDS
    context_tokens = set(TOKEN_RE.findall(text[:4000].lower())) - STOPWORDS

    def score(item):
        candidate_tokens = set(TOKEN_RE.findall(item["title"].lower())) - STOPWORDS
        keyword_tokens = set(
            TOKEN_RE.findall(" ".join(str(value) for value in item["keywords"][:5]).lower())
        ) - STOPWORDS
        return (
            4 * len(candidate_tokens & title_tokens)
            + 2 * len(keyword_tokens & title_tokens)
            + len(candidate_tokens & context_tokens)
            + len(keyword_tokens & context_tokens)
        )

    ranked = sorted(
        (item for item in candidates if item.get("sourceUrl") != source_url),
        key=score,
        reverse=True,
    )
    return [item for item in ranked if score(item) > 0][:limit]
