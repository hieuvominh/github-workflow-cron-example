"""Extraction must keep article copy and drop everything around it.

Run with: python .github/scripts/test_article_scope.py

The fixtures are real pages with <script>, <style>, <svg> and <template>
stripped out, which keeps them committable without changing the DOM the
extractor sees. They cover the three ways non-content reaches the rewriter:
around the body container, inside it, and -- where there is no container and no
usable class name -- by wording alone.

Around the body: <article> also wraps the author bio slice and the XenForo
comment list. trafilatura keeps both, and its include_comments=False switch
does not help -- it only recognises its own COMMENTS_XPATH, and
ul.xenforo-comments-list is not in it, so the replies arrive inside <main> as a
<list> and reach the rewriter as article paragraphs.

Inside the body: #article-body itself holds an injected newsletter form and
inline "read more" cards. Tom's Guide closes with a "More from Tom's Guide"
rail whose only signal is its heading, and TechRadar with a classless <ul>
naming its reviews guarantee, identified only by the policy link inside it.

No container at all: The Verge has no #article-body and ships build-hashed
class names, so its score badge and "Follow topics and authors" block are
recognised by their wording and bounded by a character cap.
"""

import os
import pathlib
import sys
import types

os.environ.setdefault("CATEGORY_SLUG", "computing")
os.environ.setdefault("FEED_URLS", "https://example.invalid/feed")
os.environ.setdefault("BYTEKORA_URL", "https://example.invalid")
os.environ.setdefault("INGEST_SECRET", "x")
os.environ.setdefault("MEDIA_REPO", "owner/repo")
os.environ.setdefault("MEDIA_TOKEN", "x")

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.modules.setdefault("gemini_rewriter", types.ModuleType("gemini_rewriter"))
sys.modules["gemini_rewriter"].rewrite_article = lambda *a, **k: None

SOURCE = (SCRIPTS / "crawl.py").read_text(encoding="utf-8")
CRAWL = {"__name__": "crawl_under_test"}
exec(compile(SOURCE[: SOURCE.index("articles = collect_articles()")], "crawl.py", "exec"), CRAWL)

CASES = (
    {
        "fixture": "tomshardware-article.html",
        "url": (
            "https://www.tomshardware.com/raspberry-pi/raspberry-pi-locks-boards-to-"
            "factory-ram-capacities-in-firmware-engineer-tells-diy-modders-dont-waste-"
            "your-time-trying-repairs-or-upgrades-company-cites-shady-reseller-scams"
        ),
        "keep": {
            "opening paragraph": "Raspberry Pi users are effectively being blocked from swapping",
            "engineer quote paragraph": "PhilE explains the problem that faced Raspberry Pi",
            "closing paragraph": "Naturally, the Pi community",
        },
        "drop": {
            "newsletter signup copy": "straight to your inbox",
            "author biography": "Mark Tyson is a news editor",
            "reader comment (bootloader)": "closed-source proprietary firmware bootloader",
            "reader comment (shady reseller)": "doesnt THIS just defeat the entire purpose",
            "reader comment (AT&T)": "same argument that AT&T makes",
        },
    },
    {
        # Tom's Guide closes the body container itself with a link rail, so no
        # amount of scoping reaches it -- only the heading names the section.
        "fixture": "tomsguide-article.html",
        "url": (
            "https://www.tomsguide.com/phones/iphones/iphone-duo-vs-iphone-18-pro-"
            "5-biggest-things-you-dont-get-on-apples-foldable"
        ),
        "keep": {
            "opening paragraph": "Apple's take on a foldable phone is finally here",
            "section heading": "Action button",
            "closing paragraph": "Are there any features the Duo lacks",
        },
        "drop": {
            "More from rail heading": "More from Tom's Guide",
            "More from rail link": "I interviewed Apple CEO John Ternus",
            "More from rail link (watch)": "always-listening",
        },
    },
    {
        # TechRadar signs a review off with a bare <ul> that has no class and
        # follows no heading; only its link to the testing policy names it.
        "fixture": "techradar-review.html",
        "url": "https://www.techradar.com/televisions/soundbars/sonos-beam-ultra-review",
        "keep": {
            "opening paragraph": "The Sonos Beam Ultra doesn",
            "how I tested section": "How I tested the Sonos Beam Ultra",
            "closing test paragraph": "I also compared the Sonos Beam Ultra directly",
        },
        "drop": {
            "reviews guarantee link": "reviews guarantee",
            "first reviewed stamp": "First reviewed",
            "newsletter signup copy": "Sign up for breaking news",
            "author biography": "Harry is a Senior Reviews Writer",
            "comment system notice": "confirm your public display name",
        },
    },
    {
        # The Verge has no #article-body and ships build-hashed class names, so
        # only the visible wording identifies its widgets.
        "fixture": "theverge-review.html",
        "url": "https://www.theverge.com/tech/998566/beats-360-headphones-review",
        "keep": {
            "opening paragraph": "Everyone has seen someone at the gym",
            "verdict paragraph": "For those who cannot abide earbuds",
            "pros list item": "Beats’ first IP-rated headphones",
        },
        "drop": {
            "score badge": "Verge Score",
            "follow topics widget": "Follow topics and authors",
            "follow topics copy": "personalized homepage feed",
        },
    },
)


def check(case):
    downloaded = (SCRIPTS / "fixtures" / case["fixture"]).read_text(encoding="utf-8")
    article_url = case["url"]
    prepared = CRAWL["remove_source_blocked_content"](downloaded, article_url)
    blocks = CRAWL["extract_blocks"](prepared, article_url)
    text = "\n\n".join(
        block["text"]
        for block in blocks
        if block.get("type") in ("paragraph", "heading")
    )

    print(f"{case['fixture']}: {len(blocks)} block(s) extracted")
    for index, block in enumerate(blocks):
        kind = block.get("type")
        detail = block.get("text") or block.get("source") or block.get("url") or ""
        print(f"  [{index:02}] {kind:13} {detail[:88]}")
    print()

    return [
        f"MISSING body copy: {label}"
        for label, needle in case["keep"].items()
        if needle not in text
    ] + [
        f"LEAKED non-content: {label}"
        for label, needle in case["drop"].items()
        if needle in text
    ]


def main():
    failures = []
    for case in CASES:
        for failure in check(case):
            failures.append(f"{case['fixture']}: {failure}")

    if failures:
        for failure in failures:
            print(f"FAIL: {failure}")
        return 1
    print("PASS: only article copy survived extraction")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
