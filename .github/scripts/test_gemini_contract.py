"""Focused contract checks for Gemini editorial metadata.

Run with: python .github/scripts/test_gemini_contract.py
"""

import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import patch


SCRIPTS = pathlib.Path(__file__).resolve().parent

# The contract helpers do not call Gemini. Stub the optional SDK so these tests
# stay fast and do not require credentials or third-party packages.
google = types.ModuleType("google")
genai = types.ModuleType("google.genai")
genai.types = types.SimpleNamespace()
google.genai = genai
sys.modules.setdefault("google", google)
sys.modules.setdefault("google.genai", genai)

spec = importlib.util.spec_from_file_location(
    "gemini_rewriter_under_test", SCRIPTS / "gemini_rewriter.py"
)
rewriter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rewriter)


class GeminiSourceOverlapTests(unittest.TestCase):
    def test_rejects_long_copied_passage(self):
        source = "The new device was tested across several demanding workloads and showed measurable gains in sustained performance while battery life remained stable throughout the entire evaluation period."
        self.assertTrue(rewriter._has_substantial_source_overlap(source, source))

    def test_accepts_independently_written_summary(self):
        source = "The new device was tested across several demanding workloads and showed measurable gains in sustained performance while battery life remained stable throughout the entire evaluation period."
        rewritten = "Testing found faster sustained performance without a battery-life penalty. The report does not provide enough data to predict results in every workload."
        self.assertFalse(rewriter._has_substantial_source_overlap(rewritten, source))

    def test_short_shared_phrase_is_not_a_blocker(self):
        source = "The new device was tested across several demanding workloads and showed measurable gains in sustained performance while battery life remained stable throughout the entire evaluation period."
        rewritten = "The new device was tested across several demanding workloads. Its battery behavior needs more testing before drawing a broader conclusion."
        self.assertFalse(rewriter._has_substantial_source_overlap(rewritten, source))

    def test_validator_no_longer_demotes_source_copy(self):
        finding = "Writer copied a near-verbatim passage from the source"
        self.assertEqual(rewriter._blocking_findings({"remainingBoilerplate": [finding]}), {"remainingBoilerplate": [finding]})


class GeminiRewriteSafetyTests(unittest.TestCase):
    def rewrite(self, writer_blocks):
        source_blocks = [
            {"type": "heading", "level": "h2", "text": "Background"},
            {"type": "paragraph", "text": "The source describes a new chip for laptop computers and explains that its battery performance held steady during demanding tests across several hours of continuous use."},
            {"type": "paragraph", "text": "The report also includes a comparison with last year's laptop and describes faster work in a wide range of applications without giving a precise percentage."},
        ]
        writer = {
            "title": "Laptop chip report", "seoTitle": "Laptop chip report",
            "excerpt": "An independent summary of a laptop chip report.",
            "seoDescription": "An independent summary of a laptop chip report.",
            "seoKeywords": ["laptop chip", "battery performance", "computer processor"],
            "contentType": "news", "secondaryCategories": [], "tags": [],
            "featured": False, "trending": False, "readingTime": 2,
            "blocks": writer_blocks, "images": [],
        }
        validation = {"remainingBoilerplate": [], "unsupportedClaims": [], "warnings": []}
        with patch.object(rewriter, "CONTENT_TYPE_LOCK", "news"), \
             patch.object(rewriter, "load_api_keys", return_value=["fake"]), \
             patch.object(rewriter, "load_category_prompt", return_value=""), \
             patch.object(rewriter, "sanitize_source_blocks", side_effect=lambda blocks, url: blocks), \
             patch.object(rewriter, "_structured_input_is_reliable", return_value=True), \
             patch.object(rewriter, "_request_with_rotation", side_effect=[writer, validation]):
            return rewriter.rewrite_article(
                "Laptop chip report", "https://example.com/source", source_blocks, "news"
            )

    def test_non_review_drop_is_honored(self):
        _, _, blocks, _ = self.rewrite([
            {"id": 0, "type": "heading", "level": "h2", "text": "Background", "drop": False},
            {"id": 1, "type": "paragraph", "level": "none", "text": "", "drop": True},
            {"id": 2, "type": "paragraph", "level": "none", "text": "Independent analysis notes the reported battery result has limits.", "drop": False},
        ])
        self.assertEqual(len(blocks), 2)
        self.assertNotIn("The source describes", " ".join(block["text"] for block in blocks))

    def test_empty_retained_block_rejects_article(self):
        with self.assertRaisesRegex(RuntimeError, "empty retained block"):
            self.rewrite([
                {"id": 0, "type": "heading", "level": "h2", "text": "Background", "drop": False},
                {"id": 1, "type": "paragraph", "level": "none", "text": "", "drop": False},
                {"id": 2, "type": "paragraph", "level": "none", "text": "Independent analysis notes the reported battery result has limits.", "drop": False},
            ])


class GeminiSEOKeywordContractTests(unittest.TestCase):
    def test_gadget_prompt_requires_primary_product_match(self):
        prompt = rewriter._build_prompt(
            "Gadget news", "Kindle Click launches", "https://www.engadget.com/example",
            "gadgets", None, "structured", [], [], "",
            [{"id": "B0H8TBGMBJ", "name": "Kindle Click", "asin": "B0H8TBGMBJ"}],
        )
        self.assertIn("primary product covered by the article", prompt)
        self.assertIn("For a roundup without one primary product, set every candidate to false", prompt)
        self.assertIn("exactly one decision per affiliateCandidates id", prompt)

    def test_response_schema_requires_keywords_and_caps_them_at_ten(self):
        keywords = rewriter.RESPONSE_SCHEMA["properties"]["seoKeywords"]
        self.assertIn("seoKeywords", rewriter.RESPONSE_SCHEMA["required"])
        self.assertNotIn("minItems", keywords)
        self.assertEqual(keywords["maxItems"], 10)

    def test_keywords_are_trimmed_and_deduplicated_case_insensitively(self):
        result = {
            "seoKeywords": [
                "  iPhone Duo preorder deals  ",
                "iphone duo PREORDER deals",
                "Apple foldable phone discounts",
                "iPhone trade-in offers",
            ]
        }
        self.assertEqual(
            rewriter._normalized_seo_keywords(result),
            [
                "iPhone Duo preorder deals",
                "Apple foldable phone discounts",
                "iPhone trade-in offers",
            ],
        )

    def test_contract_requires_three_distinct_keywords(self):
        result = {
            "title": "Title",
            "seoTitle": "SEO title",
            "excerpt": "Excerpt",
            "seoDescription": "SEO description",
            "seoKeywords": [
                "AI laptops",
                "AI laptop deals",
                "AI laptop buying advice",
            ],
        }
        self.assertEqual(rewriter._writer_contract_issues(result), [])

    def test_contract_rejects_missing_or_too_few_keywords(self):
        result = {
            "title": "Title",
            "seoTitle": "SEO title",
            "excerpt": "Excerpt",
            "seoDescription": "SEO description",
            "seoKeywords": ["AI laptops", "ai laptops"],
        }
        self.assertTrue(
            any("seoKeywords" in issue for issue in rewriter._writer_contract_issues(result))
        )
        del result["seoKeywords"]
        self.assertTrue(
            any("seoKeywords" in issue for issue in rewriter._writer_contract_issues(result))
        )

    def test_affiliate_decisions_require_every_candidate_and_unknowns_are_ignored(self):
        candidates = [
            {"id": "B0F7K3FQN1", "name": "Samsung Galaxy Z Fold 7", "asin": "B0F7K3FQN1"},
            {"id": "B000000000", "name": "Unrelated accessory", "asin": "B000000000"},
        ]
        self.assertIsNone(rewriter._matched_affiliate_decisions(candidates, {
            "affiliateProducts": [{"id": "B0F7K3FQN1", "related": True}]
        }))
        decisions = rewriter._matched_affiliate_decisions(candidates, {
            "affiliateProducts": [
                {"id": "B0F7K3FQN1", "related": True},
                {"id": "B000000000", "related": False},
                {"id": "UNKNOWN", "related": True},
            ]
        })
        self.assertEqual([item["related"] for item in decisions], [True, False])


if __name__ == "__main__":
    unittest.main()
