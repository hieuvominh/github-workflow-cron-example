"""Offline checks for the optional Facebook Page sharing step."""

import json
from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error

import facebook_publisher as publisher


class FacebookPublisherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state_file = Path(self.temp.name) / "facebook-state.json"
        self.environment = patch.dict(os.environ, {
            "FACEBOOK_PAGE_ID": "1992170297687244",
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

    def test_http_error_logs_graph_reason_but_never_token(self):
        token = os.environ["FACEBOOK_PAGE_ACCESS_TOKEN"]
        body = json.dumps({"error": {
            "type": "OAuthException",
            "code": 200,
            "error_subcode": 123,
            "message": f"Missing pages_manage_posts for access_token={token}",
            "fbtrace_id": "ABC123",
        }}).encode("utf-8")
        response = urllib.error.HTTPError("https://graph.facebook.com", 400, "Bad Request", {}, BytesIO(body))
        with patch.object(publisher.urllib.request, "urlopen", side_effect=response):
            with self.assertRaises(RuntimeError) as raised:
                publisher._post_link("https://www.byterminal.com/ai/example-123", "Example")
        detail = str(raised.exception)
        self.assertIn("HTTP 400", detail)
        self.assertIn("code=200", detail)
        self.assertIn("error_subcode=123", detail)
        self.assertIn("pages_manage_posts", detail)
        self.assertNotIn(token, detail)

    def test_non_json_http_error_stays_generic(self):
        response = urllib.error.HTTPError("https://graph.facebook.com", 400, "Bad Request", {}, BytesIO(b"not json"))
        self.assertEqual(publisher._facebook_error_detail(response, "test-token-not-real"), "Facebook API HTTP 400")


if __name__ == "__main__":
    unittest.main()
