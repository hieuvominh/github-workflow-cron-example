import json
import os
import re
from pathlib import Path

from google import genai
from google.genai import types


MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
MAX_INPUT_CHARS = int(os.environ.get("GEMINI_MAX_INPUT_CHARS", "60000"))
_current_key_index = 0
_disabled_key_indexes = set()

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
        "excerpt": {"type": "string"},
        "contentType": {
            "type": "string",
            "enum": ["news", "guide", "opinion"],
        },
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
        "excerpt",
        "contentType",
        "secondaryCategories",
        "tags",
        "featured",
        "trending",
        "readingTime",
        "blocks",
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
    prompt_path = Path(
        os.environ.get(
            "CATEGORY_PROMPT_FILE",
            f".github/prompts/{category_slug}.txt",
        )
    )
    if not prompt_path.exists():
        raise RuntimeError(f"Category prompt not found: {prompt_path}")
    return prompt_path.read_text(encoding="utf-8").strip()


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

    source_payload = json.dumps(source_payload, ensure_ascii=False)
    return f"""You are an editor for BYTERMINAL, an independent technology magazine.

Category-specific editorial direction:
{category_prompt}

Mandatory rules:
- Produce an original, concise article, not a sentence-by-sentence paraphrase.
- Preserve factual meaning. Never invent specifications, quotes, tests, or conclusions.
- Do not copy distinctive wording from the source.
- Keep every returned block id exactly equal to an input block id.
- Preserve the input block order and return one rewritten text value per input block.
- Classify every text block as either a heading or a paragraph. Correct unreliable source labels when necessary.
- Use level "h2" or "h3" for headings and "none" for paragraphs.
- Create a navigable article structure without deleting paragraph content. For articles with at least 6 text blocks, provide 2-5 concise sectionHeading values on suitable paragraph blocks. For shorter articles, provide at least 1. Use an empty string on all other blocks.
- Do not add image placeholders.
- The title must be accurate and no longer than 180 characters.
- The excerpt must be no longer than 300 characters.
- Do not include Markdown fences or commentary outside the JSON response.
- The original source URL will be credited separately by the publishing system.

Source article data:
{source_payload}
"""


def _request_with_rotation(prompt, api_keys):
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
        try:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=RESPONSE_SCHEMA,
                    temperature=0.45,
                ),
            )
            if not response.text:
                raise RuntimeError("Gemini returned an empty response")
            _current_key_index = key_index
            return json.loads(response.text)
        except Exception as error:
            errors.append(f"key {key_index + 1}: {type(error).__name__}")
            if not _is_rotatable_error(error):
                raise
            _disabled_key_indexes.add(key_index)
            _current_key_index = (key_index + 1) % total_keys
            print(
                f"    Gemini key {key_index + 1} exhausted or unavailable; "
                "disabled for this run and rotating to the next key"
            )
    raise RuntimeError("All Gemini keys failed (" + ", ".join(errors) + ")")


def rewrite_article(title, source_url, blocks, category_slug, published_at=None):
    api_keys = load_api_keys()
    category_prompt = load_category_prompt(category_slug)
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

    rewritten_by_id = {}
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
            raise RuntimeError(f"Gemini returned empty text for block id {block_id}")
        if block_type not in ("paragraph", "heading"):
            raise RuntimeError(f"Gemini returned invalid type for block id {block_id}")
        if block_type == "heading" and level not in ("h2", "h3"):
            raise RuntimeError(f"Gemini returned invalid heading level for block id {block_id}")
        if block_type == "paragraph" and level != "none":
            raise RuntimeError(f"Gemini returned a level for paragraph block id {block_id}")
        if block_type == "heading" and section_heading:
            raise RuntimeError(
                f"Gemini returned sectionHeading on heading block id {block_id}"
            )
        rewritten_by_id[block_id] = {
            "text": text,
            "type": block_type,
            "level": level,
            "sectionHeading": section_heading,
        }

    missing_ids = valid_ids.difference(rewritten_by_id)
    if missing_ids:
        raise RuntimeError(
            f"Gemini omitted {len(missing_ids)} required article blocks"
        )

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
    rewritten_excerpt = str(result.get("excerpt", "")).strip()[:300]
    if not rewritten_title or not rewritten_excerpt:
        raise RuntimeError("Gemini response is missing title or excerpt")

    metadata = {
        "contentType": result["contentType"],
        "secondaryCategorySlugs": list(dict.fromkeys(result["secondaryCategories"])),
        "tagSlugs": list(dict.fromkeys(result["tags"])),
        "featured": bool(result["featured"]),
        "trending": bool(result["trending"]),
        "readingTime": int(result["readingTime"]),
    }

    return rewritten_title, rewritten_excerpt, rewritten_blocks, metadata
