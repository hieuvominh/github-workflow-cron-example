import unittest

from review_affiliates import (
    affiliate_product_payload,
    amazon_affiliate_url,
    extract_review_amazon_candidates,
    extract_techradar_amazon_candidates,
    in_review_affiliate_sample,
    review_publication_fields,
)


class ReviewAffiliatesTests(unittest.TestCase):
    SOURCE = "https://www.techradar.com/phones/samsung-galaxy-phones/example-review"

    def test_automatic_sample_is_stable_and_near_thirty_percent(self):
        urls = [f"https://www.techradar.com/reviews/product-{index}" for index in range(1000)]
        first = [url for url in urls if in_review_affiliate_sample(url, 30)]
        self.assertEqual(first, [url for url in urls if in_review_affiliate_sample(url, 30)])
        self.assertTrue(250 <= len(first) <= 350)
        self.assertFalse(in_review_affiliate_sample(self.SOURCE, 0))
        self.assertTrue(in_review_affiliate_sample(self.SOURCE, 100))

    def test_scheduled_affiliate_articles_publish_with_products(self):
        product = {"asin": "B0H8N49DSC", "affiliateUrl": amazon_affiliate_url("B0H8N49DSC")}
        self.assertEqual(review_publication_fields(False, []), {"publish": True})
        unrelated = affiliate_product_payload([
            {"name": "Unrelated accessory", "asin": "B000000000", "related": False},
        ])
        self.assertEqual(review_publication_fields(False, unrelated), {"publish": True})
        self.assertEqual(review_publication_fields(False, [product]), {
            "publish": True, "affiliateProducts": [product],
        })
        self.assertEqual(review_publication_fields(True, []), {
            "publish": False, "affiliateProducts": [],
        })

    def test_extracts_deduplicated_amazon_product_and_removes_source_tag(self):
        source_link = (
            "https://target.georiot.com/Proxy.ashx?tsid=8428&amp;GR_URL="
            "https%3A%2F%2Fwww.amazon.com%2Fdp%2FB0F7K3FQN1%3Ftag%3Dftr-techradar-row-20"
            "%26ascsubtag%3Dtrd-vn-123"
        )
        page = f"""<aside class="ecom-root"><div class="hawk-grid-item-block-container">
        <a class="hawk-affiliate-link-button" aria-label="View Samsung Galaxy Z Fold 7 on Amazon"
           data-aps-asin="B0F7K3FQN1" href="{source_link}">View Prime Day</a>
        <a class="hawk-affiliate-link-container" aria-label="View Samsung Galaxy Z Fold 7 on Amazon"
           data-aps-asin="B0F7K3FQN1" href="{source_link}">View</a>
        </div></aside>"""
        self.assertEqual(extract_techradar_amazon_candidates(page, self.SOURCE), [
            {"id": "B0F7K3FQN1", "name": "Samsung Galaxy Z Fold 7", "asin": "B0F7K3FQN1"}
        ])
        self.assertEqual(
            amazon_affiliate_url("B0F7K3FQN1"),
            "https://www.amazon.com/dp/B0F7K3FQN1?tag=byterminal-20",
        )

    def test_rejects_untrusted_redirect_and_mismatched_asin(self):
        page = """<aside class="ecom-root">
        <a class="hawk-affiliate-link-button" aria-label="View Product on Amazon"
           href="https://target.georiot.com/Proxy.ashx?GR_URL=https%3A%2F%2Fevil.example%2Fdp%2FB0F7K3FQN1">View</a>
        <a class="hawk-affiliate-link-button" aria-label="View Product on Amazon"
           data-aps-asin="B000000000" href="https://www.amazon.com/dp/B0F7K3FQN1">View</a>
        </aside>"""
        self.assertEqual(extract_techradar_amazon_candidates(page, self.SOURCE), [])

    def test_only_techradar_hawk_widgets(self):
        page = """<a href="https://www.amazon.com/dp/B0F7K3FQN1"
          aria-label="View Samsung Galaxy Z Fold 7 on Amazon">Ordinary link</a>"""
        self.assertEqual(extract_techradar_amazon_candidates(page, self.SOURCE), [])
        self.assertEqual(extract_techradar_amazon_candidates(page, "https://example.com/review"), [])

    def test_reads_current_price_only_from_same_product_card(self):
        page = """<aside class="ecom-root">
        <div class="hawk-grid-item-container">
          <a class="hawk-affiliate-link-button" aria-label="View Samsung Galaxy Z Fold 7 on Amazon"
             data-aps-asin="B0F7K3FQN1" href="https://www.amazon.com/dp/B0F7K3FQN1">View</a>
          <a data-aps-asin="B0F7K3FQN1"><span data-type="wasPrice"><span class="hawk-display-price-price">$2,199.99</span></span></a>
          <a data-aps-asin="B0F7K3FQN1"><span data-type="retail"><span class="hawk-display-price-price">$1,799.99</span></span></a>
          <a data-aps-asin="B000000000"><span data-type="retail"><span class="hawk-display-price-price">$4.99</span></span></a>
        </div></aside>"""
        candidate = extract_techradar_amazon_candidates(page, self.SOURCE)[0]
        self.assertEqual(candidate["sourceObservedPrice"], {
            "amount": "1799.99", "currency": "USD", "source": "techradar_widget"
        })

    def test_payload_contains_only_gemini_approved_products(self):
        decisions = [
            {
                "name": "EarFun Wave Pro X", "asin": "B0H8N49DSC", "related": True,
                "sourceObservedPrice": {"amount": "103.99", "currency": "USD", "source": "techradar_widget"},
            },
            {"name": "Other headphones", "asin": "B000000000", "related": False},
        ]
        self.assertEqual(affiliate_product_payload(decisions), [{
            "name": "EarFun Wave Pro X",
            "merchant": "amazon.com",
            "asin": "B0H8N49DSC",
            "affiliateUrl": "https://www.amazon.com/dp/B0H8N49DSC?tag=byterminal-20",
            "price": 103.99,
            "currency": "USD",
        }])

    def test_payload_keeps_missing_price_null(self):
        self.assertEqual(affiliate_product_payload([
            {"name": "EarFun Wave Pro X", "asin": "B0H8N49DSC", "related": True},
        ])[0]["price"], None)

    def test_verge_scorecard_name_price_and_deduplication(self):
        page = """<main><article><div class="duet--article--scorecard">
        <h3><a href="https://www.amazon.com/Sonos-Ultra/dp/B0H8TBGMBJ?tag=theverge02-20">Sonos Ace Ultra headphones</a></h3>
        <a href="https://www.amazon.com/Sonos-Ultra/dp/B0H8TBGMBJ?tag=theverge02-20">$449 at Amazon</a>
        </div></article></main>"""
        self.assertEqual(extract_review_amazon_candidates(page, "https://www.theverge.com/tech/review"), [{
            "id": "B0H8TBGMBJ", "name": "Sonos Ace Ultra headphones", "asin": "B0H8TBGMBJ",
            "sourceObservedPrice": {"amount": "449", "currency": "USD", "source": "theverge.com_card"},
        }])

    def test_cnet_redirect_uses_embedded_amazon_product_not_source_tracking(self):
        page = """<main><article><div class="zd-product-review-card">
        <h2 class="zd-product-review-card__title">Abode Starter Kit</h2>
        <a href="https://cc.cnet.com/v1/otc/abc?url=https%3A%2F%2Fwww.amazon.com%2Fdp%2FB0793N1V54%3Ftag%3Dcnet-20">$219 at Amazon</a>
        </div></article></main>"""
        self.assertEqual(extract_review_amazon_candidates(page, "https://www.cnet.com/home/review/"), [{
            "id": "B0793N1V54", "name": "Abode Starter Kit", "asin": "B0793N1V54",
            "sourceObservedPrice": {"amount": "219", "currency": "USD", "source": "cnet.com_card"},
        }])

    def test_cnet_rejects_non_amazon_redirect_target(self):
        page = """<main><article><div class="zd-product-review-card">
        <h2 class="zd-product-review-card__title">Unrelated item</h2>
        <a href="https://cc.cnet.com/v1/otc/abc?url=https%3A%2F%2Fevil.example%2Fdp%2FB0793N1V54">$219 at Amazon</a>
        </div></article></main>"""
        self.assertEqual(extract_review_amazon_candidates(page, "https://www.cnet.com/home/review/"), [])

    def test_ign_short_link_resolves_to_amazon_without_including_sidebar(self):
        page = """<main><article><div class="product-card"><h3 class="name">SteelSeries Apex Pro (Gen 3)</h3>
        <a href="https://zdcs.link/example">See it at Amazon</a></div></article>
        <aside><a href="https://www.amazon.com/Other-Product/dp/B000000000">Other Product</a></aside></main>"""
        calls = []
        def resolver(link):
            calls.append(link)
            return "https://cc.ign.com/v1/otc/abc?url=https%3A%2F%2Fwww.amazon.com%2FSteelSeries-Apex-Pro%2Fdp%2FB0DGZ3VV9X"
        self.assertEqual(extract_review_amazon_candidates(
            page, "https://www.ign.com/articles/example-review", resolver
        ), [{"id": "B0DGZ3VV9X", "name": "SteelSeries Apex Pro (Gen 3)", "asin": "B0DGZ3VV9X"}])
        self.assertEqual(calls, ["https://zdcs.link/example"])

    def test_techguide_direct_link_and_no_link(self):
        url = "https://www.techguide.com.au/reviews/computers-reviews/example/"
        page = """<main><article><p><a href="https://www.amazon.com/Apple-Mac-mini/dp/B0H8TBGMBJ">Apple Mac mini</a></p></article></main>"""
        self.assertEqual(extract_review_amazon_candidates(page, url), [
            {"id": "B0H8TBGMBJ", "name": "Apple Mac mini", "asin": "B0H8TBGMBJ"},
        ])
        self.assertEqual(extract_review_amazon_candidates("<main><article>No product link</article></main>", url), [])

    def test_gadget_sources_use_only_editorial_product_links(self):
        page = """<main><article><p>
        <a href="https://www.amazon.com/Kindle-Click/dp/B0H8TBGMBJ?tag=source-20">Kindle Click</a>
        </p></article><aside><a href="https://www.amazon.com/Other-Gadget/dp/B000000000">Other gadget</a></aside></main>"""
        expected = [{"id": "B0H8TBGMBJ", "name": "Kindle Click", "asin": "B0H8TBGMBJ"}]
        for host in ("techcrunch.com", "engadget.com", "theverge.com"):
            with self.subTest(host=host):
                self.assertEqual(
                    extract_review_amazon_candidates(page, f"https://www.{host}/example"),
                    expected,
                )

    def test_engadget_short_link_requires_amazon_product(self):
        page = """<main><article><a href="https://amzn.to/example">Kindle Click</a></article></main>"""
        resolved = "https://www.amazon.com/Kindle-Click/dp/B0H8TBGMBJ?tag=source-20"
        self.assertEqual(extract_review_amazon_candidates(
            page, "https://www.engadget.com/example", lambda _: resolved,
        ), [{"id": "B0H8TBGMBJ", "name": "Kindle Click", "asin": "B0H8TBGMBJ"}])


if __name__ == "__main__":
    unittest.main()
