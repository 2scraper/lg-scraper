# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[SemVer](https://semver.org/) as closely as a CLI toolkit can: a PATCH release
means fixes, not that every flag is frozen.

## [0.2.0] — 2026-09-25

> **Behaviour change for anyone reading the sidecar.** A run that fetched
> every page `--pages` allowed but stopped before the category ended is now
> `status: "limited"` (still exit 0), not `"complete"`, and `diff_runs.py`
> refuses it. A scheduled snapshot that relied on `--pages 3` of a longer
> category must ask for more pages than the category has — `--pages 10`
> costs nothing extra, the engine stops at the API's own last page.

Every item below was reproduced before it was fixed, by a third-party audit
on 2026-09-25 and again offline, and each has a regression check that fails
against v0.1.0.

### Fixed

- **A sample was reported as the whole catalogue.** The engines start from
  `stop_reason = "completed"` and kept it when the page loop simply ran out
  of `--pages`; `finish_run` counted that as complete. Live, `--pages 2` of
  the 48-product RU television category wrote `status: complete` with 24
  products and `total_results: 48`, and `diff_runs.py` then reported **24
  removed**. `finish_run` now calls such a run complete only on the site's
  own evidence — every page of its `pageCount` fetched, or the products
  reaching its `totalCount` — and `limited` otherwise. Fixed once, in the
  shared `finish_run`, so all five engines get it.
- **`diff_runs.py` trusted what it could not see.** A missing sidecar was
  treated as "nothing to check", and two complete runs of different
  categories were diffed row by row. Both are now refused without `--force`.
- **The lg.com check accepted any host ending in "lg.com"** — `notlg.com`,
  `evillg.com` — plus `http://`, `file://`, userinfo and non-standard ports.
  Now HTTPS only, the host `lg.com` or a subdomain of it, default port, no
  userinfo; a URL with no scheme says so instead of calling the host a
  locale.
- **The form's action was POSTed to wherever it pointed.** It comes from
  served HTML; an absolute action (`http://169.254.169.254/…`) was followed
  as-is. It must now be same-origin and under the page's own
  `/{locale}/mkt/ajax/`, every redirect hop of the category GET is checked
  against the same rule, and the API POST no longer follows redirects. An
  off-origin `data-price-sync-url` is dropped.
- **Output writes were not atomic.** A missing `--out` directory raised
  `FileNotFoundError` after every page had been fetched, and a run killed
  mid-write left a torn file where the last good one had been. Files are now
  written to a temporary file, `fsync`ed and renamed; the directory is
  created; the old sidecar is removed first and the new one written last.
- **Numeric flags were not validated.** `--pages 0` finished as an empty
  run, a negative `--delay` crashed in `sleep()` after page 1, `--retries 0`
  never made a request. All five CLIs now range-check these before any
  network call (exit 2).
- **`python3 env_config.py` printed credentials without an `@`** — a CDP
  endpoint with `?token=` or a proxy with a key in its query string. Every
  key but `LG_URL` is now shown as a length only.
- **The canary checked a sample.** It ran `--pages 3` of a 4-5 page
  category and required `complete`; it now reads the whole category and
  asserts `listing_complete`, `pages_completed == page_count` and at least
  95% of `total_results` written. Its `--dump-html` no longer shares a name
  with the output file.

### Added

- Sidecar fields `schema_version` (2), `listing_complete`, `scope`
  (`full_listing` / `limited_pages`), `page_count` and `completeness_ratio`.

### Not changed, and why

- **The flat module layout and the single-file `smoke_test.py`** — the
  audit's `src/` package, shared orchestrator and pytest-module migration
  are a family-wide decision (the template keeps them this way on purpose),
  not a fix; the status/exit mapping the engines must agree on is already
  one shared `finish_run`.
- **pyppeteer stays**, as the original brief requires.
- **Supply-chain hardening** (Actions pinned by SHA, image digest, non-root
  container, lock files, `pip-audit`) is worth doing and is a separate batch:
  a non-root user breaks the documented `-v "$PWD/out:/out"` mount on Linux
  and needs its own answer.

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
- **The API's two counts, told apart.** `pageInfo.totalCount` is the number
  of products the category pages through (50 for RU televisions, 41 for UA);
  the `totalCount` beside the product list is the count of SIZE VARIANTS
  across those rows (203 for the same RU category — exactly the sum of every
  row's `sibling_sizes`). Reading the second as a product count made a
  complete 50-of-50 run warn that it had lost three quarters of the
  catalogue, and wrote that claim into the run's metadata sidecar.
  `pageInfo.pageCount` now also ends the run on the last page instead of
  spending a request to discover the next one is empty.
- **A second parse path over the rendered grid**, anchored on schema.org
  microdata and the site's own `data-model-*` attributes, verified to return
  the same products in the same order, and agreeing with the API on every
  column the card carries — `super_category` and `where_to_buy_url` are read
  off the card for that reason. Three columns exist only in the API
  (`product_category`, `product_category_slug`, `status`) and stay null on
  the DOM path rather than being guessed; see README for the measurement.
- **`page_flow.py`** — one classification of what a page IS (content, empty,
  past the end, unpainted, blocked) and one policy table shared by every
  engine. On this site the page NUMBER is part of it: past the end the site
  answers 200 with an empty grid and `totalCount: 0`, the same shape as a
  category that is genuinely empty, and the two are different answers. A
  caller that read the catalogue API is judged on the record count alone:
  the HTML evidence below it (asset paths, a challenge page's wording) is
  absent from every JSON payload, so weighing it there called each exhausted
  category a block. And a response carrying LG's assets but neither
  `categoryFilterForm` nor `product-list-box` is the shell still painting,
  not an empty category — measured on a good page and on page 99, which
  holds no cards and carries both.
- **A supported-locale check that refuses with the reason.** `/uk` is a
  different markup generation, `/us` answered 403 from Akamai; the site's own
  hreflang set lists exactly `ru-ru` and `ru-ua`.
- **`proxy_pool.py`, `env_config.py`, `fingerprint_client.py`,
  `diff_runs.py`** — proxy pools with rotation, credential masking and a
  preflight, `.env` loading with documented precedence, the Fingerprint API
  client, and a run-to-run diff keyed on the model code.
- **Run metadata sidecar** (`<out>.meta.json`), the family exit-code
  contract, `--concurrency`, a 346-check offline suite, `.github/ci_checks.py`
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

- The catalogue API: `/ru/televisions` end to end — 5 pages, all 50 products
  the category pages through, exit 0, from an ordinary connection with no
  credential of any kind; `/ua/televisions` likewise, 4 pages and 41
  products under its own `categoryId` and UAH.
- `?page=` on the category URL: pages 1, 2 and 3 each served a different set
  of grid models.
- `/ua` runs the same platform as `/ru`; `/uk` does not; `/us` answered 403.
- The support form's reCAPTCHA v3 is detected from its markup, and no captcha
  of any kind appears on a category page.
