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

## Category prompts

Prompts are stored in `.github/prompts/`:

- `news.txt`
- `phones.txt`
- `ai.txt`
- `computing.txt`
- `gadgets.txt`
- `gaming.txt`
- `guides.txt`

Edit a prompt file to change only that category's tone and editorial focus.

## Repository variables

- `MEDIA_REPO`
- `MEDIA_BRANCH` (defaults to `main`)
- `GEMINI_MODEL` (defaults to `gemini-2.5-flash`)
- `NEWS_FEED_URL`
- `PHONES_FEED_URL`
- `AI_FEED_URL`
- `COMPUTING_FEED_URL`
- `GADGETS_FEED_URL`
- `GAMING_FEED_URL`
- `GUIDES_FEED_URL`
