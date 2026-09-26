"""Crawler hero selection checks; no network or credentials required."""

import os
import pathlib
import sys
import types
import unittest


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


class HeroSelectionTests(unittest.TestCase):
    def test_prefers_designated_hero_and_keeps_other_images_in_body(self):
        blocks = [
            {"type": "image", "url": "https://img.test/inline.jpg", "isHero": False},
            {"type": "paragraph", "text": "Body"},
            {"type": "image", "url": "https://img.test/hero.jpg", "isHero": True},
        ]

        hero, body = CRAWL["separate_hero_image"](blocks)

        self.assertEqual(hero["url"], "https://img.test/hero.jpg")
        self.assertEqual(
            [block["url"] for block in body if block["type"] == "image"],
            ["https://img.test/inline.jpg"],
        )
        self.assertTrue(all("isHero" not in block for block in [hero, *body]))

    def test_uses_first_uploaded_image_when_source_has_no_hero(self):
        blocks = [
            {"type": "paragraph", "text": "Opening"},
            {"type": "image", "url": "https://img.test/first.jpg", "isHero": False},
            {"type": "image", "url": "https://img.test/second.jpg", "isHero": False},
        ]

        hero, body = CRAWL["separate_hero_image"](blocks)

        self.assertEqual(hero["url"], "https://img.test/first.jpg")
        self.assertEqual(
            [block["url"] for block in body if block["type"] == "image"],
            ["https://img.test/second.jpg"],
        )

    def test_leaves_text_only_article_unchanged(self):
        blocks = [{"type": "paragraph", "text": "No image"}]

        hero, body = CRAWL["separate_hero_image"](blocks)

        self.assertIsNone(hero)
        self.assertEqual(body, blocks)


if __name__ == "__main__":
    unittest.main()
