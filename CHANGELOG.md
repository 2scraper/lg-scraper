# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[SemVer](https://semver.org/) as closely as a CLI toolkit can: a PATCH release
means fixes, not that every flag is frozen.

## [0.1.0] — 2026-09-21

First release of the rewritten scraper. Everything below is a difference from
the previous, unreleased version of this code, with the measurement that
justified the change.

### Added

- **The site's own catalogue API as the primary path** (`catalog_client.py`).
  lg.com describes the call in a `#categoryFilterForm` on every category
  page; a plain HTTPS POST returns 12 products per page with 265 fields each
  and the category's own `totalCount`. No browser, no key. The parameters are
  read off the page every run — `categoryId` differs per category AND per
  locale, and hardcoding one would scrape a different catalogue than the URL
  asked for while reporting success.
- **A second parse path over the rendered grid**, anchored on schema.org
  microdata and the site's own `data-model-*` attributes, verified to return
  the same products and agree with the API on every shared column.
- **`page_flow.py`** — one classification of what a page IS (content, empty,
  past the end, unpainted, blocked) and one policy table shared by every
  engine. On this site the page NUMBER is part of it: past the end the site
  answers 200 with an empty grid and `totalCount: 0`, the same shape as a
  category that is genuinely empty, and the two are different answers.
- **A supported-locale check that refuses with the reason.** `/uk` is a
  different markup generation, `/us` answered 403 from Akamai; the site's own
  hreflang set lists exactly `ru-ru` and `ru-ua`.
- **`proxy_pool.py`, `env_config.py`, `fingerprint_client.py`,
  `diff_runs.py`** — proxy pools with rotation, credential masking and a
  preflight, `.env` loading with documented precedence, the Fingerprint API
  client, and a run-to-run diff keyed on the model code.
- **Run metadata sidecar** (`<out>.meta.json`), the family exit-code
  contract, `--concurrency`, a 309-check offline suite, `.github/ci_checks.py`
  invoked from both CI and the suite, a Docker image that carries no browser
  because the primary engine needs none, and a daily canary.

### Fixed

- **The parser's primary path was JSON-LD, of which this platform has none.**
  Zero `application/ld+json` blocks on every `/ru` and `/ua` capture: the
  path could never return a row, and the fallback did all the work.
- **Products were collected by URL shape, page-wide.** That picks up the
  recommendation rail — 7 products, identical on pages 1, 2 and 3 — and the
  site's own unfilled template row. Both repeat on every page, so a
  multi-page run inflated its rows and then stopped early, reporting a
  complete result, when dedupe found "nothing new". Extraction is now scoped
  to `.product-list-box` and template values are discarded.
- **Nothing detected a block.** Akamai's 403 "Access Denied" page would have
  been parsed as zero products (exit 4) rather than blocked (exit 3). The
  marker set now comes from a measurement: the word "akamai" appears once on
  every good page and NOT on the refusal, while "Access Denied" and
  "edgesuite" appear only on the refusal.
- **The title was "the longest string left on the tile" after a junk-text
  filter.** It is now the microdata's `itemprop="name"`, or the API's
  `userFriendlyName`.
- **The screen size was the first entry of the size switcher**, which lists
  siblings largest-first — reporting an 85" card as the 65" model. It is now
  the ACTIVE switch.
- **Price and rating were explained in prose rather than measured.** Both are
  null here, and the evidence is now written down: nine numeric price fields
  at zero across 36 products on two locales, the site's own price service
  returning the same zeros, and `priceAreaYn: "N"` on every record.

### Verified live (2026-09-21)

- The catalogue API: 3 pages, 36 products, `totalCount` 203, exit 0, from an
  ordinary connection with no credential of any kind.
- `?page=` on the category URL: pages 1, 2 and 3 each served a different set
  of grid models.
- `/ua` runs the same platform as `/ru`; `/uk` does not; `/us` answered 403.
- The support form's reCAPTCHA v3 is detected from its markup, and no captcha
  of any kind appears on a category page.
