import json
import os
import re
import time
from pathlib import Path

from google import genai
from google.genai import types


MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_INPUT_CHARS = int(os.environ.get("GEMINI_MAX_INPUT_CHARS", "60000"))
CONTENT_TYPE_LOCK = os.environ.get("CONTENT_TYPE_LOCK", "").strip()
DYNAMIC_PRIMARY_CATEGORY = (
    os.environ.get("DYNAMIC_PRIMARY_CATEGORY", "false").lower() == "true"
)
_current_key_index = 0
_disabled_key_indexes = set()

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
    "reviews": "Prepare an attributed external technology review summary. Never imply BYTERMINAL tested the product. Preserve only source-supported scores, pros, cons, verdict, advice and specifications.",
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
        "title": {"type": "string", "minLength": 5, "maxLength": 180},
        "seoTitle": {
            "type": "string",
            "minLength": SEO_TITLE_MIN,
            "maxLength": SEO_TITLE_MAX,
        },
        "excerpt": {
            "type": "string",
            "minLength": EXCERPT_MIN,
            "maxLength": EXCERPT_MAX,
        },
        "seoDescription": {
            "type": "string",
            "minLength": SEO_DESCRIPTION_MIN,
            "maxLength": SEO_DESCRIPTION_MAX,
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
                },
                "required": ["id", "type", "level", "sectionHeading", "text"],
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
    return "Remove all source-site navigation, promotions, subscriptions, related content and interface copy."


def _is_rotatable_error(error):
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "429",
            "resource_exhausted",
            "quota",
            "rate limit",
            "rate_limit",
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
    return [
        {
            "id": f"image_{block_id}",
            "position": block_id,
            "sourceUrl": block.get("source", ""),
            "alt": block.get("alt", ""),
            "caption": block.get("caption", ""),
        }
        for block_id, block in enumerate(blocks)
        if block.get("type") == "pending_image" and block.get("source")
    ]


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
- Return reviewSummary with overallScore, verdict, factual pros, factual cons, shouldYouBuy, componentScores and verified specifications.
- This is an attributed external review summary. Never claim BYTERMINAL tested the product.
- Attribute measurements, testing observations, scores and conclusions to the source.
- Never invent a numerical score or convert another publication's score into a BYTERMINAL score.
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
- Preserve verifiable names, dates, prices, specifications, qualifications and attributed conclusions.
- Never invent facts, first-hand testing, measurements, quotes, images, links or source details.
- Do not copy distinctive wording from the source.
- Keep every returned block id exactly equal to an input block id.
- Preserve the input block order and return one rewritten text value per input block.
- Classify every text block as either a heading or a paragraph. Correct unreliable source labels when necessary.
- Use level "h2" or "h3" for headings and "none" for paragraphs.
- Create a navigable article structure without deleting paragraph content. For articles with at least 6 text blocks, provide 2-5 concise sectionHeading values on suitable paragraph blocks. For shorter articles, provide at least 1. Use an empty string on all other blocks.
- Do not add image placeholders.
- title: accurate editorial headline, normally 45-90 characters and at most 180.
- seoTitle: natural search title of {SEO_TITLE_MIN}-{SEO_TITLE_MAX} characters; preserve the primary entity and topic.
- excerpt: one or two complete sentences of {EXCERPT_MIN}-{EXCERPT_MAX} characters for cards and the article dek.
- seoDescription: one complete factual sentence of {SEO_DESCRIPTION_MIN}-{SEO_DESCRIPTION_MAX} characters, written independently rather than truncating the excerpt.
- Check every generated field for source-site residue. Never output navigation, advertising, newsletter copy, subscription prompts, author biographies, trust modules, comments, account prompts, related/recommended stories, Most Popular modules, deal/price widgets or gallery controls.
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
    return f"""Act as an independent BYTERMINAL publishing gate.

Compare the Writer JSON with the supplied source context. Reject unsupported claims, changed numbers or dates, invented first-hand testing, missing material context, source-site boilerplate, duplicated sections, or SEO fields outside their limits.

The required limits are:
- seoTitle: {SEO_TITLE_MIN}-{SEO_TITLE_MAX} characters.
- excerpt: {EXCERPT_MIN}-{EXCERPT_MAX} characters.
- seoDescription: {SEO_DESCRIPTION_MIN}-{SEO_DESCRIPTION_MAX} characters and not a simple excerpt truncation.

Set readyToPublish true only when boilerplateDetected is false and both remainingBoilerplate and unsupportedClaims are empty. Do not rewrite the article. Return only the validation JSON.

Source context:
{json.dumps(source_payload, ensure_ascii=False)}

Writer JSON:
{json.dumps(writer_result, ensure_ascii=False)}
"""


def _build_repair_prompt(writer_prompt, writer_result, validation):
    return f"""{writer_prompt}

The first draft failed an independent publishing check. Return a complete corrected JSON object, fixing every issue without adding unsupported facts.

First draft:
{json.dumps(writer_result, ensure_ascii=False)}

Validator findings:
{json.dumps(validation, ensure_ascii=False)}
"""


def _writer_contract_issues(result):
    checks = (
        ("title", 5, 180),
        ("seoTitle", SEO_TITLE_MIN, SEO_TITLE_MAX),
        ("excerpt", EXCERPT_MIN, EXCERPT_MAX),
        ("seoDescription", SEO_DESCRIPTION_MIN, SEO_DESCRIPTION_MAX),
    )
    issues = []
    for field_name, minimum, maximum in checks:
        value = str(result.get(field_name, "")).strip()
        if not minimum <= len(value) <= maximum:
            issues.append(
                f"{field_name} must be {minimum}-{maximum} characters; "
                f"received {len(value)}"
            )
    return issues


def _validation_failed(validation):
    return (
        validation.get("readyToPublish") is not True
        or validation.get("boilerplateDetected") is not False
        or bool(validation.get("remainingBoilerplate"))
        or bool(validation.get("unsupportedClaims"))
    )


def _request_with_rotation(prompt, api_keys, response_schema=RESPONSE_SCHEMA, temperature=0.35):
    global _current_key_index

    errors = []
    total_keys = len(api_keys)
    available_indexes = [
        (_current_key_index + offset) % total_keys
        for offset in range(total_keys)
        if (_current_key_index + offset) % total_keys
        not in _disabled_key_indexes
    ]

    if not available_indexes:
        raise RuntimeError("All Gemini keys are exhausted for this workflow run")

    for key_index in available_indexes:
        api_key = api_keys[key_index]
        client = genai.Client(api_key=api_key)
        for attempt in range(1, 4):
            try:
                response = client.models.generate_content(
                    model=MODEL,
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
                _current_key_index = key_index
                return json.loads(response.text)
            except Exception as error:
                transient = _is_transient_model_error(error)
                if transient and attempt < 3:
                    delay = 2 ** attempt
                    print(
                        f"    Gemini temporary error on key {key_index + 1} "
                        f"(attempt {attempt}/3); retrying in {delay}s"
                    )
                    time.sleep(delay)
                    continue
                errors.append(f"key {key_index + 1}: {type(error).__name__}")
                if not (_is_rotatable_error(error) or transient):
                    raise
                _disabled_key_indexes.add(key_index)
                _current_key_index = (key_index + 1) % total_keys
                print(
                    f"    Gemini key {key_index + 1} exhausted or unavailable; "
                    "disabled for this run and rotating to the next key"
                )
                break
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
            "unsupportedClaims": contract_issues,
            "warnings": [],
        }
        print("    Gemini draft missed field limits; running one repair")
    else:
        validation = _request_with_rotation(
            _build_validation_prompt(validation_source, result),
            api_keys,
            response_schema=VALIDATION_SCHEMA,
            temperature=0.0,
        )

    if _validation_failed(validation):
        if not contract_issues:
            print("    Gemini validator rejected the first draft; running one repair")
        result = _request_with_rotation(
            _build_repair_prompt(prompt, result, validation),
            api_keys,
        )
        repaired_contract_issues = _writer_contract_issues(result)
        if repaired_contract_issues:
            raise RuntimeError(
                "Gemini still violated field limits after repair: "
                + "; ".join(repaired_contract_issues)
            )
        validation = _request_with_rotation(
            _build_validation_prompt(validation_source, result),
            api_keys,
            response_schema=VALIDATION_SCHEMA,
            temperature=0.0,
        )
    if _validation_failed(validation):
        raise RuntimeError(
            "Gemini validation failed after repair: "
            + json.dumps(validation, ensure_ascii=False)
        )

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
        }
        print(f"    Gemini omitted block {block_id}; preserving source block")

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
        if block["type"] == "heading"
    ]
    inserted_headings = [
        block["sectionHeading"]
        for block in rewritten_by_id.values()
        if block["sectionHeading"]
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

    rewritten_blocks = []
    for block_id, block in enumerate(blocks):
        if block_id in rewritten_by_id:
            rewritten = rewritten_by_id[block_id]
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
            rewritten_blocks.append(block)

    rewritten_title = str(result.get("title", "")).strip()[:180]
    seo_title = str(result.get("seoTitle", "")).strip()
    rewritten_excerpt = str(result.get("excerpt", "")).strip()
    seo_description = str(result.get("seoDescription", "")).strip()
    field_lengths = {
        "seoTitle": (seo_title, SEO_TITLE_MIN, SEO_TITLE_MAX),
        "excerpt": (rewritten_excerpt, EXCERPT_MIN, EXCERPT_MAX),
        "seoDescription": (
            seo_description,
            SEO_DESCRIPTION_MIN,
            SEO_DESCRIPTION_MAX,
        ),
    }
    if not rewritten_title:
        raise RuntimeError("Gemini response is missing title")
    for field_name, (value, minimum, maximum) in field_lengths.items():
        if not minimum <= len(value) <= maximum:
            raise RuntimeError(
                f"Gemini {field_name} must be {minimum}-{maximum} characters; "
                f"received {len(value)}"
            )

    content_type = str(result.get("contentType", "")).strip()
    if CONTENT_TYPE_LOCK and content_type != CONTENT_TYPE_LOCK:
        raise RuntimeError(
            f"Gemini returned contentType {content_type!r}; expected {CONTENT_TYPE_LOCK!r}"
        )

    metadata = {
        "seoTitle": seo_title,
        "seoDescription": seo_description,
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
