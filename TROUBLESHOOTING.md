# Troubleshooting

Ordered by how often each one happens. Every number was measured on the date
given, from an ordinary connection in Europe unless stated otherwise.

## Exit 2 — "this URL cannot be scraped by this repo"

Only `lg.com/ru` and `lg.com/ua` run the platform this parser reads. The
message names which difference stopped it:

* **another locale** — `/uk`, `/de`, `/in` and the rest are a different
  markup generation: no `data-model-*` attributes, no `#categoryFilterForm`,
  and their category paths differ too (`/uk/tvs` redirects to
  `/uk/tvs-soundbars/all-tvs/`). Measured 2026-09-21.
* **another host** — anything that is not `lg.com`.

The supported set is not a preference: the site's own `hreflang` on
`/ru/televisions` lists exactly `ru-ru` and `ru-ua`.

## Exit 2 — "the proxy exit … cannot be used"

One small request through the exit failed before any browser started. The
usual cause is a rotated password; a `407` in the detail says exactly that.
A slow exit only warns.

## Exit 3 — blocked

That is **Akamai, not a captcha**. lg.com answers a normal request with 200,
and `/us` answered the same client `403 Access Denied` with an
`errors.edgesuite.net` reference on 2026-09-21. A different exit is what
helps; solving something will not, because there is nothing to solve.

Note the marker set deliberately does not contain the word "akamai": it
appears once on every page the site serves and **not at all** on the refusal.

## Exit 4 — zero products, and the file was not written

A run that finds nothing writes nothing, so a failure cannot overwrite last
night's good data. Check the sidecar's `stop_reason`:

* `no_results` — page 1 of a category with nothing in it. Pass
  `--allow-empty` if an empty file is the answer you want.
* `listing_exhausted` — you asked for more pages than the category has. Past
  the end the site answers 200 with an empty grid, and the API reports
  `totalCount: 0` for that response — which is why the page NUMBER is part of
  the classification.

## The run returned 12 rows and I asked for 3 pages

Read `stop_reason`. `no_new_products` means page 2 repeated page 1. On this
site that is usually the recommendation rail leaking into the rows — it
carries the same 7 products on every page — which is why extraction is scoped
to `.product-list-box`. If you have modified the parser, that scope is the
first thing to check.

## Every product says the price is empty

That is the site, and it was measured rather than assumed: nine numeric price
fields at 0 across 36 products on two locales and two categories, the site's
own price service (`retrievePlpPriceSyncList`) returning the same zeros, and
`priceAreaYn: "N"` on every record. lg.com is a manufacturer catalogue; the
`where_to_buy_url` column is what it offers instead of a price.

A published `0` is deliberately reported as `null`: a zero in a
price-monitoring column would make every LG television look free.

## screen_size is null on some rows, filled on others

The grid path reads the size off the card's size switcher, and a
single-size model has no switcher. The API path has `inchCode` for those
products, so `catalog_client.py` fills the column where a browser engine
cannot. Guessing it out of the title ("55-дюймовый…") would be the parser
inventing data, so it does not.

## pyppeteer: "Browser closed unexpectedly"

pyppeteer downloads a Chromium from 2018 and launches that. Point it at one
that runs:

    PYPPETEER_EXECUTABLE_PATH="$HOME/Library/Caches/ms-playwright/chromium-*/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing" \
      python3 puppeteer_scraper.py --url "https://www.lg.com/ru/televisions"

## Selenium: SessionNotCreatedException against a Scraping Browser endpoint

Expected, and refused up front with the reason: chromedriver's
`debuggerAddress` takes a bare `host:port` with nowhere to put a password.
Use Playwright or pyppeteer for an authenticated CDP endpoint.

## A captcha is reported on a page that clearly has none

The 2Captcha autosolver extension injects `<captcha-widgets>` and its own
turnstile hunters into every page it loads, so a capture taken through the
Scraping Browser carries captcha markup that the site never served. Every
detector here strips `chrome-extension://` script tags first, and the mount
is only treated as a finding when it has CONTENT.
