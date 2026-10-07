import unittest

from manual_review import source_article_title, validate_manual_review_url


class ManualReviewTests(unittest.TestCase):
    def test_accepts_configured_review_sources_only(self):
        for host in (
            "www.techradar.com", "www.theverge.com", "www.ign.com",
            "www.cnet.com", "www.techguide.com.au",
        ):
            with self.subTest(host=host):
                self.assertEqual(
                    validate_manual_review_url(f"https://{host}/reviews/example-review#section"),
                    f"https://{host}/reviews/example-review",
                )
        for url in (
            "http://www.techradar.com/reviews/example",
            "https://evil.example/reviews/example",
            "https://techradar.com.evil.example/reviews/example",
            "https://www.techradar.com/",
            "https://www.ign.com.evil.example/articles/review",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_manual_review_url(url)

    def test_reads_source_title(self):
        self.assertEqual(
            source_article_title(b'<html><head><meta property="og:title" content="Product review"></head><body><h1>Other</h1></body></html>'),
            "Product review",
        )


if __name__ == "__main__":
    unittest.main()
