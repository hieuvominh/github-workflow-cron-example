import json
import os
import re
import time
from pathlib import Path

from google import genai
from google.genai import types


PREFERRED_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
_raw_fallback_models = os.environ.get(
    "GEMINI_FALLBACK_MODELS",
    os.environ.get("GEMINI_FALLBACK_MODEL", "gemini-3.1-flash-lite"),
)
FALLBACK_MODELS = tuple(
    dict.fromkeys(model.strip() for model in _raw_fallback_models.split(",") if model.strip())
)
FALLBACK_MODEL = FALLBACK_MODELS[0] if FALLBACK_MODELS else PREFERRED_MODEL
MAX_INPUT_CHARS = int(os.environ.get("GEMINI_MAX_INPUT_CHARS", "60000"))
CONTENT_TYPE_LOCK = os.environ.get("CONTENT_TYPE_LOCK", "").strip()
DYNAMIC_PRIMARY_CATEGORY = (
    os.environ.get("DYNAMIC_PRIMARY_CATEGORY", "false").lower() == "true"
)
_current_key_index = 0
_disabled_key_indexes = set()
_key_index_loaded = False
_model_preferences = {}
_model_preferences_loaded = False

SEO_TITLE_MIN = 20
SEO_TITLE_MAX = 65
EXCERPT_MIN = 80
EXCERPT_MAX = 320
SEO_DESCRIPTION_MIN = 120
SEO_DESCRIPTION_MAX = 165

COMMON_BOILERPLATE_PATTERNS = (
    r"\bsign up for (?:breaking|our|the)\b",
    r"\bwhy you can trust\b",
    r"\bjoin the conversation\b",
    r"\babout the author\b",
    r"\btoday(?:'|’)?s best deals\b",
    r"\bsubscribe (?:to|for)\b",
    r"\bfollow us on google news\b",
    r"^\s*most popular\s*$",
    r"^\s*related stor(?:y|ies)\s*$",
    r"^\s*advertisement\s*$",
)
REVIEW_BOILERPLATE_PATTERNS = (
    r"^\s*(?:[•*\-–—]\s*)?(?:first|originally)\s+reviewed\b",
    r"^\s*(?:[•*\-–—]\s*)?last\s+(?:reviewed|updated)\b",
)

SOURCE_BOILERPLATE_PATTERNS = {
    "techradar.com": (
        r"\bwhy you can trust techradar\b",
        r"\bsign up for breaking news\b",
        r"\btoday(?:'|’)?s best deals\b",
    ),
    "tomshardware.com": (
        r"\bwhy you can trust tom(?:'|’)?s hardware\b",
        r"\bjoin the discussion\b",
    ),
    "theverge.com": (
        r"\bthe verge homepage\b",
        r"^\s*most popular\s*$",
        r"^\s*(?:view |see all )?comments?\s*$",
    ),
    "engadget.com": (
        r"\bsubscribe to engadget\b",
        r"\brecommended stor(?:y|ies)\b",
    ),
}

CATEGORY_DIRECTIONS = {
    "phones": "Cover phones, mobile operating systems, apps and accessories. Lead with practical user impact; preserve models, regions, prices, availability and limitations. Choose news, guide or opinion from the source's real purpose. The primary category is phones.",
    "computing": "Cover PCs, processors, graphics, storage, networking, cloud, data centers and enterprise software. Preserve benchmarks, configurations and caveats and attribute vendor claims. The primary category is computing.",
    "gadgets": "Cover consumer electronics, wearables, smart-home devices and accessories. Focus on real-world use, compatibility, price, availability and trade-offs. The primary category is gadgets.",
    "gaming": "Cover games, consoles, handhelds, PC gaming hardware and industry developments. Preserve platform, release, pricing and performance details. The primary category is gaming.",
    "ai": "Cover artificial intelligence with a neutral, technically literate voice. Explain capabilities, limitations, business impact, privacy and safety. Distinguish claims, research and independent evidence. The primary category is ai.",
    "guides": "Create a practical technology guide with an explicit outcome, prerequisites, ordered actions and caveats. Preserve exact commands and settings only when supported. Never invent a missing step. The content type is guide.",
    "news": "Use a sharp, neutral newsroom voice. Lead with what happened and why it matters. Preserve dates, entities, confirmed figures, attribution and uncertainty.",
    "reviews": "Prepare an attributed external technology review summary. Never imply BYTERMINAL tested the product. Derive a conservative BYTERMINAL editorial score from the source's qualitative evidence, verdict, strengths and weaknesses even when the source has no numerical score. Preserve source-supported pros, cons, advice and specifications.",
}

ALLOWED_PRIMARY_CATEGORIES = ["phones", "ai", "computing", "gadgets", "gaming"]
ALLOWED_SECONDARY_CATEGORIES = [
    "news",
    "phones",
    "computing",
    "gadgets",
    "gaming",
    "guides",
]
ALLOWED_AI_TAGS = [
    "generative-ai",
    "large-language-models",
    "ai-agents",
    "machine-learning",
    "robotics",
    "ai-hardware",
    "data-centers",
    "privacy",
    "security",
    "regulation",
    "startups",
    "funding",
    "openai",
    "anthropic",
    "google",
    "microsoft",
    "meta",
    "nvidia",
    "apple",
    "amazon",
]

RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "seoTitle": {"type": "string"},
        "excerpt": {"type": "string"},
        "seoDescription": {
            "type": "string",
        },
        "seoKeywords": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 10,
        },
        "contentType": {
            "type": "string",
            "enum": ["review", "news", "guide", "opinion"],
        },
        "primaryCategory": {
            "type": "string",
            "enum": ALLOWED_PRIMARY_CATEGORIES,
        },
        "eligibleForPublication": {"type": "boolean"},
        "rejectionReason": {"type": "string"},
        "secondaryCategories": {
            "type": "array",
            "items": {"type": "string", "enum": ALLOWED_SECONDARY_CATEGORIES},
            "maxItems": 2,
        },
        "tags": {
            "type": "array",
            "items": {"type": "string", "enum": ALLOWED_AI_TAGS},
            "maxItems": 6,
        },
        "featured": {"type": "boolean"},
        "trending": {"type": "boolean"},
        "readingTime": {"type": "integer", "minimum": 1, "maximum": 60},
        "reviewSummary": {
            "type": "object",
            "properties": {
                "overallScore": {"type": "number", "minimum": 1, "maximum": 10},
                "verdict": {"type": "string"},
                "pros": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 8,
                },
                "cons": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 8,
                },
                "shouldYouBuy": {"type": "string"},
                "componentScores": {
                    "type": "object",
                    "properties": {
                        "design": {"type": "number", "minimum": 1, "maximum": 10},
                        "display": {"type": "number", "minimum": 1, "maximum": 10},
                        "performance": {"type": "number", "minimum": 1, "maximum": 10},
                        "battery": {"type": "number", "minimum": 1, "maximum": 10},
                        "value": {"type": "number", "minimum": 1, "maximum": 10},
                    },
                },
                "specifications": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string"},
                            "value": {"type": "string"},
                        },
                        "required": ["label", "value"],
                    },
                    "maxItems": 30,
                },
            },
            "required": [
                "overallScore",
                "verdict",
                "pros",
                "cons",
                "shouldYouBuy",
                "componentScores",
                "specifications",
            ],
        },
        "blocks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "type": {
                        "type": "string",
                        "enum": ["paragraph", "heading"],
                    },
                    "level": {
                        "type": "string",
                        "enum": ["none", "h2", "h3"],
                    },
                    "sectionHeading": {"type": "string"},
                    "text": {"type": "string"},
                    "drop": {"type": "boolean"},
                },
                "required": ["id", "type", "level", "sectionHeading", "text", "drop"],
            },
        },
        "images": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "keep": {"type": "boolean"},
                },
                "required": ["id", "keep"],
            },
        },
    },
    "required": [
        "title",
        "seoTitle",
        "excerpt",
        "seoDescription",
        "contentType",
        "secondaryCategories",
        "tags",
        "featured",
        "trending",
        "readingTime",
        "blocks",
        "images",
    ],
}

VALIDATION_SCHEMA = {
    "type": "object",
    "properties": {
        "readyToPublish": {"type": "boolean"},
        "boilerplateDetected": {"type": "boolean"},
        "remainingBoilerplate": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 20,
        },
        "unsupportedClaims": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 20,
        },
        "warnings": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 10,
        },
    },
    "required": [
        "readyToPublish",
        "boilerplateDetected",
        "remainingBoilerplate",
        "unsupportedClaims",
        "warnings",
    ],
}


def _split_keys(raw):
    return [
        value.strip()
        for value in re.split(r"[,;\r\n]+", raw or "")
        if value.strip() and not value.strip().startswith("#")
    ]


def load_api_keys():
    env_keys = _split_keys(os.environ.get("GEMINI_API_KEYS", ""))
    if env_keys:
        return list(dict.fromkeys(env_keys))

    key_path = Path(os.environ.get("GEMINI_API_KEYS_FILE", "api_keys.txt"))
    if not key_path.exists():
        raise RuntimeError(
            "No Gemini keys found. Set GEMINI_API_KEYS or create api_keys.txt"
        )

    file_keys = []
    for line in key_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            file_keys.extend(_split_keys(stripped))

    if not file_keys:
        raise RuntimeError(f"No Gemini API keys found in {key_path}")
    return list(dict.fromkeys(file_keys))


def load_category_prompt(category_slug):
    explicit_prompt = os.environ.get("CATEGORY_PROMPT_FILE", "").strip()
    if explicit_prompt:
        prompt_path = Path(explicit_prompt)
        if not prompt_path.exists():
            raise RuntimeError(f"Category prompt not found: {prompt_path}")
        return prompt_path.read_text(encoding="utf-8").strip()
    if category_slug in CATEGORY_DIRECTIONS:
        return CATEGORY_DIRECTIONS[category_slug]
    prompt_path = Path(f".github/prompts/{category_slug}.txt")
    if not prompt_path.exists():
        raise RuntimeError(f"Category prompt not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


def _source_host(source_url):
    from urllib.parse import urlsplit

    return urlsplit(source_url).hostname.lower().removeprefix("www.")


def _boilerplate_patterns(source_url):
    patterns = list(COMMON_BOILERPLATE_PATTERNS)
    if CONTENT_TYPE_LOCK == "review":
        patterns.extend(REVIEW_BOILERPLATE_PATTERNS)
    host = _source_host(source_url)
    for domain, domain_patterns in SOURCE_BOILERPLATE_PATTERNS.items():
        if host == domain or host.endswith("." + domain):
            patterns.extend(domain_patterns)
    return tuple(re.compile(pattern, re.IGNORECASE) for pattern in patterns)


def sanitize_source_blocks(blocks, source_url):
    """Layer-one cleanup before any source text is sent to Gemini."""
    patterns = _boilerplate_patterns(source_url)
    cleaned = []
    removed = []
    for block in blocks:
        if block.get("type") not in ("paragraph", "heading"):
            cleaned.append(block)
            continue
        text = re.sub(r"\s+", " ", str(block.get("text", ""))).strip()
        if not text:
            continue
        matched = next((pattern.pattern for pattern in patterns if pattern.search(text)), None)
        if matched:
            removed.append(text[:180])
            continue
        cleaned.append({**block, "text": text})
    if removed:
        print(f"    Pre-Gemini cleanup removed {len(removed)} boilerplate block(s)")
    return cleaned


def _source_specific_instruction(source_url):
    host = _source_host(source_url)
    if host.endswith("techradar.com"):
        return "Remove TechRadar trust copy, breaking-news signup copy, price widgets and Today's best deals."
    if host.endswith("tomshardware.com"):
        return "Remove Tom's Hardware trust copy, deal widgets, comments and newsletter modules."
    if host.endswith("theverge.com"):
        return "Remove The Verge Most Popular and related-story rails, newsletters, comments and account prompts."
    if host.endswith("engadget.com"):
        return "Remove Engadget newsletters, commerce recommendations, related stories and author footer modules."
    if host.endswith("cnet.com"):
        return "Remove CNET How We Test modules, price and deal widgets, newsletter prompts and related-story rails."
    return "Remove all source-site navigation, promotions, subscriptions, related content and interface copy."


def _is_rate_limit_error(error):
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "429",
            "resource_exhausted",
            "quota",
            "rate limit",
            "rate_limit",
            "too many requests",
            "too_many_requests",
        )
    )


def _is_rotatable_error(error):
    message = str(error).lower()
    return _is_rate_limit_error(error) or any(
        marker in message
        for marker in (
            "401",
            "403",
            "unauthenticated",
            "permission_denied",
            "api key",
        )
    )


def _is_transient_model_error(error):
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "500",
            "502",
            "503",
            "504",
            "unavailable",
            "high demand",
            "service unavailable",
            "deadline exceeded",
        )
    )


def _is_model_access_error(error):
    message = str(error).lower()
    return "404" in message and "model" in message and any(
        marker in message
        for marker in (
            "no longer available",
            "not available",
            "not found",
            "not supported",
        )
    )


def _source_blocks(blocks):
    source = []
    character_count = 0
    for block_id, block in enumerate(blocks):
        if block.get("type") not in ("paragraph", "heading"):
            continue
        text = str(block.get("text", "")).strip()
        if not text:
            continue
        if character_count + len(text) > MAX_INPUT_CHARS:
            raise RuntimeError(
                f"Article exceeds Gemini input safety limit of {MAX_INPUT_CHARS} characters"
            )
        source_block = {
            "id": block_id,
            "type": block["type"],
            "text": text,
        }
        if block["type"] == "heading":
            source_block["level"] = block.get("level", "h2")
        source.append(source_block)
        character_count += len(text)
    return source


def _source_images(blocks):
    images = []
    for block_id, block in enumerate(blocks):
        if block.get("type") != "pending_image" or not block.get("source"):
            continue
        before = next(
            (str(item.get("text", "")) for item in reversed(blocks[:block_id])
             if item.get("type") in ("paragraph", "heading") and item.get("text")),
            "",
        )
        after = next(
            (str(item.get("text", "")) for item in blocks[block_id + 1:]
             if item.get("type") in ("paragraph", "heading") and item.get("text")),
            "",
        )
        images.append({
            "id": f"image_{block_id}",
            "position": block_id,
            "isHero": block.get("isHero") is True,
            "sourceUrl": block.get("source", ""),
            "alt": block.get("alt", ""),
            "caption": block.get("caption", ""),
            "nearbyArticleText": [before[:500], after[:500]],
        })
    return images


def _structured_input_is_reliable(source_blocks):
    paragraphs = [block for block in source_blocks if block["type"] == "paragraph"]
    headings = [block for block in source_blocks if block["type"] == "heading"]
    if len(paragraphs) < 3:
        return False
    if headings and len(headings) > len(paragraphs):
        return False
    if any(len(block["text"]) > 8_000 for block in source_blocks):
        return False

    normalized = [
        re.sub(r"\W+", " ", block["text"].lower()).strip()
        for block in source_blocks
        if len(block["text"]) >= 40
    ]
    duplicate_count = len(normalized) - len(set(normalized))
    if normalized and duplicate_count / len(normalized) > 0.2:
        return False
    return True


def _raw_fallback_content(blocks):
    parts = []
    for block_id, block in enumerate(blocks):
        block_type = block.get("type")
        if block_type in ("paragraph", "heading"):
            text = str(block.get("text", "")).strip()
            if text:
                parts.append(f"[BLOCK:{block_id}]\n{text}")
        elif block_type == "pending_image" and block.get("source"):
            parts.append(f"[IMAGE:image_{block_id}]")
    return "\n\n".join(parts)


def _build_prompt(
    category_prompt,
    title,
    source_url,
    category_slug,
    published_at,
    extraction_mode,
    source_blocks,
    source_images,
    raw_content,
):
    source_payload = {
        "extractionMode": extraction_mode,
        "sourceTitle": title,
        "sourceUrl": source_url,
        "categorySlug": category_slug,
        "publishedAt": published_at,
        "images": source_images,
    }
    if extraction_mode == "structured":
        source_payload["blocks"] = source_blocks
    else:
        source_payload["content"] = raw_content

    review_rules = ""
    if CONTENT_TYPE_LOCK == "review":
        review_rules = f"""
Review workflow rules:
- contentType must be "review".
- primaryCategory must be exactly one of: {', '.join(ALLOWED_PRIMARY_CATEGORIES)}.
- Set eligibleForPublication to true only for technology products, games, computer hardware, phones, consumer electronics, or AI products that fit those categories.
- Set eligibleForPublication to false for movies, television, entertainment-only coverage, mattresses, beauty, kitchen, exercise equipment, household appliances, or anything outside BYTERMINAL's taxonomy, and explain why in rejectionReason.
- Return reviewSummary with an editorial overallScore, verdict, factual pros, factual cons, shouldYouBuy, evidence-based componentScores and verified specifications.
- This is an attributed external review summary. Never claim BYTERMINAL tested the product.
- Rewrite first-person product observations in neutral third person, such as "The source reviewer found..."; never retain "I tested", "we tested" or wording that implies BYTERMINAL performed the test.
- Do not name the source publisher inside article blocks. The publishing system credits the source separately.
- Set drop=true on every block that does not help a reader evaluate the product. This includes "How I tested" and testing-methodology sections, test duration or setup, playlists, reviewer credentials, author biographies, career history, publisher trust copy, "First reviewed" or "Last updated" stamps, promotions, affiliate/deal copy, related links and repeated summaries. Do not move dropped material into another block.
- Never output the original publisher, website, magazine or author name in any field, including titles, SEO fields, article blocks and reviewSummary. Source credit is handled separately from this JSON.
- Preserve useful product measurements and findings outside those removed sections, attributing them to "the source review" or "the source reviewer" when attribution is needed.
- overallScore is BYTERMINAL's editorial summary score. Infer it conservatively from the article's tone, verdict, strengths, weaknesses and buying advice even when the source gives no numerical score. This editorial judgement is required and is not a factual claim that the source assigned that exact number.
- If the source has a score, use it as one signal but do not present the BYTERMINAL score as the source's score or mechanically claim an unmentioned conversion.
- componentScores may also be inferred from qualitative evidence for that specific dimension. Include only dimensions the source discusses meaningfully. A component score must agree with the source's praise, criticism and caveats; it does not require an exact source number.
- Keep scores measured and internally consistent: strong praise with minor caveats should score higher than a mixed verdict, while serious flaws or poor value should materially lower the relevant score. Do not use a perfect 10 unless the source evidence is exceptionally strong.
- Editorial scores are allowed estimates. They must never be described as a benchmark, measurement, source-issued rating or result of BYTERMINAL hands-on testing.
"""
    elif CONTENT_TYPE_LOCK:
        review_rules = f'\n- contentType must be "{CONTENT_TYPE_LOCK}".\n'

    source_payload = json.dumps(source_payload, ensure_ascii=False)
    source_rule = _source_specific_instruction(source_url)
    return f"""You are the editorial production engine for BYTERMINAL, an independent English-language technology magazine.

Category-specific editorial direction:
{category_prompt}

Mandatory rules:
- Produce an original, coherent article, not a sentence-by-sentence paraphrase.
- Apply this cleanup policy to every source and category: remove navigation, menus, breadcrumbs, website UI; read-more/read-less, pagination, slide counters and gallery controls; newsletter, subscription, login, notification and app-install prompts; ads, sponsored content, affiliate widgets, price widgets, coupons and retailer modules; author bios, trust modules, copyright notices and publication boilerplate; related, recommended, trending and Most Popular sections; reader comments, forum reactions and unnecessary social posts; misplaced photo/image credits; duplicate paragraphs, repeated quotes, broken fragments, standalone labels and raw URLs; and sections about products, deals, events or topics unrelated to the title and primary subject. Keep relevant facts, context, criticism, specifications, pricing and comparisons. When unsure whether a block is relevant, remove it.
- For every text block, set drop=true when it is contamination, duplicate, broken, or unrelated to the title and primary subject. This applies to all categories, not only reviews. Do not move removed material into another block.
- Do not include the original publisher/site or author name in article fields; source credit is added separately by the publishing system.
- Return exactly one image decision per input image in the images array, using its exact id and keep=true/false. Keep only images directly relevant to the primary subject and retained article content. Remove unrelated images, logos, icons, avatars, banners, ads, placeholders, decorative images, duplicates, responsive variants/repeated crops, unnecessary gallery images, and images from removed sections. When unsure, set keep=false.
- Preserve the publisher-designated lead image (`isHero=true`) when it depicts the article's primary subject. Do not reject it only because alt text is missing or its caption is a photo credit.
- Preserve verifiable names, dates, prices, specifications, qualifications and attributed conclusions.
- Never invent facts, first-hand testing, measurements, quotes, images, links or source details.
- Do not copy distinctive wording from the source.
- Keep every returned block id exactly equal to an input block id.
- Preserve the input block order and return one result per input block. Set drop=false for retained blocks and drop=true for removed blocks; its text may be empty.
- Classify every text block as either a heading or a paragraph. Correct unreliable source labels when necessary.
- Use level "h2" or "h3" for headings and "none" for paragraphs.
- Create a navigable article structure without deleting useful evidence. For articles with at least 6 text blocks, provide 2-5 concise sectionHeading values on suitable retained paragraph blocks. For shorter articles, provide at least 1. Use an empty string on all other blocks.
- Do not add image placeholders.
- title: accurate editorial headline, ideally 45-90 characters.
- seoTitle: natural search title, ideally {SEO_TITLE_MIN}-{SEO_TITLE_MAX} characters; preserve the primary entity and topic.
- excerpt: one or two complete sentences, ideally {EXCERPT_MIN}-{EXCERPT_MAX} characters, for cards and the article dek.
- seoDescription: one complete factual sentence written independently rather than truncating the excerpt. Aim for {SEO_DESCRIPTION_MIN}-{SEO_DESCRIPTION_MAX} characters as an SEO recommendation, but clarity takes priority and text outside that range is allowed.
- seoKeywords: aim for 3-10 distinct, natural search phrases that accurately describe the article, but return fewer when only fewer useful phrases are supported. Include the primary entity and search intent, prefer specific multi-word phrases over isolated generic words, and never include publisher names or unsupported claims. Keyword quantity must never block publication.
- Every character range above is an editorial recommendation only. Never reject, omit or damage useful copy merely to hit a character count.
- Check every generated field for source-site residue using the global cleanup policy above.
- Never output phrases such as "Sign up for", "Why you can trust", "Join the conversation", "About the author", "Today's best deals", or equivalent source chrome.
- Source-specific cleanup: {source_rule}
- Do not include Markdown fences or commentary outside the JSON response.
- The original source URL is controlled and credited separately by the publishing system. Do not place it in article blocks.
- Do not generate sourcePublisher, sourcePublishedAt, slug, canonical URL or BYTERMINAL publication time.
{review_rules}

Source article data:
{source_payload}
"""


def _build_validation_prompt(source_payload, writer_result):
    return f"""Act as an independent BYTERMINAL publishing gate. You check factual integrity and source-chrome removal. You do not judge style.

How the Writer works, so you do not misread its output:
- The Writer rewrites the source block by block. Writer block N is meant to carry the same facts as Source block N, in the same order. That alignment is the required behaviour and is never a defect.
- The Writer is required to strip newsletter copy, subscription and ticket prompts, deal or price widgets, event marketing, author biographies, trust modules, related or recommended stories and comment prompts. Detail missing for that reason is correct and is never a defect.
- In reviews, a block with drop=true intentionally removes non-editorial, redundant or source-identifying material. Do not treat that removal as missing context or an unsupported claim.
- In every category, drop=true intentionally removes contamination or content unrelated to the primary subject. Image decisions with keep=false intentionally remove irrelevant, decorative, duplicate or contamination images; do not treat these removals as missing content.
- For reviews, overallScore and componentScores are BYTERMINAL editorial judgements inferred from the source's qualitative evidence. Their exact numbers usually will not appear in the source, and that is intentional.

Block publication only on these two, and put each finding in the matching array:
- unsupportedClaims: a factual statement in the Writer JSON the source does not support - an invented name, date, price, quote, link, first-hand test or measurement, or a factual number or date changed from the source. Review scores are the explicit exception described below.
- remainingBoilerplate: any contamination or off-topic material surviving in the Writer JSON - navigation, menus, breadcrumbs, website UI, read-more/read-less, pagination, slide counters/gallery controls, newsletter/subscription/login/notification/app prompts, ads/sponsored/affiliate/price/coupon/retailer modules, author bios/trust/copyright/publication boilerplate, related/recommended/trending/Most Popular sections, comments/forum reactions/unnecessary social posts, misplaced photo credits, duplicates/broken fragments/standalone labels/raw URLs, unrelated sections, original publisher/site/author names, or an image decision with keep=true for an unrelated, decorative, duplicate, responsive-variant, placeholder or removed-section image. Any such residue in any output field is blocking; source credit is handled separately.

Review score policy:
- Never put overallScore or componentScores in unsupportedClaims merely because the exact number is absent from the source.
- Accept an inferred score when it is reasonably consistent with the article's qualitative assessment, even if another editor could choose a somewhat different number.
- Block a score only when it clearly contradicts the source's overall verdict, a component has no relevant qualitative evidence at all, or the copy falsely says the source assigned that number or BYTERMINAL measured it hands-on.
- A score disagreement within a reasonable editorial range is a warning at most, never a blocker.

Everything else belongs in warnings and must not block: tag or taxonomy preferences, thin or generic headings, flat phrasing, an ordering you would have chosen differently, omitted promotional detail, or wording that stays close to the source without lifting a substantial distinctive passage. Tags are editorial classification and do not need to appear as exact words in the source. Never report a Writer block as a duplicate of a Source block. Report duplication only when the same passage repeats inside the Writer JSON itself. Similarity or near-verbatim concerns belong in warnings; reserve unsupportedClaims for factual fabrication.

The recommended editorial ranges are:
- seoTitle: {SEO_TITLE_MIN}-{SEO_TITLE_MAX} characters.
- excerpt: {EXCERPT_MIN}-{EXCERPT_MAX} characters.
For seoDescription, {SEO_DESCRIPTION_MIN}-{SEO_DESCRIPTION_MAX} characters is only a recommendation, not a publishing limit. A longer or shorter non-empty description must not block publication; record any length concern only in warnings.
All character ranges are recommendations. Never put a length concern in a blocking array and never set readyToPublish false solely because of length.

Set readyToPublish true when both remainingBoilerplate and unsupportedClaims are empty, and set boilerplateDetected to whether remainingBoilerplate is non-empty. Do not rewrite the article. Return only the validation JSON.

Source context:
{json.dumps(source_payload, ensure_ascii=False)}

Writer JSON:
{json.dumps(writer_result, ensure_ascii=False)}
"""


def _build_repair_prompt(writer_prompt, writer_result, validation):
    return f"""{writer_prompt}

The first draft failed an independent publishing check. Return a complete corrected JSON object, fixing every issue without adding unsupported factual claims. For a review, preserve or adjust the required editorial overallScore and evidence-based componentScores; do not delete them merely because the source lacks exact numeric ratings.

First draft:
{json.dumps(writer_result, ensure_ascii=False)}

Validator findings:
{json.dumps(validation, ensure_ascii=False)}
"""


def _normalized_seo_keywords(result):
    keywords = []
    seen = set()
    for item in result.get("seoKeywords") or []:
        keyword = re.sub(r"\s+", " ", str(item)).strip()
        key = keyword.casefold()
        if 2 <= len(keyword) <= 100 and key not in seen:
            seen.add(key)
            keywords.append(keyword)
    return keywords[:10]


def _writer_contract_issues(result):
    required_fields = ("title", "seoTitle", "excerpt", "seoDescription")
    issues = [
        f"{field_name} must not be empty"
        for field_name in required_fields
        if not str(result.get(field_name, "")).strip()
    ]
    return issues


BLOCK_COMPARISON_NOISE = re.compile(
    r"(?:near[\s-]?(?:duplicat|verbatim)|duplicat|identical|similar|paraphras|mirrors|echoes|reproduc)",
    re.IGNORECASE,
)
SOURCE_REFERENCE = re.compile(r"\bsource\b", re.IGNORECASE)
EDITORIAL_SCORE_REFERENCE = re.compile(
    r"(?:overallScore|componentScores?|editorial (?:summary )?score|numeric(?:al)? score)",
    re.IGNORECASE,
)
SCORE_MISATTRIBUTION = re.compile(
    r"(?:source (?:assigned|gave|awarded)|presented as (?:the )?source|"
    r"BYTERMINAL (?:tested|measured|benchmarked)|hands[\s-]?on)",
    re.IGNORECASE,
)


def _is_block_comparison(finding):
    """A boilerplate finding must quote surviving source chrome.

    A validator that instead compares a writer block with its source block is
    describing the block-aligned rewrite it was asked to accept, so the finding
    is demoted to a warning rather than blocking publication.
    """
    return bool(
        BLOCK_COMPARISON_NOISE.search(finding) and SOURCE_REFERENCE.search(finding)
    )


def _is_editorial_score_finding(finding):
    """Score plausibility is editorial judgement, not a factual publish blocker."""
    return bool(
        EDITORIAL_SCORE_REFERENCE.search(finding)
        and not SCORE_MISATTRIBUTION.search(finding)
    )


def _blocking_findings(validation, demoted=None):
    findings = {}
    for field_name in ("unsupportedClaims", "remainingBoilerplate"):
        values = []
        for item in validation.get(field_name) or []:
            text = str(item).strip()
            if not text:
                continue
            if _is_block_comparison(text) or _is_editorial_score_finding(text):
                if demoted is not None:
                    demoted.append(text)
                continue
            values.append(text)
        if values:
            findings[field_name] = values
    return findings


def _validation_failed(validation):
    return bool(_blocking_findings(validation))


def _load_key_index(total_keys):
    global _current_key_index, _key_index_loaded

    if not total_keys:
        raise RuntimeError("No Gemini API keys are configured")
    if not _key_index_loaded:
        _key_index_loaded = True
        key_index_file = os.environ.get("GEMINI_KEY_INDEX_FILE", "").strip()
        if key_index_file:
            try:
                saved_index = int(Path(key_index_file).read_text(encoding="utf-8").strip())
                _current_key_index = saved_index % total_keys
                print(f"    Resuming Gemini key rotation from key {_current_key_index + 1}")
            except (OSError, ValueError):
                pass
    _current_key_index %= total_keys
    return _current_key_index


def _save_key_index(key_index, total_keys):
    global _current_key_index

    _current_key_index = key_index % total_keys
    key_index_file = os.environ.get("GEMINI_KEY_INDEX_FILE", "").strip()
    if not key_index_file:
        return
    try:
        path = Path(key_index_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{_current_key_index}\n", encoding="utf-8")
    except OSError as error:
        print(f"    Could not save Gemini key rotation index: {error}")


def _load_model_preferences(total_keys):
    global _model_preferences_loaded

    if _model_preferences_loaded:
        return
    _model_preferences_loaded = True
    preference_file = os.environ.get("GEMINI_MODEL_PREFERENCE_FILE", "").strip()
    if not preference_file:
        return
    try:
        saved_preferences = json.loads(
            Path(preference_file).read_text(encoding="utf-8")
        )
    except (OSError, ValueError):
        return
    if not isinstance(saved_preferences, dict):
        return
    for raw_index, model_name in saved_preferences.items():
        try:
            key_index = int(raw_index)
        except (TypeError, ValueError):
            continue
        if (
            0 <= key_index < total_keys
            and model_name in FALLBACK_MODELS
            and model_name != PREFERRED_MODEL
        ):
            _model_preferences[key_index] = model_name


def _save_model_preference(key_index, model_name):
    _model_preferences[key_index] = model_name
    preference_file = os.environ.get("GEMINI_MODEL_PREFERENCE_FILE", "").strip()
    if not preference_file:
        return
    try:
        path = Path(preference_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {str(index): model for index, model in _model_preferences.items()},
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
    except OSError as error:
        print(f"    Could not save Gemini model preference: {error}")


def _request_with_rotation(prompt, api_keys, response_schema=RESPONSE_SCHEMA, temperature=0.35):
    global _current_key_index

    errors = []
    total_keys = len(api_keys)
    start_index = _load_key_index(total_keys)
    _load_model_preferences(total_keys)
    available_indexes = [
        (start_index + offset) % total_keys
        for offset in range(total_keys)
        if (start_index + offset) % total_keys
        not in _disabled_key_indexes
    ]

    if not available_indexes:
        raise RuntimeError("All Gemini keys are exhausted for this workflow run")

    for key_index in available_indexes:
        api_key = api_keys[key_index]
        client = genai.Client(api_key=api_key)
        selected_model = _model_preferences.get(key_index, PREFERRED_MODEL)
        if selected_model != PREFERRED_MODEL:
            print(
                f"    Using cached Gemini model {selected_model} "
                f"for key {key_index + 1}"
            )
        model_candidates = list(
            dict.fromkeys([selected_model, PREFERRED_MODEL, *FALLBACK_MODELS])
        )
        model_access_failed = selected_model != PREFERRED_MODEL
        rotate_key = False
        for model_name in model_candidates:
            try_another_model = False
            for attempt in range(1, 4):
                try:
                    response = client.models.generate_content(
                        model=model_name,
                        contents=prompt,
                        config=types.GenerateContentConfig(
                            response_mime_type="application/json",
                            response_schema=response_schema,
                            automatic_function_calling=types.AutomaticFunctionCallingConfig(
                                disable=True
                            ),
                            max_output_tokens=65536,
                            temperature=temperature,
                        ),
                    )
                    if not response.text:
                        raise RuntimeError("Gemini returned an empty response")
                    _save_key_index(key_index, total_keys)
                    cached_model = (
                        model_name
                        if model_access_failed and model_name != PREFERRED_MODEL
                        else PREFERRED_MODEL
                    )
                    _save_model_preference(key_index, cached_model)
                    return json.loads(response.text)
                except Exception as error:
                    if _is_model_access_error(error):
                        model_access_failed = True
                        errors.append(
                            f"key {key_index + 1} ({model_name}): model unavailable"
                        )
                        print(
                            f"    Gemini model {model_name} unavailable for key "
                            f"{key_index + 1}; trying the next model"
                        )
                        try_another_model = True
                        break

                    transient = _is_transient_model_error(error)
                    rate_limited = _is_rate_limit_error(error)
                    if (transient or rate_limited) and attempt < 3:
                        delay = 5 * (2 ** (attempt - 1)) if rate_limited else 2 ** attempt
                        print(
                            f"    Gemini {'rate limit' if rate_limited else 'temporary error'} "
                            f"on key {key_index + 1}, model {model_name} "
                            f"(attempt {attempt}/3); retrying in {delay}s"
                        )
                        time.sleep(delay)
                        continue
                    errors.append(
                        f"key {key_index + 1} ({model_name}): {type(error).__name__}"
                    )
                    if transient or rate_limited:
                        print(
                            f"    Gemini {model_name} still unavailable after retries; "
                            "trying the next model"
                        )
                        try_another_model = True
                        break
                    if not _is_rotatable_error(error):
                        raise
                    rotate_key = True
                    break
            if rotate_key:
                break
            if not try_another_model and model_name == model_candidates[-1]:
                break

        _disabled_key_indexes.add(key_index)
        _save_key_index(key_index + 1, total_keys)
        if rotate_key:
            print(
                f"    Gemini key {key_index + 1} is invalid or unauthorized; "
                "disabled for this run and rotating to the next key"
            )
        else:
            print(
                f"    All configured Gemini models failed for key {key_index + 1}; "
                "disabled for this run and rotating to the next key"
            )
    raise RuntimeError("All Gemini keys failed (" + ", ".join(errors) + ")")


def rewrite_article(title, source_url, blocks, category_slug, published_at=None):
    api_keys = load_api_keys()
    category_prompt = load_category_prompt(category_slug)
    blocks = sanitize_source_blocks(blocks, source_url)
    source_blocks = _source_blocks(blocks)
    if not source_blocks:
        raise RuntimeError("No text blocks available for Gemini")
    source_images = _source_images(blocks)
    extraction_mode = (
        "structured"
        if _structured_input_is_reliable(source_blocks)
        else "raw_fallback"
    )
    raw_content = (
        _raw_fallback_content(blocks)
        if extraction_mode == "raw_fallback"
        else None
    )
    print(f"    Gemini input mode: {extraction_mode}")

    prompt = _build_prompt(
        category_prompt,
        title,
        source_url,
        category_slug,
        published_at,
        extraction_mode,
        source_blocks,
        source_images,
        raw_content,
    )
    result = _request_with_rotation(prompt, api_keys)

    validation_source = {
        "sourceTitle": title,
        "sourceUrl": source_url,
        "categorySlug": category_slug,
        "blocks": source_blocks,
    }
    contract_issues = _writer_contract_issues(result)
    if contract_issues:
        validation = {
            "readyToPublish": False,
            "boilerplateDetected": False,
            "remainingBoilerplate": [],
            "unsupportedClaims": [],
            "fieldLimitIssues": contract_issues,
            "warnings": [],
        }
        print("    Gemini draft omitted a required field; running one repair")
    else:
        validation = _request_with_rotation(
            _build_validation_prompt(validation_source, result),
            api_keys,
            response_schema=VALIDATION_SCHEMA,
            temperature=0.0,
        )
        if _validation_failed(validation):
            print("    Gemini validator rejected the first draft; running one repair")

    if contract_issues or _validation_failed(validation):
        result = _request_with_rotation(
            _build_repair_prompt(prompt, result, validation),
            api_keys,
        )
        repaired_contract_issues = _writer_contract_issues(result)
        if repaired_contract_issues:
            raise RuntimeError(
                "Gemini still omitted a required field after repair: "
                + "; ".join(repaired_contract_issues)
            )
        validation = _request_with_rotation(
            _build_validation_prompt(validation_source, result),
            api_keys,
            response_schema=VALIDATION_SCHEMA,
            temperature=0.0,
        )
    demoted_findings = []
    blocking_findings = _blocking_findings(validation, demoted_findings)
    for finding in demoted_findings:
        print(f"    Ignored block-comparison finding: {finding}")
    if blocking_findings:
        raise RuntimeError(
            "Gemini validation failed after repair: "
            + json.dumps(blocking_findings, ensure_ascii=False)
        )
    for warning in validation.get("warnings") or []:
        print(f"    Validator warning: {warning}")

    rewritten_by_id = {}
    source_by_id = {block["id"]: block for block in source_blocks}
    valid_ids = {block["id"] for block in source_blocks}
    for item in result.get("blocks", []):
        block_id = item.get("id")
        text = str(item.get("text", "")).strip()
        block_type = item.get("type")
        level = item.get("level")
        section_heading = str(item.get("sectionHeading", "")).strip()[:500]
        if block_id not in valid_ids:
            raise RuntimeError(f"Gemini returned unknown block id {block_id}")
        if block_id in rewritten_by_id:
            raise RuntimeError(f"Gemini duplicated block id {block_id}")
        drop = CONTENT_TYPE_LOCK == "review" and item.get("drop") is True
        if drop:
            rewritten_by_id[block_id] = {
                "text": "",
                "type": block_type,
                "level": level,
                "sectionHeading": "",
                "drop": True,
            }
            continue
        if not text:
            text = source_by_id.get(block_id, {}).get("text", "").strip()
            print(f"    Gemini returned empty block {block_id}; preserving source text")
        if block_type not in ("paragraph", "heading"):
            block_type = source_by_id.get(block_id, {}).get("type", "paragraph")
            print(f"    Gemini returned invalid type for block {block_id}; using {block_type}")
        if block_type == "heading" and level not in ("h2", "h3"):
            level = source_by_id.get(block_id, {}).get("level", "h2")
            level = level if level in ("h2", "h3") else "h2"
            print(f"    Gemini returned invalid heading level for block {block_id}; using {level}")
        if block_type == "paragraph" and level != "none":
            level = "none"
        if block_type == "heading" and len(text) > 500:
            block_type = "paragraph"
            level = "none"
            section_heading = ""
            print(
                f"    Gemini classified long prose as heading for block {block_id}; "
                "using paragraph"
            )
        if block_type == "heading" and section_heading:
            print(f"    Ignoring redundant sectionHeading on heading block {block_id}")
            section_heading = ""
        rewritten_by_id[block_id] = {
            "text": text,
            "type": block_type,
            "level": level,
            "sectionHeading": section_heading,
            "drop": False,
        }

    missing_ids = valid_ids.difference(rewritten_by_id)
    for block_id in sorted(missing_ids):
        source_block = source_by_id[block_id]
        block_type = source_block["type"]
        rewritten_by_id[block_id] = {
            "text": source_block["text"],
            "type": block_type,
            "level": source_block.get("level", "h2") if block_type == "heading" else "none",
            "sectionHeading": "",
            "drop": True,
        }
        print(f"    Gemini omitted block {block_id}; dropping unreviewed source block")

    inserted_ids = [
        block_id
        for block_id in sorted(rewritten_by_id)
        if rewritten_by_id[block_id]["sectionHeading"]
    ]
    for block_id in inserted_ids[5:]:
        rewritten_by_id[block_id]["sectionHeading"] = ""
    if len(inserted_ids) > 5:
        print(f"    Limited inserted headings from {len(inserted_ids)} to 5")

    classified_headings = [
        block["text"]
        for block in rewritten_by_id.values()
        if not block["drop"] and block["type"] == "heading"
    ]
    inserted_headings = [
        block["sectionHeading"]
        for block in rewritten_by_id.values()
        if not block["drop"] and block["sectionHeading"]
    ]
    heading_count = len(classified_headings) + len(inserted_headings)
    required_heading_count = 2 if len(source_blocks) >= 6 else 1
    if heading_count < required_heading_count:
        raise RuntimeError(
            "Gemini did not create enough section headings "
            f"({heading_count}/{required_heading_count}); "
            f"classified={len(classified_headings)}, inserted={len(inserted_headings)}"
        )

    print(
        "    Gemini structure: "
        f"{len(classified_headings)} classified heading(s), "
        f"{len(inserted_headings)} inserted heading(s)"
    )
    for heading in classified_headings:
        print(f"      H(source): {heading}")
    for heading in inserted_headings:
        print(f"      H(inserted): {heading}")

    image_decisions = {
        item.get("id"): item.get("keep") is True
        for item in result.get("images", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }
    rewritten_blocks = []
    for block_id, block in enumerate(blocks):
        if block_id in rewritten_by_id:
            rewritten = rewritten_by_id[block_id]
            if rewritten["drop"]:
                continue
            if rewritten["sectionHeading"]:
                rewritten_blocks.append(
                    {
                        "type": "heading",
                        "level": "h2",
                        "text": rewritten["sectionHeading"],
                    }
                )
            if rewritten["type"] == "heading":
                rewritten_blocks.append(
                    {
                        "type": "heading",
                        "level": rewritten["level"],
                        "text": rewritten["text"],
                    }
                )
            else:
                rewritten_blocks.append(
                    {
                        "type": "paragraph",
                        "text": rewritten["text"],
                    }
                )
        else:
            if block.get("type") == "pending_image":
                if block.get("isHero") is True:
                    if not image_decisions.get(f"image_{block_id}", False):
                        print(
                            f"    Gemini rejected publisher lead image at block {block_id}; "
                            "retaining it as the article hero"
                        )
                    rewritten_blocks.append(block)
                elif image_decisions.get(f"image_{block_id}", False):
                    rewritten_blocks.append(block)
                else:
                    print(
                        f"    Gemini removed irrelevant or unapproved image at block {block_id}"
                    )
                continue
            rewritten_blocks.append(block)

    rewritten_title = str(result.get("title", "")).strip()
    seo_title = str(result.get("seoTitle", "")).strip()
    rewritten_excerpt = str(result.get("excerpt", "")).strip()
    seo_description = str(result.get("seoDescription", "")).strip()
    seo_keywords = _normalized_seo_keywords(result)
    required_values = {
        "title": rewritten_title,
        "seoTitle": seo_title,
        "excerpt": rewritten_excerpt,
        "seoDescription": seo_description,
    }
    missing_fields = [name for name, value in required_values.items() if not value]
    if missing_fields:
        raise RuntimeError("Gemini response is missing: " + ", ".join(missing_fields))
    content_type = str(result.get("contentType", "")).strip()
    if CONTENT_TYPE_LOCK and content_type != CONTENT_TYPE_LOCK:
        raise RuntimeError(
            f"Gemini returned contentType {content_type!r}; expected {CONTENT_TYPE_LOCK!r}"
        )

    metadata = {
        "seoTitle": seo_title,
        "seoDescription": seo_description,
        "seoKeywords": seo_keywords,
        "contentType": content_type,
        "secondaryCategorySlugs": list(dict.fromkeys(result["secondaryCategories"])),
        "tagSlugs": list(dict.fromkeys(result["tags"])),
        "featured": bool(result["featured"]),
        "trending": bool(result["trending"]),
        "readingTime": int(result["readingTime"]),
        "qualityAssessment": {
            "readyToPublish": True,
            "boilerplateDetected": False,
            "remainingBoilerplate": [],
            "unsupportedClaims": [],
            "warnings": [
                str(item).strip()[:500]
                for item in validation.get("warnings", [])
                if str(item).strip()
            ][:10],
        },
    }
    if DYNAMIC_PRIMARY_CATEGORY:
        if result.get("eligibleForPublication") is not True:
            reason = str(result.get("rejectionReason", "outside BYTERMINAL taxonomy")).strip()
            raise RuntimeError(f"Review is not eligible for publication: {reason}")
        primary_category = str(result.get("primaryCategory", "")).strip()
        if primary_category not in ALLOWED_PRIMARY_CATEGORIES:
            raise RuntimeError(
                f"Gemini returned invalid primaryCategory {primary_category!r}"
            )
        metadata["categorySlug"] = primary_category

    if content_type == "review":
        summary = result.get("reviewSummary") or {}
        verdict = str(summary.get("verdict", "")).strip()
        should_you_buy = str(summary.get("shouldYouBuy", "")).strip()
        if not verdict or not should_you_buy:
            raise RuntimeError("Gemini review response is missing verdict or shouldYouBuy")
        metadata["reviewSummary"] = {
            "overallScore": float(summary["overallScore"]),
            "verdict": verdict[:2000],
            "pros": [
                str(item).strip()[:300]
                for item in summary.get("pros", [])
                if str(item).strip()
            ][:8],
            "cons": [
                str(item).strip()[:300]
                for item in summary.get("cons", [])
                if str(item).strip()
            ][:8],
            "shouldYouBuy": should_you_buy[:2000],
            "componentScores": {
                key: float(value)
                for key, value in (summary.get("componentScores") or {}).items()
                if key in ("design", "display", "performance", "battery", "value")
            },
            "specifications": [
                {
                    "label": str(item.get("label", "")).strip()[:100],
                    "value": str(item.get("value", "")).strip()[:500],
                }
                for item in summary.get("specifications", [])
                if str(item.get("label", "")).strip()
                and str(item.get("value", "")).strip()
            ][:30],
        }
        metadata["affiliateDisclosure"] = True

    return rewritten_title, rewritten_excerpt, rewritten_blocks, metadata
