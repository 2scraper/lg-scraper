# lg-scraper

[![release](https://img.shields.io/github/v/release/2scraper/lg-scraper?label=release)](https://github.com/2scraper/lg-scraper/releases)
[![tests](https://github.com/2scraper/lg-scraper/actions/workflows/tests.yml/badge.svg)](https://github.com/2scraper/lg-scraper/actions/workflows/tests.yml)
[![canary](https://github.com/2scraper/lg-scraper/actions/workflows/canary.yml/badge.svg)](https://github.com/2scraper/lg-scraper/actions/workflows/canary.yml)
[![python](https://img.shields.io/badge/python-3.9%2B-blue)](pyproject.toml)
[![licence](https://img.shields.io/badge/licence-MIT-green)](LICENSE)
[![engines](https://img.shields.io/badge/engines-catalogue--api%20%7C%20playwright%20%7C%20pyppeteer%20%7C%20selenium-lightgrey)](#four-ways-to-run-it)
[![runs without an account](https://img.shields.io/badge/runs%20without%20an%20account-yes-brightgreen)](#what-the-paid-products-buy-you)

A working scraper for **lg.com** category pages — televisions, refrigerators,
washing machines, monitors, audio, anything with a product grid. No category
is hardcoded: what a run covers is the URL you give it.

Part of the [2scraper](https://github.com/2scraper) family of open-source,
single-site scrapers.

**Scope, stated up front: the `/ru` and `/ua` platforms.** That is not a
preference, it is what the site's own `hreflang` set on `/ru/televisions`
declares — exactly two entries, `ru-ru` and `ru-ua` — and both serve the same
markup and the same catalogue API. `lg.com/uk` is a different markup
generation with no `data-model-*` attributes anywhere, and `lg.com/us`
answered `403 Access Denied` from Akamai to the same client that `/ru`
served, on 2026-09-21. A URL on any other locale is refused **with that
reason**, rather than returning zero rows and letting you hunt for a broken
selector.

## What it extracts

One row per product, 27 columns. The ones that carry the site's own data:

| Column | What it is |
|---|---|
| `sku` | LG's model code, e.g. `OLED83W69LA` — the key `diff_runs.py` compares on |
| `model_id` / `sales_model_code` | The internal id (`MD07610675`) and the code with its market suffix (`OLED83W69LA.ARUG`) |
| `title` | The site's own long product name |
| `url` / `image_url` / `where_to_buy_url` | Canonical product page, primary image, and the "where to buy" link this catalogue offers instead of a checkout |
| `model_year` / `screen_size` / `sibling_sizes` | 2026 / `83` / `83", 77"` — the other sizes of the same model |
| `product_category` / `product_category_slug` / `super_category` | `Телевизоры` / `televisions` / `tv-audio-video` |
| `status` | `ACTIVE` |
| `currency` | `RUB` on `/ru` — what the page's own microdata declares |
| `price` / `original_price` / `discount_pct` | **Null on this platform.** See below |
| `rating` / `review_count` | Null and 0 respectively, on everything measured |
| `energy_label` | The EU energy class where it is published; null on these two locales |
| `page` / `position` | Where the row sat in the run. The PAIR is unique |
| `price_source` | `api` or `dom` — which path produced the row |

Plus `source`, `scraped_at` and `category` (your `--category` label, or the
one taken from the URL). The full row is in
[`sample_output.json`](sample_output.json), cut from a real run.

Two family columns are **absent**, each with its reason: `brand` (lg.com
lists LG's own products and nothing else — the column would read "LG" on
every row) and `in_stock` (a manufacturer catalogue publishes no
availability; `where_to_buy_url` is what it offers instead).

### Why there are no prices, and how that was established

Not by failing to find one. The site publishes a price FIELD and runs a price
SERVICE, and both say zero:

* the card's microdata carries `price="0"` with `priceCurrency="RUB"`;
* the catalogue API's nine numeric price fields — `msrp`, `promotionPrice`,
  `obsSellingPrice`, `obsOriginalPrice`, `rPrice`, `cheaperPrice`,
  `discountedRate` among them — are 0 or null on **all 36 products** measured
  across two locales and two categories on 2026-09-21;
* `retrievePlpPriceSyncList`, the endpoint the page calls for nothing but
  prices, returns the same zeros;
* and each record carries the site's own switch, `priceAreaYn: "N"`.

A published zero is not a price, so the column stays `null` rather than
reporting every LG television as free. The column is kept, rather than
dropped, so the day LG turns prices on shows up in a diff instead of
requiring someone to notice.

## How it reads the page

**The site publishes its own catalogue API, and the page describes the
call.** In the served HTML:

```html
<form id="categoryFilterForm"
      action="/ru/mkt/ajax/category/retrieveCategoryProductList"
      data-price-sync-url="/ru/mkt/ajax/priceSync/retrievePlpPriceSyncList"
      method="post">
  <input name="categoryId" value="CT20206007">
  <input name="page" value="1">
  <input name="modelStatusCode" value="ACTIVE">
  <input name="bizType" value="B2C">
```

A plain POST with those fields returns JSON: **12 products per page, 265
fields each, and the category's own `totalCount`** (203 for RU televisions on
2026-09-21). No browser, no key, no proxy.

1. **Primary path — that API** (`catalog_client.py`). The parameters are read
   off the page every run, never hardcoded: `categoryId` differs per category
   AND per locale, and a hardcoded one would quietly scrape a different
   catalogue than the URL asked for while reporting success.
2. **Fallback path — the rendered grid**, for the browser engines. Each card
   carries schema.org **microdata** (`itemprop` name/image/priceCurrency) and
   the site's own `data-model-*` attributes. Both beat matching a CSS class.
3. `price_source` on every row says which produced it. Verified on real
   captures: the two paths return **the same products and agree on every
   shared column**.

There is no JSON-LD anywhere on this platform — zero blocks on every `/ru`
and `/ua` capture — so a JSON-LD-first parser, which is the family default,
would return nothing here for ever.

### Two things on the page that are not products

Both were measured, and both would corrupt a run quietly:

* **A recommendation rail** (`.products-list-group`) of 7 products, identical
  on pages 1, 2 and 3. Counted as products, every page contributes the same
  seven — and then the run sees "no new products" and stops early, reporting
  a complete result.
* **The site's own unfilled template row** inside the grid, whose every value
  is a placeholder (`*modelName*`, `*priceValue*`). It is the same on every
  page too.

Extraction is scoped to `.product-list-box`, and any value wrapped in
asterisks is discarded.

## Four ways to run it

| Script | Engine | Live status, 2026-09-21 |
|---|---|---|
| `catalog_client.py` | **The site's own API** (recommended) | 36 products / 3 pages / exit 0, no browser and no key |
| `playwright_scraper.py` | Playwright | the rendered grid |
| `puppeteer_scraper.py` | pyppeteer | the rendered grid; needs `PYPPETEER_EXECUTABLE_PATH` on modern macOS |
| `selenium_scraper.py` | Selenium + Chrome | the rendered grid |
| `scraper_api_client.py` | 2Captcha Scraper API | the rendered grid, fetched through someone else's address |

## Install and run

```bash
pip install -r requirements.txt        # core: bs4 + requests

python3 catalog_client.py \
  --url "https://www.lg.com/ru/televisions" --pages 3 --out tv_products
```

That is the whole install for the recommended path — no browser at all. For a
browser engine, add exactly one of `requirements-playwright.txt`,
`requirements-puppeteer.txt` or `requirements-selenium.txt` (their pins are
mutually unsatisfiable; use a virtualenv per engine).

Credentials, when you need any, live in `.env` rather than on a command line:

```bash
cp .env.example .env
python3 env_config.py     # says what was picked up, without printing it
```

## What the paid products buy you

**You need none of them for this site.** Every number in this README was
measured with no API key, no proxy and no browser: the catalogue API answers
a plain HTTPS POST.

What the 2Captcha products are for here:

* **Proxies** (`--proxy`, `--proxy-file`) — the one that genuinely matters on
  this site. `lg.com/us` refused an ordinary request with `403 Access Denied`
  from Akamai while `/ru` served the same client, so an exit in the wrong
  place is the failure mode you will actually meet. Every engine preflights
  its exit before starting.
* **The Scraper API** (`scraper_api_client.py`) — the same idea without
  managing proxies yourself.
* **The Scraping Browser API** (`--cdp-endpoint`) — a real browser somewhere
  else, with `Captcha.setAutoSolve` enabled on connect.
* **Captcha solving** (`--twocaptcha-key`) — the site configures reCAPTCHA v3
  on its **support forms** (`/ru/support/email-to-ceo`) and nothing on a
  category page. This scraper never touches those forms, so the default
  `--solve-captcha when-blocked` will not spend anything here.

## Exit codes

| Code | Means |
|---|---|
| 0 | products written |
| 1 | crash |
| 2 | bad usage — including an unsupported locale, and an exit that fails its preflight |
| 3 | blocked before parsing (Akamai's 403 / "Access Denied") |
| 4 | ran fine, zero products |
| 5 | the remote API failed (`scraper_api_client.py`) |
| 6 | partial: some pages gathered, then the run stopped early |

Every run that writes output also writes `<out>.meta.json` with `status`,
`stop_reason`, **which** pages failed by number and the category's own
`total_results`. `diff_runs.py` refuses to compare two runs that are not both
`complete`.

## Traps that look like bugs

See [TROUBLESHOOTING.md](TROUBLESHOOTING.md) for the full list. The four that
cost the most time:

* **"akamai" is on every good page and absent from the real refusal.**
  Measured: 1 occurrence on each good page, 0 on the 403. A marker set built
  from the vendor's name would report exit 3 on every successful run — and
  exit 4 on the actual block.
* **The recommendation rail and the template row**, above.
* **A hardcoded `categoryId` scrapes the wrong catalogue and reports
  success.** It is read off the page instead.
* **`lg.com/uk` and `lg.com/us` are different platforms.** Refused with the
  reason rather than returning nothing.

## Testing

```bash
python3 smoke_test.py      # 309 checks, no network, ~2s
pytest -q                  # the same run, through the pytest entry point
```

## Licence

MIT. Scrapes public catalogue pages only: nothing behind a login, nothing
that submits a form, nothing that defeats a protection rather than passing it
the way an ordinary browser does.
