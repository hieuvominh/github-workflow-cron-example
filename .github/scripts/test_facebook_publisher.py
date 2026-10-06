"""Offline checks for the optional Facebook Page sharing step."""

import json
from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import urllib.error
import urllib.parse

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
        publisher._resolve_page_token.cache_clear()
        self.addCleanup(publisher._resolve_page_token.cache_clear)
        publisher._attempted_photos.clear()
        publisher._attempted_comments.clear()
        self.addCleanup(publisher._attempted_photos.clear)
        self.addCleanup(publisher._attempted_comments.clear)
        self.article = {
            "title": "Example title",
            "excerpt": "Short description.",
            "seoDescription": "SEO description.",
            "heroImageUrl": "https://raw.githubusercontent.com/owner/media/main/articles/example.webp",
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
        photo = {"photo_id": "101", "post_id": "1992170297687244_101", "comment_id": ""}
        with patch.object(publisher, "_post_photo", return_value=photo) as post, \
             patch.object(publisher, "_post_comment", return_value="comment_101") as comment:
            publisher.enqueue_published_article(self.result, self.article)
            publisher.enqueue_published_article(self.result, self.article)
        post.assert_called_once_with(
            self.article["heroImageUrl"],
            "Example title\n\nSEO description.",
        )
        comment.assert_called_once_with("1992170297687244_101", "https://www.byterminal.com/ai/example-123")
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["pending"], {})
        self.assertEqual(state["posted"]["https://www.byterminal.com/ai/example-123"]["comment_id"], "comment_101")

    def test_retries_when_facebook_fails_without_losing_cms_publish(self):
        with patch.object(publisher, "_post_photo", side_effect=RuntimeError("temporary")):
            publisher.enqueue_published_article(self.result, self.article)
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(len(state["pending"]), 1)
        publisher._attempted_photos.clear()  # A new scheduled run.
        photo = {"photo_id": "102", "post_id": "1992170297687244_102", "comment_id": ""}
        with patch.object(publisher, "_post_photo", return_value=photo) as post, \
             patch.object(publisher, "_post_comment", return_value="comment_102"):
            publisher.flush_pending_shares()
        post.assert_called_once()
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["pending"], {})

    def test_failed_comment_retries_without_reposting_photo(self):
        photo = {"photo_id": "103", "post_id": "1992170297687244_103", "comment_id": ""}
        with patch.object(publisher, "_post_photo", return_value=photo) as post, \
             patch.object(publisher, "_post_comment", side_effect=RuntimeError("temporary")):
            publisher.enqueue_published_article(self.result, self.article)
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["pending"], {})
        self.assertEqual(state["posted"]["https://www.byterminal.com/ai/example-123"]["photo_id"], "103")
        publisher._attempted_comments.clear()  # A new scheduled run.
        with patch.object(publisher, "_post_comment", return_value="comment_103") as comment:
            publisher.flush_pending_shares()
        post.assert_called_once()
        comment.assert_called_once()
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["posted"]["https://www.byterminal.com/ai/example-123"]["comment_id"], "comment_103")

    def test_failed_photo_is_not_retried_again_in_same_run(self):
        with patch.object(publisher, "_post_photo", side_effect=RuntimeError("temporary")) as post:
            publisher.enqueue_published_article(self.result, self.article)
            publisher.flush_pending_shares()
        post.assert_called_once()

    def test_looks_up_post_id_when_photo_response_only_has_photo_id(self):
        photo = {"photo_id": "104", "post_id": "", "comment_id": ""}
        with patch.object(publisher, "_post_photo", return_value=photo) as post, \
             patch.object(publisher, "_photo_post_id", return_value="1992170297687244_104") as lookup, \
             patch.object(publisher, "_post_comment", return_value="comment_104") as comment:
            publisher.enqueue_published_article(self.result, self.article)
        post.assert_called_once()
        lookup.assert_called_once_with("104")
        comment.assert_called_once_with("1992170297687244_104", "https://www.byterminal.com/ai/example-123")
        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(state["posted"]["https://www.byterminal.com/ai/example-123"]["post_id"], "1992170297687244_104")

    def test_skips_article_without_github_hero(self):
        self.article.pop("heroImageUrl")
        with patch.object(publisher, "_post_photo") as post:
            publisher.enqueue_published_article(self.result, self.article)
        post.assert_not_called()
        self.assertFalse(self.state_file.exists())

    def test_legacy_link_queue_is_not_republished_as_photo(self):
        self.state_file.write_text(json.dumps({
            "pending": {"https://www.byterminal.com/ai/old": {"message": "Old link post"}},
            "posted": {"https://www.byterminal.com/ai/already": "page_100"},
        }), encoding="utf-8")
        with patch.object(publisher, "_post_photo") as post, patch.object(publisher, "_post_comment") as comment:
            publisher.flush_pending_shares()
        post.assert_not_called()
        comment.assert_not_called()

    def test_does_not_share_unpublished_cms_article(self):
        with patch.object(publisher, "_post_photo") as post:
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
                publisher._graph_post("1992170297687244/photos", {"url": self.article["heroImageUrl"]}, token, "v26.0")
        detail = str(raised.exception)
        self.assertIn("HTTP 400", detail)
        self.assertIn("code=200", detail)
        self.assertIn("error_subcode=123", detail)
        self.assertIn("pages_manage_posts", detail)
        self.assertNotIn(token, detail)

    def test_non_json_http_error_stays_generic(self):
        response = urllib.error.HTTPError("https://graph.facebook.com", 400, "Bad Request", {}, BytesIO(b"not json"))
        self.assertEqual(publisher._facebook_error_detail(response, "test-token-not-real"), "Facebook API HTTP 400")

    def test_photo_and_comment_requests_use_github_url_and_article_link(self):
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(request)
            if request.full_url.endswith("/photos"):
                return BytesIO(b'{"id":"42","post_id":"1992170297687244_42"}')
            return BytesIO(b'{"id":"comment_42"}')

        with patch.object(publisher, "_resolve_page_token", return_value="page-token-test"), \
             patch.object(publisher.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.assertEqual(publisher._post_photo(self.article["heroImageUrl"], "Title\n\nDescription")["post_id"], "1992170297687244_42")
            self.assertEqual(publisher._post_comment("1992170297687244_42", "https://www.byterminal.com/ai/example"), "comment_42")
        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0].full_url, "https://graph.facebook.com/v26.0/1992170297687244/photos")
        self.assertEqual(requests[1].full_url, "https://graph.facebook.com/v26.0/1992170297687244_42/comments")
        self.assertEqual(requests[0].get_header("Authorization"), "Bearer page-token-test")
        photo_fields = dict(urllib.parse.parse_qsl(requests[0].data.decode("utf-8")))
        comment_fields = dict(urllib.parse.parse_qsl(requests[1].data.decode("utf-8")))
        self.assertEqual(photo_fields, {"url": self.article["heroImageUrl"], "caption": "Title\n\nDescription", "published": "true"})
        self.assertEqual(comment_fields, {"message": "Read the full article: https://www.byterminal.com/ai/example"})

    def test_exchanges_system_user_token_and_posts_with_page_token(self):
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(request)
            if request.get_method() == "POST":
                return BytesIO(b'{"id":"1992170297687244_43"}')
            if "1992170297687244?" in request.full_url:
                return BytesIO(b'{"id":"1992170297687244","name":"Byterminal","access_token":"page-token-test"}')
            if request.get_header("Authorization") == "Bearer page-token-test":
                return BytesIO(b'{"id":"1992170297687244","name":"Byterminal"}')
            return BytesIO(b'{"id":"system-user-id","name":"System User"}')

        with patch.object(publisher.urllib.request, "urlopen", side_effect=fake_urlopen):
            self.assertEqual(publisher._resolve_page_token("1992170297687244", "v26.0", "test-token-not-real"), "page-token-test")
            self.assertEqual(publisher._resolve_page_token("1992170297687244", "v26.0", "test-token-not-real"), "page-token-test")
        self.assertEqual(len([request for request in requests if request.get_method() == "GET"]), 3)
        self.assertEqual(requests[-1].get_header("Authorization"), "Bearer page-token-test")

    def test_does_not_post_if_meta_does_not_return_page_token(self):
        requests = []

        def fake_urlopen(request, timeout):
            requests.append(request)
            if "1992170297687244?" in request.full_url:
                return BytesIO(b'{"id":"1992170297687244","name":"Byterminal"}')
            return BytesIO(b'{"id":"system-user-id"}')

        with patch.object(publisher.urllib.request, "urlopen", side_effect=fake_urlopen):
            with self.assertRaisesRegex(RuntimeError, "Meta did not return its Page token"):
                publisher._resolve_page_token("1992170297687244", "v26.0", "test-token-not-real")
        self.assertTrue(all(request.get_method() == "GET" for request in requests))


if __name__ == "__main__":
    unittest.main()
