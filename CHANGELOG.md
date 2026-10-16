# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow
[SemVer](https://semver.org/) as closely as a CLI toolkit can: a PATCH release
means fixes, not that every flag is frozen.

## [Unreleased]

### Changed

- CI only: the canary's optional paid secret `LG_PROXY` now reaches it only on a new weekly
  run (Mondays 06:23 UTC) and on a manual dispatch. The daily run keeps its
  schedule and takes the free route without them, so it bills nothing.

## [0.3.0] — 2026-10-15

> **Behaviour changes.**
> - **Exit 5 now means "no rows, and the content was never obtained".** A
>   category page that never loaded, a catalogue API that failed on every
>   attempt, a page with no catalogue form, a form pointing off-site, and a
>   page the site's own count says holds products that parsed to none all
>   exited 4 ("zero products") before. Exit 4 is now kept for the site's own
>   "this category is empty / you are past its end". Scripts keyed on exit 4
>   for those failures need updating.
> - **A served page inside the category's own total that yields no rows is
>   no longer "complete".** It used to end the run as `listing_exhausted`,
>   exit 0. It is now `stop_reason: parser_found_nothing` (or
>   `page_not_painted` in the browser engines): exit 6 with rows, exit 5
>   without, and the page is listed in `pages_failed`.
> - **The catalogue API refusing with 401/403/429/503 on every attempt is a
>   block (exit 3)**, not an `api_error`.
> - **Output files are no longer created 0600.** A new file gets the mode the
>   umask gives it; an existing file keeps its own.
> - **`catalog_client.py --proxy-rotate per-page` is now a usage error
>   (exit 2).** That engine keeps one HTTP session for the whole run, so the
>   value was accepted and did nothing.

### Fixed

- **CSV formula cells.** In the CSV only, a text cell beginning with `=`,
  `+`, `-`, `@`, a tab, CR or LF gets a leading `'`; numbers and the JSON are
  untouched, and the sidecar records `csv_cells_escaped`.
- **One paid captcha solve per page.** Each retry from another exit called
  the solver again, so a page retried from three exits could buy three
  solves. Every browser engine now checks a per-page allowance before
  buying, and a rotation does not reset it.
- **The credential scan read 0 files from a clone under a directory named
  `venv` or `captures`**, because it judged the absolute path. It now judges
  the path inside the repository, also reads `.env*` backups, and fails if it
  scanned nothing.
- **Scraper API client:** `waitFor` is sent as an object (the string form is
  refused with HTTP 422 and still billed), the target's status is read from
  `http_code` rather than the API's verdict string, and the `x-debug` header
  is logged with credentials redacted.
- **`position` from the catalogue API counts the rows emitted**, so a
  skipped record no longer leaves a gap that the rendered-grid path does not.
- **The category page is retried** `--retries` times before the run gives up.
- **Playwright and pyppeteer pass the navigation's HTTP status to the page
  classifier**, so a refusal status is seen even when the body looks served.
- `--url` help in the browser engines and the bug-report template described
  another site's URLs; both now describe lg.com category URLs, and the
  template lists `catalog_client.py`.
- The weekly `pip-audit` job had been red since 2026-10-05 on two new urllib3
  1.x advisories (PYSEC-2026-4175, PYSEC-2026-4177) in the pyppeteer lock.
  pyppeteer 2.0.0 still requires `urllib3<2` and the fix is only in 2.8.0, so
  they are ignored by ID beside the five already listed, and the README says
  so.
- The daily canary now fails on a block (exit 3) or on exit 5, instead of
  passing a block from a GitHub runner as a notice: this path needs no
  credential and has served those runners every day.

## [0.2.3] — 2026-09-29

### Fixed

Found by a family-wide pass against the template on 2026-09-29; each one
made a documented feature do less than it said while the run reported
success.

- **`CONTRIBUTING.md` promised checks the suite did not make**: that a
  workflow installing anything but a hashed lock, a lock drifting from its
  `.txt`, or an action referenced by tag fails the suite. It now does.
- **`--fingerprint` never patched anything.** The init script it installs
  was syntactically broken JavaScript (`( => {` — the parameter list and the
  IIFE call were missing), and a syntax error in an init script is silent:
  the page ran unpatched. Fixed, and the suite now runs `node --check` on
  that script too — 0.2.2's "every shipped script" did not include it.
- **pyppeteer could not authenticate a proxy on current Chrome.**
  `page.authenticate()` uses `Network.setRequestInterception`, which
  current Chrome no longer has ("wasn't found", exit 5 before the first
  request — measured 2026-09-29 in rosreestr-scraper, which shares this
  engine code). The engine now answers the proxy's 407 through the CDP
  Fetch domain; verified against a local Basic-auth proxy.
- **A failing suite could turn the engine CI job green.** The step ran
  `smoke_test.py | tee` under GitHub's default shell, which has no
  pipefail, so the job took tee's exit status. It now runs with
  `shell: bash`, imports the engine by name first, and the suite fails if
  any piped step lacks pipefail.
- **The engine CI jobs had been failing, unseen.** Once pipefail made the
  step's exit status real, all three engine jobs went red on the secret
  scan: it walked into CI's own `.venv-<engine>` and read pip's and
  selenium's vendored code as credentials. The scan now skips any directory
  holding `pyvenv.cfg`, whatever it is called, and still
  reads ordinary untracked files — both planted in a check.
- **The Playwright engine job never had a browser**, so the suite's
  fingerprint-refusal check — which starts Chromium before it fetches —
  crashed there with exit 1 instead of the expected 5. Also hidden until
  pipefail; the engine jobs now install the browser they drive.
- **Skipped checks were counted as passed.** A check that could not run
  printed "SKIPPED" and was added to the pass count. Skips are now their own
  count, listed at the end.
- **`env_config.py --help` read `.env`.** `ci_checks.py` runs every CLI's
  `--help`; a help screen now opens no credentials file.
- **`.gitignore` let a renamed `.env` through** (`.env.bak`, `.env.local`),
  and a run directory (`live/`) or a paged dump (`X.page3`). Now ignored by
  shape, with a check.

### Changed

- **Connecting to the Scraping Browser API** follows one policy in every
  engine: three attempts 3 s apart for a locked
  profile or an outage, none for an expired login (401), and a message that
  names the status's own cause. pyppeteer's per-attempt timeout is 10 s (it
  reports a refused handshake only as a timeout), and the orphaned-task
  traceback that followed a correct exit is filtered.

## [0.2.2] — 2026-09-28

### Fixed

- **A refused Fingerprint API crashed the run with a traceback and exit 1.**
  It now exits 5, the remote-API code, with the reason and no traceback.
  The case is real: on 2026-09-28 a key with a working captcha-solving
  balance got 403 from `/fingerprint/random`, because fingerprints are a
  separate subscription.
- **Runtime captcha detection never ran, in any engine.** The in-page
  discovery script began ` => {`, with no parameter list. That is a
  SyntaxError, and it was logged at debug level. Selenium also returned the
  function instead of calling it. Both are fixed, and a discovery failure is
  now a warning. The suite now runs `node --check` on every shipped script
  and executes discovery in a real Chromium page. A script error there is a
  failure, not a skip.
- **An explicitly rendered reCAPTCHA v2 widget was classified as v3** by the
  HTML detector. 2Captcha answers a v2 task sent as v3 with
  `ERROR_CAPTCHA_UNSOLVABLE`. Found by a live solve in screener-scraper
  (2026-09-28, 2Captcha's reCAPTCHA v2 demo page), which shares this
  module; after the fix, the demo page's own server-side check returned
  `"success": true`.

## [0.2.1] — 2026-09-25

No change to what the scraper does. What changes is how it is installed and
built: CI, the canary and the Docker image now install exact, hash-checked
versions, and every GitHub Action is pinned by commit. If you install with
`pip install -r requirements.txt`, nothing changes for you; for the versions
CI tested, install the matching `requirements*.lock` (see the README).

> **pyppeteer users:** that engine's environment carries urllib3 1.26.20,
> which has five advisories fixed only in urllib3 2.x — pyppeteer forbids
> it. One lets a cross-origin redirect forward `Proxy-Authorization`. Prefer
> Playwright when running with proxy credentials.

### Security

- **Every GitHub Action is pinned to a commit SHA** (`checkout` v4.4.0,
  `setup-python` v5.6.0, `upload-artifact` v4.6.2), with the release as a
  comment. A tag can be moved to another commit — the March 2025
  `tj-actions/changed-files` compromise did exactly that — and the canary
  runs with the `LG_PROXY` secret in its environment. Dependabot now proposes
  updates to the pins.
- **Hashed lock files** for the core and each engine (`requirements*.lock`,
  plus `.github/requirements-ci.lock` for pytest and pip-audit), resolved for
  Python 3.9+. CI, the canary and the Docker image install only these, with
  `--require-hashes`; the unpinned `pip install --upgrade pip` and
  `pip install pytest` steps are gone.
- **`audit.yml`: pip-audit over every lock**, on each change and weekly. It
  found five urllib3 1.26.20 advisories in the pyppeteer lock, fixed only in
  urllib3 2.x, which pyppeteer forbids; they are ignored by ID and documented
  in the README, so any new advisory still fails.
- `smoke_test.py` asserts all of the above, so a later edit cannot quietly
  unpin an action or add an unlocked install.

### Not changed, and why

- **Docker image digest and a non-root user** — deferred until the image runs
  as a service; a non-root user breaks the documented
  `-v "$PWD/out:/out"` mount on Linux.
- **The `.txt` files keep their `>=` floors** — they are the specification a
  person edits and what `pyproject.toml` mirrors; the locks are generated
  from them.

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
