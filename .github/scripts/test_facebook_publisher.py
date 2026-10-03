"""Offline checks for the optional Facebook Page sharing step."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import facebook_publisher as publisher


class FacebookPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_file = Path(self.temp.name) / "facebook-state.json"
        self.environment = patch.dict(os.environ, {
            "FACEBOOK_PAGE_ID": "100037609826117",
            "FACEBOOK_PAGE_ACCESS_TOKEN": "test-token-not-real",
            "FACEBOOK_SHARE_STATE_FILE": str(self.state_file),
            "SITE_PUBLIC_URL": "https://www.byterminal.com",
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.article = {
            "title": "Example title",
            "excerpt": "Short description.",
            "categorySlug": "ai",
        }
        self.result = {"ok": True, "status": "published", "slug": "example-123"}

    def test_builds_url_from_saved_cms_slug(self):
        self.assertEqual(
            publisher.public_article_url(self.result, self.article),
            "https://www.byterminal.com/ai/example-123",
        )

    def test_uses_only_same_site_cms_url(self):
        result = {**self.result, "canonicalUrl": "https://www.byterminal.com/ai/canonical"}
        self.assertEqual(
            publisher.public_article_url(result, self.article),
            result["canonicalUrl"],
        )
        result["canonicalUrl"] = "https://unrelated.example/story"
        self.assertEqual(
            publisher.public_article_url(result, self.article),
            "https://www.byterminal.com/ai/example-123",
        )

    def test_posts_once_and_remembers_cms_article(self):
        with patch.object(publisher, "_post_link", return_value="page_101") as post:
            publisher.enqueue_published_article(self.result, self.article)
            publisher.enqueue_published_article(self.result, self.article)
        post.assert_called_once_with(
            "https://www.byterminal.com/ai/example-123",
            "Example title\n\nShort description.",
        )
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["pending"], {})
        self.assertEqual(state["posted"], {"https://www.byterminal.com/ai/example-123": "page_101"})

    def test_retries_when_facebook_fails_without_losing_cms_publish(self):
        with patch.object(publisher, "_post_link", side_effect=RuntimeError("temporary")):
            publisher.enqueue_published_article(self.result, self.article)
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(len(state["pending"]), 1)
        with patch.object(publisher, "_post_link", return_value="page_102") as post:
            publisher.flush_pending_shares()
        post.assert_called_once()
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["pending"], {})

    def test_does_not_share_unpublished_cms_article(self):
        with patch.object(publisher, "_post_link") as post:
            publisher.enqueue_published_article({**self.result, "status": "draft"}, self.article)
        post.assert_not_called()
        self.assertFalse(self.state_file.exists())


if __name__ == "__main__":
    unittest.main()
