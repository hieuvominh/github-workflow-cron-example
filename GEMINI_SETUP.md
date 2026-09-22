# Gemini setup for the BYTERMINAL crawler

Each publication category has an independent GitHub Actions workflow. The shared
crawler extracts an article, rewrites its text with Gemini, keeps images in their
original block positions, uploads images to the media repository, and publishes
the result through the BYTERMINAL ingestion API.

## Required GitHub Actions secrets

- `BYTEKORA_URL`
- `BYTEKORA_INGEST_SECRET`
- `MEDIA_TOKEN`
- `GEMINI_API_KEYS`

`GEMINI_API_KEYS` accepts one key or multiple keys separated by commas. Newlines
and semicolons are also accepted. Keep this value in **Settings > Secrets and
variables > Actions > Secrets**. Never add real keys to the repository.

Example secret value:

```text
YOUR_GEMINI_KEY_1,YOUR_GEMINI_KEY_2,YOUR_GEMINI_KEY_3
```

## Local API key file

Copy `api_keys.example.txt` to `api_keys.txt`, then put one key on each line or
separate keys with commas. `api_keys.txt` is ignored by Git.

To use a different private file path, set `GEMINI_API_KEYS_FILE`.

## Editorial prompts and validation

The shared production policy lives in `.github/scripts/gemini_rewriter.py`. It applies two layers:
deterministic source cleanup, followed by a Gemini writer and an independent Gemini validator. A
failed draft receives one repair attempt and is never published if it fails validation again.

Only two validator findings block publication: `unsupportedClaims` (a fact the source does not
support, or distinctive source wording lifted near-verbatim) and `remainingBoilerplate` (source-site
chrome that survived the rewrite). Field lengths are measured in Python rather than by the model,
and everything else the validator notices is printed as a warning. Because the writer is required to
return one rewritten block per source block, a finding that merely compares a writer block with its
source block is demoted to a warning instead of blocking the article.

Built-in category directions are defined beside that shared policy. Files in `.github/prompts/` are
kept as optional custom prompt overrides:

- `news.txt`
- `phones.txt`
- `ai.txt`
- `computing.txt`
- `gadgets.txt`
- `gaming.txt`
- `guides.txt`

Set `CATEGORY_PROMPT_FILE` to use one of these files instead of the built-in direction.

Gemini creates the display title, SEO title, excerpt, SEO description, taxonomy, article blocks and
review fields. The crawler owns the source URL; BYTERMINAL derives the publisher, canonical URL,
slug and publication time. The source publication date is context only and is not stored.

## Repository variables

- `MEDIA_REPO`
- `MEDIA_BRANCH` (defaults to `main`)
- `GEMINI_MODEL` (defaults to `gemini-3.6-flash`)
- `NEWS_FEED_URL`
- `PHONES_FEED_URL`
- `AI_FEED_URL`
- `COMPUTING_FEED_URL`
- `GADGETS_FEED_URL`
- `GAMING_FEED_URL`
- `GUIDES_FEED_URL`
