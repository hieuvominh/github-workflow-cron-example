"""Parser checks for the local article publisher; no network or credentials required."""

import importlib.util
from pathlib import Path
import unittest


SCRIPT = Path(__file__).resolve().with_name("local_post.py")
spec = importlib.util.spec_from_file_location("local_post_under_test", SCRIPT)
local_post = importlib.util.module_from_spec(spec)
spec.loader.exec_module(local_post)


class ParseDraftTests(unittest.TestCase):
    def setUp(self):
        self.body = (
            "This is a sufficiently detailed opening paragraph about a technology product, "
            "its useful features, practical limitations, compatibility, and what readers "
            "should understand before deciding whether it fits their needs.\n\n"
            "## What to know\n\n"
            "A second paragraph adds context about availability and the supported details "
            "without making claims that are not present in the supplied source material."
        )

    def draft_text(self, header=""):
        default = (
            "Title: Example product update\n"
            "Source URL: https://example.com/story\n"
            "Category: gadgets\n"
        )
        return (header or default) + "---\n" + self.body

    def test_parses_headers_hero_and_inline_image_in_position(self):
        header = (
            "Title: Example product update\n"
            "Source URL: https://example.com/story\n"
            "Category: gadgets\n"
            "Hero image: https://example.com/hero.jpg | Product front view | Front view\n"
        )
        draft = local_post.parse_draft(
            header + "---\n" + self.body.split("\n\n")[0]
            + "\n\nImage: https://example.com/detail.png | Detail\n\n"
            + self.body.split("\n\n")[1]
        )
        self.assertEqual(draft.title, "Example product update")
        self.assertEqual(draft.source_url, "https://example.com/story")
        self.assertEqual(draft.blocks[0]["type"], "pending_image")
        self.assertTrue(draft.blocks[0]["isHero"])
        images = [block for block in draft.blocks if block["type"] == "pending_image"]
        self.assertEqual(len(images), 2)
        self.assertFalse(images[1]["isHero"])
        self.assertEqual(draft.blocks[1]["type"], "paragraph")
        self.assertEqual(draft.blocks[2]["type"], "pending_image")

    def test_rejects_missing_separator_and_duplicate_fields(self):
        with self.assertRaisesRegex(ValueError, "---"):
            local_post.parse_draft("Title: Test")
        header = "Title: A\nTitle: B\nSource URL: https://example.com\nCategory: ai\n"
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            local_post.parse_draft(self.draft_text(header))

    def test_rejects_invalid_url_category_and_short_body(self):
        bad_url = "Title: A\nSource URL: file:///tmp/a\nCategory: ai\n"
        with self.assertRaisesRegex(ValueError, "http\(s\)"):
            local_post.parse_draft(self.draft_text(bad_url))
        bad_category = "Title: A\nSource URL: https://example.com\nCategory: other\n"
        with self.assertRaisesRegex(ValueError, "Category must"):
            local_post.parse_draft(self.draft_text(bad_category))
        with self.assertRaisesRegex(ValueError, "too short"):
            local_post.parse_draft(
                "Title: A\nSource URL: https://example.com\nCategory: ai\n---\nShort text."
            )

    def test_review_category_sets_review_type_and_review_type_requires_category(self):
        review_header = (
            "Title: Example review\nSource URL: https://example.com/review\n"
            "Category: reviews\n"
        )
        self.assertEqual(local_post.parse_draft(self.draft_text(review_header)).content_type, "review")
        wrong_category = (
            "Title: Example review\nSource URL: https://example.com/review\n"
            "Category: gadgets\nContent type: review\n"
        )
        with self.assertRaisesRegex(ValueError, "requires Category: reviews"):
            local_post.parse_draft(self.draft_text(wrong_category))


if __name__ == "__main__":
    unittest.main()
