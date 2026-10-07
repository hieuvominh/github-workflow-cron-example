"""Manual review-product discovery; never publish source tracking links."""

import re
import urllib.parse
import urllib.error
import urllib.request
from decimal import Decimal, InvalidOperation

from lxml import html as lxml_html


ASIN = re.compile(r"^[A-Z0-9]{10}$")
NAMED_PRODUCT_PATH = re.compile(r"^/(?:[^/]+/)?(?:dp|gp/product)/([A-Z0-9]{10})(?:/|$)", re.I)
NAME_LABEL = re.compile(r"^View\s+(.+?)\s+on\s+Amazon$", re.I)
USD_PRICE = re.compile(r"^\$\s*([\d,]+(?:\.\d{2})?)$")
AMAZON_BUTTON_PRICE = re.compile(r"\$\s*([\d,]+(?:\.\d{2})?)\s+(?:at|on)\s+Amazon\b", re.I)
ASSOCIATE_TAG = "byterminal-20"
REVIEW_HOSTS = {"theverge.com", "ign.com", "cnet.com", "techguide.com.au"}
SHORT_LINK_HOSTS = {"zdcs.link", "amzn.to", "r.zdbb.net"}
REDIRECT_HOSTS = SHORT_LINK_HOSTS | {"cc.ign.com", "cc.cnet.com", "amazon.com", "www.amazon.com"}


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


def _amazon_target_url(link):
    try:
        parsed = urllib.parse.urlsplit(link)
        if parsed.username or parsed.password or parsed.port not in {None, 443}:
            return None
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https":
        return None
    if host in {"target.georiot.com", "cc.ign.com", "cc.cnet.com"}:
        field = "GR_URL" if host == "target.georiot.com" else "url"
        target = urllib.parse.parse_qs(parsed.query).get(field, [""])[0]
        try:
            parsed = urllib.parse.urlsplit(target)
            if parsed.username or parsed.password or parsed.port not in {None, 443}:
                return None
        except ValueError:
            return None
        host = (parsed.hostname or "").lower()
    if host not in {"amazon.com", "www.amazon.com"} or parsed.scheme != "https":
        return None
    return urllib.parse.urlunsplit(parsed)


def _amazon_product_url(link):
    target = _amazon_target_url(link)
    if not target:
        return None
    parsed = urllib.parse.urlsplit(target)
    match = NAMED_PRODUCT_PATH.match(parsed.path)
    return match.group(1).upper() if match else None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        return None


def _resolve_short_link(link):
    """Read only redirect headers; never follow an unknown host or fetch a product page."""
    opener = urllib.request.build_opener(_NoRedirect)
    current = link
    for _ in range(3):
        try:
            parsed = urllib.parse.urlsplit(current)
            if parsed.username or parsed.password or parsed.port not in {None, 443}:
                return None
        except ValueError:
            return None
        if parsed.scheme != "https" or parsed.hostname not in REDIRECT_HOSTS:
            return None
        if _amazon_product_url(current):
            return current
        if parsed.hostname in {"cc.ign.com", "cc.cnet.com"}:
            return None
        request = urllib.request.Request(
            current, headers={"User-Agent": "Mozilla/5.0"}, method="HEAD"
        )
        try:
            with opener.open(request, timeout=10) as response:
                return response.geturl() if _amazon_product_url(response.geturl()) else None
        except urllib.error.HTTPError as error:
            if error.code not in {301, 302, 303, 307, 308}:
                return None
            location = error.headers.get("Location")
            if not location:
                return None
            current = urllib.parse.urljoin(current, location)
        except (OSError, ValueError):
            return None
    return None


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


def _editorial_anchor(anchor):
    """Keep commerce links inside the article, not sidebar or related-story UI."""
    in_article = False
    for node in anchor.iterancestors():
        if node.tag in {"article", "main"}:
            in_article = True
        if node.tag in {"nav", "footer", "aside"}:
            return False
        classes = (node.get("class") or "").lower()
        if any(marker in classes for marker in (
            "related-articles", "related-stories", "recommended", "most-popular",
            "author-bio", "author-box", "post-author", "social-share", "share-buttons",
        )):
            return False
    return in_article


def _review_product_name(anchor, amazon_link):
    for node in anchor.iterancestors():
        classes = (node.get("class") or "").lower().split()
        if "duet--article--scorecard" in classes:
            headings = node.xpath(".//h3 | .//h2")
        elif "zd-product-review-card" in classes:
            headings = node.xpath(".//*[contains(@class, 'zd-product-review-card__title')]")
        elif "product-card" in classes:
            headings = node.xpath(".//h3[contains(@class, 'name')] | .//h2[contains(@class, 'name')]")
        else:
            continue
        for heading in headings:
            name = " ".join(heading.itertext()).strip()
            if name:
                return " ".join(name.split())[:150]

    label = (anchor.get("aria-label") or "").strip()
    match = NAME_LABEL.match(label)
    if match:
        return match.group(1).strip()[:150]
    text = " ".join(" ".join(anchor.itertext()).split())
    if text and len(text) >= 6 and not re.search(
        r"\b(?:amazon|see it|view deal|buy now|shop now|check price)\b", text, re.I
    ) and not text.startswith("$"):
        return text[:150]

    path = urllib.parse.urlsplit(_amazon_target_url(amazon_link) or "").path
    match = re.search(r"/([^/]+)/dp/[A-Z0-9]{10}(?:/|$)", path, re.I)
    if match:
        slug = urllib.parse.unquote(match.group(1)).replace("-", " ").replace("_", " ")
        if len(slug) >= 6:
            return " ".join(slug.split())[:150]
    return None


def _review_button_price(anchor, source_host):
    text = " ".join(" ".join(anchor.itertext()).split())
    match = AMAZON_BUTTON_PRICE.search(text)
    if not match:
        return None
    try:
        amount = Decimal(match.group(1).replace(",", ""))
    except InvalidOperation:
        return None
    if amount <= 0:
        return None
    return {"amount": str(amount), "currency": "USD", "source": f"{source_host}_card"}


def extract_review_amazon_candidates(downloaded, source_url, redirect_resolver=None):
    """Find Amazon.com products in manual reviews from all configured sources."""
    source_host = (urllib.parse.urlsplit(source_url).hostname or "").lower().removeprefix("www.")
    if source_host == "techradar.com":
        return extract_techradar_amazon_candidates(downloaded, source_url)
    if source_host not in REVIEW_HOSTS:
        return []
    document = lxml_html.fromstring(downloaded)
    resolver = redirect_resolver or _resolve_short_link
    resolved = {}
    candidates = {}
    for anchor in document.xpath("//a[@href]"):
        if not _editorial_anchor(anchor):
            continue
        link = urllib.parse.urljoin(source_url, anchor.get("href") or "")
        parsed = urllib.parse.urlsplit(link)
        if parsed.scheme != "https":
            continue
        if parsed.hostname in SHORT_LINK_HOSTS:
            if link not in resolved:
                if len(resolved) >= 25:
                    continue
                resolved[link] = resolver(link)
            amazon_link = resolved[link]
        else:
            amazon_link = link
        if not amazon_link:
            continue
        asin = _amazon_product_url(amazon_link)
        if not asin:
            continue
        name = _review_product_name(anchor, amazon_link)
        if not name:
            continue
        if asin not in candidates:
            if len(candidates) >= 20:
                continue
            candidates[asin] = {"id": asin, "name": name, "asin": asin}
        price = _review_button_price(anchor, source_host)
        if price and "sourceObservedPrice" not in candidates[asin]:
            candidates[asin]["sourceObservedPrice"] = price
    return list(candidates.values())
