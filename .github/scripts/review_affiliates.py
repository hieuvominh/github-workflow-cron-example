"""TechRadar review-product discovery; never publish source tracking links."""

import re
import urllib.parse
from decimal import Decimal, InvalidOperation

from lxml import html as lxml_html


ASIN = re.compile(r"^[A-Z0-9]{10}$")
PRODUCT_PATH = re.compile(r"^/(?:dp|gp/product)/([A-Z0-9]{10})(?:/|$)", re.I)
NAME_LABEL = re.compile(r"^View\s+(.+?)\s+on\s+Amazon$", re.I)
USD_PRICE = re.compile(r"^\$\s*([\d,]+(?:\.\d{2})?)$")
ASSOCIATE_TAG = "byterminal-20"


def amazon_affiliate_url(asin):
    asin = str(asin or "").upper()
    if not ASIN.fullmatch(asin):
        raise ValueError("Invalid Amazon ASIN")
    return f"https://www.amazon.com/dp/{asin}?tag={ASSOCIATE_TAG}"


def affiliate_product_payload(decisions):
    """Build the CMS field from Gemini-approved matches only."""
    return [
        {
            "name": item["name"],
            "merchant": "amazon.com",
            "asin": item["asin"],
            "affiliateUrl": amazon_affiliate_url(item["asin"]),
            "price": (
                float(item["sourceObservedPrice"]["amount"])
                if item.get("sourceObservedPrice") else None
            ),
            "currency": (
                item["sourceObservedPrice"]["currency"]
                if item.get("sourceObservedPrice") else None
            ),
        }
        for item in decisions
        if item.get("related") is True
    ]


def _amazon_product_url(link):
    parsed = urllib.parse.urlsplit(link)
    host = (parsed.hostname or "").lower()
    if host == "target.georiot.com":
        target = urllib.parse.parse_qs(parsed.query).get("GR_URL", [""])[0]
        parsed = urllib.parse.urlsplit(target)
        host = (parsed.hostname or "").lower()
    if host not in {"amazon.com", "www.amazon.com"} or parsed.scheme != "https":
        return None
    match = PRODUCT_PATH.match(parsed.path)
    return match.group(1).upper() if match else None


def _product_name(anchor):
    label = anchor.get("aria-label") or ""
    match = NAME_LABEL.match(label.strip())
    if match:
        return match.group(1).strip()
    for image in anchor.xpath(".//img[@alt]"):
        name = (image.get("alt") or "").strip()
        if name and not name.lower().startswith(("amazon", "view ")):
            return name
    return ""


def _observed_price(anchor, asin):
    """Price snapshot from TechRadar's card, not a live Amazon quote."""
    cards = anchor.xpath(
        "ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' hawk-grid-item-container ') "
        "or contains(concat(' ', normalize-space(@class), ' '), ' hawk-multimodel-review-items-grid-item ')][1]"
    )
    if not cards:
        return None
    for price in cards[0].xpath(".//span[@data-type='retail']//span[contains(@class, 'hawk-display-price-price')]"):
        parent = price.xpath("ancestor::a[@data-aps-asin][1]")
        if not parent or (parent[0].get("data-aps-asin") or "").upper() != asin:
            continue
        match = USD_PRICE.fullmatch(" ".join(price.itertext()).strip())
        if not match:
            continue
        try:
            amount = Decimal(match.group(1).replace(",", ""))
        except InvalidOperation:
            continue
        if amount > 0:
            return {"amount": str(amount), "currency": "USD", "source": "techradar_widget"}
    return None


def extract_techradar_amazon_candidates(downloaded, source_url):
    """Return distinct Amazon products from TechRadar Hawk commerce widgets only."""
    host = (urllib.parse.urlsplit(source_url).hostname or "").lower()
    if host not in {"techradar.com", "www.techradar.com"}:
        return []
    document = lxml_html.fromstring(downloaded)
    candidates = {}
    for anchor in document.xpath("//a[contains(concat(' ', normalize-space(@class), ' '), ' hawk-affiliate-link-button ') or contains(concat(' ', normalize-space(@class), ' '), ' hawk-affiliate-link-container ')]"):
        if not anchor.xpath("ancestor::*[contains(concat(' ', normalize-space(@class), ' '), ' ecom-root ') or contains(@class, 'hawk-')]"):
            continue
        link = anchor.get("href") or anchor.get("data-url") or ""
        asin = _amazon_product_url(link)
        attribute_asin = (anchor.get("data-aps-asin") or "").upper()
        if not asin or (attribute_asin and attribute_asin != asin):
            continue
        name = _product_name(anchor)
        if not name:
            continue
        if asin not in candidates:
            candidates[asin] = {"id": asin, "name": name, "asin": asin}
        observed_price = _observed_price(anchor, asin)
        if observed_price and "sourceObservedPrice" not in candidates[asin]:
            candidates[asin]["sourceObservedPrice"] = observed_price
    return list(candidates.values())[:20]
