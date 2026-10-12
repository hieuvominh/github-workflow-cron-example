import unittest

from lxml import html as lxml_html

from review_source_trial import (
    is_trial_review_url,
    prepare_trial_review_html,
    trial_article_body_node,
    trial_feed_urls,
    validate_trial_review_url,
)


class ReviewSourceTrialTests(unittest.TestCase):
    def test_only_review_articles_from_selected_source(self):
        examples = {
            "gsmarena": "https://www.gsmarena.com/vivo_x_fold6-review-3006.php",
            "pcgamer": "https://www.pcgamer.com/games/life-sim/nivalis-nights-review/",
            "wccftech": "https://wccftech.com/review/gmktec-evo-x3-mini-pc-review/",
        }
        for source, url in examples.items():
            with self.subTest(source=source):
                self.assertTrue(is_trial_review_url(url, source))
                self.assertEqual(validate_trial_review_url(url + "?utm_source=rss#top", source), url)
        for url in (
            "https://www.gsmarena.com/vivo_x_fold6-review-3006p2.php",
            "https://www.gsmarena.com/week_41_in_review-news-75000.php",
            "https://www.pcgamer.com/reviews/",
            "https://www.pcgamer.com/games/star-wars-review-process-is-changing/",
            "https://wccftech.com/review/",
            "https://evil.example/review/example-review/",
            "http://www.gsmarena.com/vivo_x_fold6-review-3006.php",
        ):
            with self.subTest(url=url):
                self.assertFalse(is_trial_review_url(url))
        self.assertEqual(len(trial_feed_urls("all")), 3)

    def test_gsmarena_merges_all_pages_and_limits_each_page_images(self):
        url = "https://www.gsmarena.com/example-review-1234.php"
        page_1 = """<html><head><meta property="og:image" content="hero.jpg"></head><body>
        <div id="review-body"><h3>Introduction</h3><p>First page facts.</p>
        <img src="one.jpg"><img src="two.jpg"><img src="three.jpg"></div>
        <a href="example-review-1234p2.php">2. Testing</a>
        <a href="example-review-1234p3.php">3. Verdict</a>
        <div id="comments">Reader reactions</div></body></html>"""
        pages = {
            "https://www.gsmarena.com/example-review-1234p2.php":
                '<html><div id="review-body"><h3>Testing</h3><p>Benchmarks.</p>'
                '<img src="four.jpg"><img src="five.jpg"><img src="six.jpg"></div></html>',
            "https://www.gsmarena.com/example-review-1234p3.php":
                '<html><div id="review-body"><h3>Verdict</h3><p>Final recommendation.</p>'
                '<img src="seven.jpg"></div></html>',
        }
        calls = []

        def fetch(page_url):
            calls.append(page_url)
            return pages[page_url]

        document = lxml_html.fromstring(prepare_trial_review_html(page_1, url, fetch))
        body = trial_article_body_node(document, url)
        self.assertEqual(calls, list(pages))
        self.assertIn("Benchmarks.", " ".join(body.itertext()))
        self.assertIn("Final recommendation.", " ".join(body.itertext()))
        self.assertEqual(len(body.xpath(".//img")), 5)
        self.assertEqual(len(document.xpath('//*[@id="comments"]')), 1)

    def test_gsmarena_fails_closed_when_a_page_is_missing(self):
        url = "https://www.gsmarena.com/example-review-1234.php"
        page = '<html><div id="review-body">Introduction</div>' \
               '<a href="example-review-1234p2.php">Testing</a></html>'
        with self.assertRaisesRegex(ValueError, "Could not download"):
            prepare_trial_review_html(page, url, lambda _: None)

    def test_pcgamer_removes_price_comparison(self):
        url = "https://www.pcgamer.com/games/example-review/"
        page = '<div id="article-body"><p>Editorial verdict.</p>' \
               '<aside class="ecom-root">Unrelated deals</aside></div>'
        document = lxml_html.fromstring(prepare_trial_review_html(page, url, lambda _: None))
        self.assertNotIn("Unrelated deals", " ".join(document.itertext()))
        self.assertIn("Editorial verdict.", " ".join(document.itertext()))

    def test_wccftech_removes_purchase_and_author_modules(self):
        url = "https://wccftech.com/review/example-review/"
        page = """<html><main><div class="post"><p>Editorial review.</p>
        <div class="related-story">Other story</div>
        <p>It can be bought from the following links:</p><ul><li>Amazon Link</li></ul>
        <div class="author-notice">Author biography</div>
        </div></main></html>"""
        document = lxml_html.fromstring(prepare_trial_review_html(page, url, lambda _: None))
        text = " ".join(document.itertext())
        self.assertIn("Editorial review.", text)
        for removed in ("Other story", "Amazon Link", "Author biography"):
            self.assertNotIn(removed, text)


if __name__ == "__main__":
    unittest.main()
