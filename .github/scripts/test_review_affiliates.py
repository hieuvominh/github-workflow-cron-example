import unittest

from review_affiliates import (
    affiliate_product_payload,
    amazon_affiliate_url,
    extract_techradar_amazon_candidates,
)


class ReviewAffiliatesTests(unittest.TestCase):
    SOURCE = "https://www.techradar.com/phones/samsung-galaxy-phones/example-review"

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


if __name__ == "__main__":
    unittest.main()
