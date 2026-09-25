"""Focused contract checks for Gemini editorial metadata.

Run with: python .github/scripts/test_gemini_contract.py
"""

import importlib.util
import pathlib
import sys
import types
import unittest


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


class GeminiSEOKeywordContractTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
