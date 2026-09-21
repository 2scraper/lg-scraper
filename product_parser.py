"""
product_parser.py
------------------
Extraction for lg.com category (PLP) pages. **This module IS the site** —
every other module here is family core with a handful of named constants.

What the captures actually say, answered from the dumps rather than from
expectation. Sources: three rendered browser captures kept by the previous
version of this scraper (2026-08-13 and 2026-08-23) and nine plain-HTTP
captures taken 2026-09-21 — `/ru/televisions` pages 1-3, `/ru/refrigerators`,
`/ua/televisions`, `/uk/tvs`, `/us/tvs`, and two responses from the site's
own catalogue API.

1. **There is no JSON-LD on this platform.** Zero `application/ld+json`
   blocks on every `/ru` and `/ua` capture. The previous version of this file
   tried JSON-LD first: dead code that could never return a row.

2. **The page publishes its own catalogue API, in a form element.**

       <form id="categoryFilterForm"
             action="/ru/mkt/ajax/category/retrieveCategoryProductList"
             data-price-sync-url="/ru/mkt/ajax/priceSync/retrievePlpPriceSyncList"
             method="post">
         <input name="categoryId" value="CT20206007">
         <input name="page" value="1">  <input name="modelStatusCode" value="ACTIVE">
         <input name="bizType" value="B2C">  <input name="filterFlag" value="Y">

   A plain POST with those fields returns JSON: 12 products per page, 265
   fields each, and a `pageInfo` block — 50 products in 5 pages for RU
   televisions on 2026-09-21. No browser, no key. That is the primary path,
   and `parse_catalog_form()` reads the parameters out of the page rather
   than hardcoding them. Mind the two counts: see `api_row_count()`.

3. **`?page=N` on the category URL works too**, and the browser engines use
   it: pages 1, 2 and 3 each served a different set of grid models.

4. **The grid is 12 products; the page shows more, and the extras are
   traps.** Measured on `/ru/televisions`:
     * `.products-list-group` — a recommendation rail of 7 products that is
       IDENTICAL on pages 1, 2 and 3. Read as products, it adds seven
       duplicates per page, and the run then reports "no new products" and
       stops early.
     * one more "product" inside the grid whose every value is a template
       placeholder (`*modelName*`, `*priceValue*`, `*reviewRating*`) — the
       site's own unfilled row template.
   So extraction is scoped to `.product-list-box`, and any value wrapped in
   asterisks is discarded.

5. **Each card carries schema.org MICRODATA**, which is the durable anchor:
   `itemtype="http://schema.org/Product"` with `itemprop` name/image and an
   `Offer` carrying `priceCurrency` (RUB on /ru) and `price`. Alongside it
   the site's own `data-model-*` attributes carry the model code, the year
   and the review counters. Both beat matching a CSS class, and the previous
   version matched neither — it collected product LINKS by URL shape and
   then picked the longest remaining string on the tile as the title.

6. **There is no price, and the site's own price service says so.** `msrp`,
   `promotionPrice`, `obsSellingPrice`, `obsOriginalPrice`, `rPrice`,
   `cheaperPrice` and `discountedRate` are 0 or null on all 36 products
   measured across RU pages 1-2 and UA page 1 — and calling
   `retrievePlpPriceSyncList`, the endpoint the page uses for nothing but
   prices, returns the same zeros. The microdata says `price = 0` with
   `priceCurrency = RUB`. A published 0 is not a price, so `price` stays
   None while `currency` records what the page declares.

7. **Two locales, by the site's own reckoning.** The hreflang set on
   `/ru/televisions` has exactly two entries: `ru-ru` and `ru-ua`. `/ua`
   serves the same platform and the same attributes. `/uk` is a DIFFERENT
   platform — no `data-model-*` anywhere, JSON-LD instead — and `/us`
   answered 403 from Akamai to the same request that `/ru` served normally.
   So this parser supports `ru` and `ua`, and says why for anything else.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

from bs4 import BeautifulSoup

from output_writer import Product

logger = logging.getLogger("product_parser")

SELECTORS = {
    # The product grid, and nothing else on the page. The recommendation rail
    # (`.products-list-group`) carries the same 7 products on every page —
    # see the module docstring.
    "grid": ".product-list-box",
    "item_card": ".product-list-box .item",
}

# The locales this platform serves, taken from the site's own hreflang set
# rather than guessed, and then checked: both answer with `data-model-*`
# cards and the same catalogue API.
SUPPORTED_LOCALES = ("ru", "ua")

# What a full page of the grid holds. The API ignores the form's `length`
# field (sending 9 or 36 both returned 12), so this is a property of the
# server, not a request parameter.
PAGE_SIZE = 12

# The site's own unfilled template row. Every value in it is wrapped in
# asterisks, and it sits inside the grid on every page.
_TEMPLATE_VALUE_RE = re.compile(r"^\*.*\*$")

_CATALOG_FORM_ID = "categoryFilterForm"
# Fields of that form which the catalogue API actually needs. The form also
# carries dozens of filter facets (FT…=FV…) and UI strings; sending those
# back would reproduce whatever filter the page happened to render with.
_CATALOG_FIELDS = ("v", "categoryId", "modelStatusCode", "bizType", "viewAll",
                   "filterFlag", "length", "sort", "sortByFlag")

# ---------------------------------------------------------------------------
# Blocked-page detection
# ---------------------------------------------------------------------------
# lg.com sits behind Akamai, and this is the clearest case in the family of
# why a marker has to be COUNTED ON A PAGE YOU KNOW IS GOOD. Measured
# 2026-09-21 on `/ru/televisions` (364 KB, full grid) and `/ua/televisions`,
# against the 403 that `/us/tvs` returned to the same client:
#
#     marker                                good ru   good ua   the 403
#     "akamai"                                    1         1         0
#     "Access Denied"                             0         0         2
#     "edgesuite"                                 0         0         1
#     "You don't have permission to access"       0         0         1
#
# The vendor's own NAME is on every good page and absent from the block. A
# marker set built from "akamai" would have reported exit 3 on every
# successful run and exit 4 on the real refusal — both wrong, in opposite
# directions.
BOT_CHALLENGE_MARKERS = {
    "akamai": ("Access Denied", "edgesuite",
               "You don't have permission to access"),
}

# Every page the site serves is built out of its own asset path. An
# interstitial, and Chromium's own network-error page, are not: measured 83
# and 72 references on the two good pages, 0 on the 403.
_ASSET_REFERENCE_RE = re.compile(r"/lg5-common-gp/|/[a-z]{2}/images/")
MIN_ASSET_REFERENCES = 5

_EXTENSION_SCRIPT_RE = re.compile(
    r"<script\b[^>]*(?:chrome|moz)-extension://[^>]*>\s*</script>", re.IGNORECASE)


def strip_extension_scripts(html: str) -> str:
    """Remove browser-extension <script> tags, whole, before scanning markers."""
    return _EXTENSION_SCRIPT_RE.sub("", html)


def detect_bot_challenge(html: str) -> Optional[str]:
    """Vendor name if `html` is a refusal page rather than content, else None."""
    cleaned = strip_extension_scripts(html)
    for vendor, markers in BOT_CHALLENGE_MARKERS.items():
        if any(marker in cleaned for marker in markers):
            return vendor
    return None


def asset_reference_count(html: str) -> int:
    """How many times the response references LG's own asset paths."""
    return len(_ASSET_REFERENCE_RE.findall(html))


# The machinery a category page carries whether or not it holds products:
# measured once each on a good /ru/televisions page AND on page 99, which is
# past the end of that category and holds no cards at all. A response with
# the assets but NONE of this is the shell, still to paint — that difference
# is what keeps a slow render from being reported as an empty category.
_CATEGORY_MACHINERY = ("categoryFilterForm", "product-list-box")


def has_category_machinery(html: str) -> bool:
    """Whether the response carries the category grid's own machinery."""
    return any(marker in html for marker in _CATEGORY_MACHINERY)


def count_cards(html: str) -> int:
    """Grid products in `html` — the readiness marker the engines wait on."""
    soup = BeautifulSoup(html, "html.parser")
    return len(_grid_cards(soup))


# ---------------------------------------------------------------------------
# URLs: locale, category, pagination
# ---------------------------------------------------------------------------
def locale_from_url(url: str) -> Optional[str]:
    """The locale segment of an lg.com URL, or None."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    return parts[0].lower() if parts else None


def unsupported_locale_reason(url: str) -> Optional[str]:
    """Why this URL cannot be scraped, or None when it can.

    Refusing WITH THE REASON matters more than refusing: "not an LG site" is
    false and sends the reader hunting for a typo. These two are different
    platforms wearing the same domain.
    """
    host = (urlparse(url).hostname or "").lower()
    if host and not host.endswith("lg.com"):
        return f"{host} is not lg.com"
    locale = locale_from_url(url)
    if locale is None:
        return ("the URL carries no locale segment — this scraper needs a "
                "category page such as https://www.lg.com/ru/televisions")
    if locale in SUPPORTED_LOCALES:
        return None
    return (f"lg.com/{locale} runs a different platform from lg.com/ru and "
            f"lg.com/ua: it serves no data-model-* attributes and no "
            f"categoryFilterForm, so nothing here can read it. Measured "
            f"2026-09-21 on /uk (a different markup generation) and /us "
            f"(403 from Akamai to the same request /ru served). The site's "
            f"own hreflang set on /ru/televisions lists exactly ru-ru and "
            f"ru-ua.")


_PAGE_PARAM = "page"


def page_url(url: str, page_num: int) -> str:
    """`url` with LG's own `?page=` parameter set to `page_num`.

    Measured 2026-09-21: pages 1, 2 and 3 of `/ru/televisions` each served a
    different set of grid models, so the convention is real rather than
    ignored. Page 1 carries no parameter, which is what the site's own links
    do. Existing query parameters (filters) are preserved.
    """
    parts = urlparse(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() != _PAGE_PARAM]
    if page_num > 1:
        query.append((_PAGE_PARAM, str(page_num)))
    return urlunparse(parts._replace(query=urlencode(query)))


_NOT_A_CATEGORY = {"", "mkt", "ajax"} | set(SUPPORTED_LOCALES)


def category_from_url(url: str) -> Optional[str]:
    """A label for the `category` column, from the URL itself.

    `/ru/televisions` -> "televisions". Returns None rather than inventing a
    label when the path says nothing.
    """
    for segment in reversed([p for p in urlparse(url).path.split("/") if p]):
        if segment.lower() not in _NOT_A_CATEGORY:
            return segment.replace("-", " ").replace("_", " ")
    return None


# ---------------------------------------------------------------------------
# The catalogue API, as the page itself describes it
# ---------------------------------------------------------------------------
def declared_currency(html: str) -> Optional[str]:
    """The currency this page declares, or None.

    Two independent declarations, both the site's own: the microdata's
    `priceCurrency` on each card (RUB on /ru) and a hidden `currencySymbol`
    input (" руб."). The catalogue API's `obsCurrency` is null on every
    record measured, so the API path takes the currency from here rather
    than inventing one from the locale.
    """
    soup = BeautifulSoup(html, "html.parser")
    node = soup.find(attrs={"itemprop": "priceCurrency"})
    if node is not None:
        code = _clean(node.get("content"))
        if code and re.fullmatch(r"[A-Z]{3}", code):
            return code
    return None


def parse_catalog_form(html: str, base_url: str) -> Optional[Dict[str, object]]:
    """The catalogue API call this page describes, or None.

    Returns {"url": …, "price_sync_url": …, "params": {…}} built from the
    page's own `#categoryFilterForm`. Read from the page rather than
    hardcoded: `categoryId` differs per category AND per locale (CT20206007
    for RU televisions, CT20226005 for UA televisions, CT20206048 for RU
    refrigerators), and a hardcoded id would quietly scrape the wrong
    catalogue.
    """
    soup = BeautifulSoup(html, "html.parser")
    form = soup.find("form", id=_CATALOG_FORM_ID)
    if form is None:
        return None
    action = form.get("action")
    if not action:
        return None
    params: Dict[str, str] = {}
    for field in form.find_all("input"):
        name = field.get("name")
        if name in _CATALOG_FIELDS and name not in params:
            params[name] = field.get("value") or ""
    if not params.get("categoryId"):
        return None
    return {
        "url": urljoin(base_url, action),
        "price_sync_url": (urljoin(base_url, form.get("data-price-sync-url"))
                           if form.get("data-price-sync-url") else None),
        "params": params,
    }


def catalog_payload(form: Dict[str, object], page_num: int) -> Dict[str, str]:
    """The POST body for one page of the catalogue API."""
    params = dict(form["params"])           # type: ignore[arg-type]
    params["page"] = str(page_num)
    return params


def _page_info(payload: dict) -> dict:
    """The API's own pagination block, or {}."""
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return {}
    info = data[0].get("pageInfo")
    return info if isinstance(info, dict) else {}


def api_row_count(payload: dict) -> Optional[int]:
    """How many products this category pages through, or None.

    This is `pageInfo.totalCount`, and it is the number that answers "did the
    run get everything?" — 50 for RU televisions and 41 for UA televisions on
    2026-09-21, matching the rows the API actually hands out.

    NOT `totalCount` beside the product list, which is a different number:
    203 for the same RU category, and measured to be the count of SIZE
    VARIANTS across those 50 model groups (the sum of each row's
    `sibling_sizes`, exactly 203). Reading that one as the product count made
    every complete run look like it had lost three quarters of the catalogue.
    """
    total = _page_info(payload).get("totalCount")
    return total if isinstance(total, int) else None


def api_page_count(payload: dict) -> Optional[int]:
    """How many pages the API says this category has, or None.

    `pageInfo.pageCount`: 5 for RU televisions, 4 for UA. Past the end the
    block collapses to `{"view": "N", "pageCount": 0}`, which describes that
    response rather than the category — so a 0 here is read as "unknown",
    never as "no pages".
    """
    count = _page_info(payload).get("pageCount")
    return count if isinstance(count, int) and count > 0 else None


def api_variant_count(payload: dict) -> Optional[int]:
    """The site's count of size variants in this category, or None.

    `totalCount` beside the product list. Kept because it is the number the
    site itself shows, and `sibling_sizes` on the rows adds up to exactly it
    — but it is not a product count, and nothing paginates by it.
    """
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return None
    total = data[0].get("totalCount")
    return total if isinstance(total, int) else None


def api_products(payload: dict) -> List[dict]:
    """The product records in a catalogue API response, or []."""
    data = payload.get("data")
    if not isinstance(data, list) or not data:
        return []
    products = data[0].get("productList")
    return [p for p in products if isinstance(p, dict)] if isinstance(products, list) else []


# ---------------------------------------------------------------------------
# Value helpers
# ---------------------------------------------------------------------------
def _clean(value) -> Optional[str]:
    """Trim a string, discarding the site's unfilled template placeholders."""
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text or _TEMPLATE_VALUE_RE.match(text):
        return None
    return text


def _number(value) -> Optional[float]:
    text = _clean(value)
    if text is None:
        return None
    match = re.search(r"-?\d+(?:[.,]\d+)?", text.replace(" ", "").replace(" ", ""))
    if not match:
        return None
    try:
        return float(match.group().replace(",", "."))
    except ValueError:
        return None


def _positive_price(value) -> Optional[float]:
    """A price, or None for the zero this platform publishes everywhere.

    A published 0 is not a price. Reporting it as one would put "0" in a
    price-monitoring column and make every product look free — and the
    measurement behind this is in the module docstring: the site's own price
    service returns zeros for every product on both locales.
    """
    amount = _number(value)
    return amount if amount and amount > 0 else None


def _rating(value) -> Optional[float]:
    score = _number(value)
    return score if score and score > 0 else None


def _int(value) -> Optional[int]:
    amount = _number(value)
    return int(amount) if amount is not None else None


# ---------------------------------------------------------------------------
# The primary path: the site's own catalogue API
# ---------------------------------------------------------------------------
def _sales_code(code, suffix) -> Optional[str]:
    """`salesModelCode` with its market suffix, as the card's attribute has it."""
    base, tail = _clean(code), _clean(suffix)
    if base and tail:
        return f"{base}.{tail}"
    return base


def _row_from_api(record: dict, base_url: str, category: Optional[str],
                  page: Optional[int], position: Optional[int]) -> Product:
    """One Product from one record of the catalogue API's productList."""
    sku = _clean(record.get("modelName"))
    url_path = _clean(record.get("modelUrlPath"))
    image = _clean(record.get("mediumImageAddr")) or _clean(record.get("smallImageAddr"))

    siblings = record.get("siblingModels")
    sibling_sizes = None
    if isinstance(siblings, list) and siblings:
        values = [_clean(s.get("siblingValue")) or _clean(s.get("siblingCode"))
                  for s in siblings if isinstance(s, dict)]
        values = [v for v in values if v]
        sibling_sizes = ", ".join(dict.fromkeys(values)) or None

    return Product(
        url=urljoin(base_url, url_path) if url_path else "",
        sku=sku,
        title=_clean(record.get("userFriendlyName")) or sku,
        # Every price field this platform publishes is 0 — see the module
        # docstring, and _positive_price for why 0 is not written as a price.
        price=(_positive_price(record.get("obsSellingPrice"))
               or _positive_price(record.get("promotionPrice"))
               or _positive_price(record.get("msrp"))),
        currency=_clean(record.get("obsCurrency")),
        original_price=_positive_price(record.get("obsOriginalPrice")),
        discount_pct=_number(record.get("discountedRate")),
        category=category,
        price_source="api",
        page=page,
        position=position,
        model_id=_clean(record.get("modelId")),
        # The DOM writes this as one string with the market suffix appended
        # (`OLED83W69LA.ARUG`) while the API splits it in two. Composed here
        # so the same product reads identically whichever path produced it.
        sales_model_code=_sales_code(record.get("salesModelCode"),
                                     record.get("salesSuffixCode")),
        model_year=_int(record.get("modelYear")),
        screen_size=_clean(record.get("inchCode")),
        sibling_sizes=sibling_sizes,
        product_category=_clean(record.get("categoryName")),
        product_category_slug=_clean(record.get("categoryEngName")),
        super_category=_clean(record.get("superCategoryName")),
        rating=_rating(record.get("reviewRatingStar2")) or _rating(record.get("reviewRating")),
        review_count=_int(record.get("reviewCount")) or 0,
        status=_clean(record.get("modelStatusCode")),
        energy_label=_clean(record.get("energyLabel")),
        image_url=urljoin(base_url, image) if image else None,
        where_to_buy_url=(urljoin(base_url, _clean(record.get("whereToBuyUrl")))
                          if _clean(record.get("whereToBuyUrl")) else None),
    )


def rows_from_api(payload: dict, base_url: str, category: Optional[str] = None,
                  page: Optional[int] = None,
                  currency: Optional[str] = None) -> List[Product]:
    """Rows from one catalogue API response.

    `currency` is what the category page declared (see `declared_currency`):
    the API's own `obsCurrency` is null on every record measured, and the
    column should carry what the site says rather than nothing.
    """
    rows = []
    for position, record in enumerate(api_products(payload), start=1):
        if not _clean(record.get("modelName")):
            logger.warning("Skipping an API record with no modelName "
                           "(position %d).", position)
            continue
        row = _row_from_api(record, base_url, category, page, position)
        if row.currency is None:
            row.currency = currency
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# The fallback path: the rendered grid
# ---------------------------------------------------------------------------
def _grid_cards(soup) -> List:
    """The grid's product cards, and nothing from the recommendation rail.

    Scoped to `.product-list-box`: the rail (`.products-list-group`) carries
    the SAME seven products on every page, so a page-wide sweep would add
    seven duplicates per page and then report the listing as exhausted.
    """
    cards = []
    for grid in soup.select(SELECTORS["grid"]):
        for card in grid.select(".item"):
            name_node = card.find(attrs={"data-model-name": True})
            name = _clean(name_node.get("data-model-name")) if name_node else None
            if name:                       # None discards the template row
                cards.append(card)
    return cards


def _card_attr(card, *names: str) -> Optional[str]:
    """The first of `names` found on any descendant of `card`."""
    for name in names:
        node = card.find(attrs={name: True})
        if node is not None:
            value = _clean(node.get(name))
            if value:
                return value
    return None


def _itemprop(card, prop: str) -> Optional[str]:
    """A schema.org microdata value from the card.

    Microdata is the standards-based anchor here, and it survives the class
    churn that a Tailwind-style build produces: `itemprop="name"`,
    `itemprop="image"`, and an `Offer` carrying `priceCurrency` and `price`.
    """
    node = card.find(attrs={"itemprop": prop})
    if node is None:
        return None
    for attribute in ("content", "href", "src"):
        value = _clean(node.get(attribute))
        if value:
            return value
    return _clean(node.get_text(" ", strip=True))


def _row_from_card(card, base_url: str, category: Optional[str],
                   page: Optional[int], position: Optional[int]) -> Product:
    sku = _card_attr(card, "data-model-name", "data-adobe-modelname")
    url_path = _itemprop(card, "url")
    if not url_path:
        link = card.find("a", href=True)
        url_path = link["href"] if link else None

    # The size switcher lists every sibling; the one this card IS carries
    # `active`/aria-checked. Taking the first link instead reported an 85"
    # card as the 65" model on the very first real page — the sizes are
    # listed largest-first, not current-first.
    # Two columns the API fills and the card carries too, measured identical
    # on all 12 cards of /ru/televisions?page=2 on 2026-09-21. Left null
    # before that, which read as "the site does not publish this" when the
    # site publishes it on every card.
    where_to_buy = card.select_one("a.where-to-buy")
    where_to_buy_url = where_to_buy.get("href") if where_to_buy else None

    sizes = [_clean(a.get_text()) for a in card.select(".model-group a")]
    sizes = [s for s in sizes if s]
    active = card.select_one('.model-group a.active, .model-group a[aria-checked="true"]')
    active_size = _clean(active.get_text()) if active is not None else None

    return Product(
        url=urljoin(base_url, url_path) if url_path else "",
        sku=sku,
        title=_itemprop(card, "name") or sku,
        # The microdata publishes price="0" with priceCurrency="RUB"; a
        # published zero is not a price.
        price=_positive_price(_itemprop(card, "price")),
        currency=_itemprop(card, "priceCurrency"),
        category=category,
        price_source="dom",
        page=page,
        position=position,
        model_id=_card_attr(card, "data-model-id", "data-wish-model-id"),
        sales_model_code=_card_attr(card, "data-model-salesmodelcode",
                                    "data-adobe-salesmodelcode"),
        model_year=_int(_card_attr(card, "data-model-year")),
        screen_size=(active_size.rstrip('"') if active_size else None),
        sibling_sizes=", ".join(dict.fromkeys(sizes)) or None if sizes else None,
        rating=_rating(_card_attr(card, "data-model-overallscore")),
        review_count=_int(_card_attr(card, "data-model-reviewcnt")) or 0,
        image_url=(urljoin(base_url, _itemprop(card, "image"))
                   if _itemprop(card, "image") else None),
        super_category=_card_attr(card, "data-super-category-name"),
        where_to_buy_url=(urljoin(base_url, where_to_buy_url)
                          if where_to_buy_url else None),
    )


def rows_from_dom(html: str, base_url: str, category: Optional[str] = None,
                  page: Optional[int] = None) -> List[Product]:
    soup = BeautifulSoup(html, "html.parser")
    rows, seen = [], set()
    position = 0
    for card in _grid_cards(soup):
        name_node = card.find(attrs={"data-model-name": True})
        sku = _clean(name_node.get("data-model-name")) if name_node else None
        if not sku or sku in seen:
            continue
        seen.add(sku)
        position += 1
        rows.append(_row_from_card(card, base_url, category, page, position))
    return rows


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------
def parse_products(html: str, base_url: str, category: Optional[str] = None,
                   page: Optional[int] = None) -> List[Product]:
    """Rows for one category page, from the rendered grid.

    The engines call this with whatever HTML their browser holds. The
    catalogue API path (`rows_from_api`) is richer — it carries the category
    names, the sibling sizes and the site's own totalCount — and
    `scraper_api_client.py` uses it; `price_source` on every row says which
    one produced it, so two runs that read the page differently cannot look
    like a data change.
    """
    rows = rows_from_dom(html, base_url, category, page)
    if not rows:
        logger.info("No grid products in this response. On this site that is "
                    "either a page past the end of the category or a page "
                    "that has not painted — page_flow.classify tells them "
                    "apart.")
    return rows
