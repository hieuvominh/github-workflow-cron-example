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
import argparse

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
        # Tom's Guide deal pages mix a Quick Links nav and Hawk affiliate
        # cards into the downloaded page, including inside the article body.
        "fixture": "tomsguide-deals.html",
        "url": "https://www.tomsguide.com/phones/iphones/how-to-save-up-to-usd885-on-the-new-iphone-duo",
        "keep": {
            "article copy": "Apple is offering up to $885 in trade-in credit",
            "editorial pricing context": "The iPhone Duo starts at $1,299",
        },
        "drop": {
            "quick links navigation": "Quick Links",
            "affiliate widget and retailer CTA": "VIEW DEAL",
            "affiliate retailer copy": "Verizon is waiving activation",
            "shop more deals section": "Shop more deals",
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
        # Tech Guide puts author/share modules and related-post rails around
        # an otherwise extractable product review.
        "fixture": "techguide-review.html",
        "url": "https://www.techguide.com.au/reviews/computers-reviews/hp-omnibook-ultra-14-review/",
        "keep": {
            "article copy": "HP OmniBook Ultra is a superb all-rounder laptop",
        },
        "keep_images": {"product image": "HP-OmniBook-review.jpg"},
        "require_hero": True,
        "drop": {
            "author biography": "Stephen is the Tech Guide editor",
            "byline": "By Stephen Fenech",
            "share controls": "Facebook Twitter Pinterest LinkedIn",
            "related posts": "Related Posts",
        },
    },
    {
        # Engadget's og:image uses an l-intro rendition while its <picture>
        # supplies the accessible alt text on the matching upload ID.
        "fixture": "engadget-hero.html",
        "url": "https://www.engadget.com/2267210/meta-muse-ai-agent-smart-glasses/",
        "keep": {
            "article copy": "Meta's Muse AI agent can book travel and send emails",
        },
        "keep_images": {
            "hero alt from responsive picture rendition": "A person wearing Meta AI glasses",
        },
        "require_hero": True,
        "expected_image_count": 1,
        "drop": {
            "share controls": "Share this story",
            "related-story card": "Related coverage",
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
    {
        # CNET detail pages use a WordPress .entry-content body and inject
        # recommendation videos inside it; the author bio remains outside.
        "fixture": "cnet-gaming.html",
        "url": "https://www.cnet.com/tech/gaming/console-security-update/",
        "keep": {
            "opening article copy": "Owners of the original console should install",
            "compatibility detail": "The newer console is not affected",
            "recommended fix": "Installing the firmware update is the recommended fix",
        },
        "keep_images": {"publisher hero": "console-hero.jpg"},
        "require_hero": True,
        "expected_image_count": 1,
        "drop": {
            "injected More from CNET module": "More from CNET",
            "related story": "An unrelated gaming story",
            "author biography": "Author biography that must not become article copy",
        },
    },
)

# Live regression inputs for the end-to-end Gemini cleanup step. These are
# opt-in because source HTML changes and publishers may rate-limit downloads.
LIVE_CLEANUP_CASES = (
    ("CNET gaming detail structure", "https://www.cnet.com/tech/gaming/nintendo-switch-exploit-fixable-with-update-2/"),
    ("Engadget hero image extraction", "https://www.engadget.com/2267210/meta-muse-ai-agent-smart-glasses/"),
    ("TechCrunch hero image extraction", "https://techcrunch.com/2026/09/23/meta-made-a-tamagotchi-like-wearable-for-its-muse-ai-agent/"),
    ("Tech Guide product review", "https://www.techguide.com.au/reviews/computers-reviews/hp-omnibook-ultra-14-review-the-allrounder-laptop-for-work-play-and-entertainment/"),
    ("Tech Guide phone review", "https://www.techguide.com.au/reviews/mobiles-reviews/samsung-galaxy-z-fold8-ultra-review-sets-the-bar-for-flagship-foldable-smartphones/"),
    ("Tech Guide audio review", "https://www.techguide.com.au/reviews/audio-reviews/noble-audio-fokus-apollo-pro-wireless-headphones-review-luxury-design-and-audio-quality/"),
    ("Tom's Guide UI / off-topic deals", "https://www.tomsguide.com/phones/iphones/how-to-save-up-to-usd885-on-the-new-iphone-duo"),
    ("The Verge mixed-topic contamination", "https://www.theverge.com/gadgets/998824/apple-magic-keyboard-touch-interstellar-4k-blu-ray-deal-sale"),
    ("Tom's Hardware image-heavy deal", "https://www.tomshardware.com/pc-components/gpus/get-this-spiffy-stealthy-msi-rtx-5090-for-just-usd4-299-geforce-week-at-walmart-serves-up-a-rare-deal-on-nvidias-fastest-gaming-gpu"),
    ("TechRadar image-heavy review", "https://www.techradar.com/home/robot-vacuums/eufy-omni-e35-review"),
    ("Tom's Hardware image-heavy review", "https://www.tomshardware.com/peripherals/gaming-keyboards/glorious-gmmk-eternal-review"),
    ("Tom's Hardware image-heavy review", "https://www.tomshardware.com/maker-stem/bambu-lab-r1-review"),
    ("Tom's Guide image-heavy comparison", "https://www.tomsguide.com/phones/iphones/i-put-the-iphone-18-pro-max-vs-iphone-17-pro-max-through-a-7-round-face-off-heres-the-winner"),
    ("The Verge image-heavy article", "https://www.theverge.com/tech/998272/peloton-tread-flex-fitness-treadmills"),
    ("CNET image-heavy review", "https://www.cnet.com/home/smart-home/abode-starter-kit-review/"),
)


def check_live_cleanup_inputs():
    """Smoke-test article fetching/extraction only; never calls Gemini or CMS."""
    for label, article_url in LIVE_CLEANUP_CASES:
        request = CRAWL["urllib"].request.Request(
            article_url,
            headers={
                "User-Agent": CRAWL["BROWSER_USER_AGENT"],
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            },
        )
        try:
            with CRAWL["urllib"].request.urlopen(
                request,
                timeout=15,
                context=CRAWL["ARTICLE_SSL_CONTEXT"],
            ) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                downloaded = response.read(CRAWL["ARTICLE_DOWNLOAD_MAX_BYTES"]).decode(
                    charset, errors="replace"
                )
        except Exception as error:
            print(f"UNAVAILABLE (not a cleanup failure): {label}: {error}", flush=True)
            continue
        prepared = CRAWL["remove_source_blocked_content"](downloaded, article_url)
        blocks = CRAWL["extract_blocks"](prepared, article_url)
        text_chars = sum(
            len(block.get("text", ""))
            for block in blocks
            if block.get("type") in ("paragraph", "heading")
        )
        image_count = sum(block.get("type") == "pending_image" for block in blocks)
        print(
            f"INPUT READY: {label}: {text_chars} text chars, "
            f"{image_count} candidate images — {article_url}",
            flush=True,
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
    image_details = "\n".join(
        " ".join(str(block.get(key, "")) for key in ("source", "alt", "caption"))
        for block in blocks
        if block.get("type") == "pending_image"
    )
    has_hero = any(
        block.get("type") == "pending_image" and block.get("isHero") is True
        for block in blocks
    )
    image_count = sum(block.get("type") == "pending_image" for block in blocks)

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
    ] + [
        f"MISSING relevant image: {label}"
        for label, needle in case.get("keep_images", {}).items()
        if needle.lower() not in image_details.lower()
    ] + [
        f"LEAKED irrelevant image: {label}"
        for label, needle in case.get("drop_images", {}).items()
        if needle.lower() in image_details.lower()
    ] + (
        ["MISSING publisher-designated hero image"]
        if case.get("require_hero") and not has_hero
        else []
    ) + (
        [
            "UNEXPECTED image count: "
            f"expected {case['expected_image_count']}, got {image_count}"
        ]
        if "expected_image_count" in case
        and image_count != case["expected_image_count"]
        else []
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--live-cleanup-inputs",
        action="store_true",
        help="Fetch the supplied regression URLs and report extracted text/image inputs (no Gemini or publishing).",
    )
    args = parser.parse_args()
    if args.live_cleanup_inputs:
        check_live_cleanup_inputs()
        return 0

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
