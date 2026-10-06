import unittest

from manual_review import source_article_title, validate_manual_review_url


class ManualReviewTests(unittest.TestCase):
    def test_accepts_only_techradar_https_article(self):
        self.assertEqual(
            validate_manual_review_url("https://www.techradar.com/phones/example-review#section"),
            "https://www.techradar.com/phones/example-review",
        )
        for url in (
            "http://www.techradar.com/reviews/example",
            "https://evil.example/reviews/example",
            "https://techradar.com.evil.example/reviews/example",
            "https://www.techradar.com/",
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
