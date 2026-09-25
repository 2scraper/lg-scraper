#!/usr/bin/env python3
"""
lg-scraper — Selenium edition
==================================

The same scrape as playwright_scraper.py, driven through Selenium + Chrome.
Kept for parity: all three engines must agree on exit codes, run status, and
whether a run crashes or spends money. What a page IS comes
from page_flow.py, what a listing CONTAINS from product_parser.py, and the
status/exit mapping from output_writer.finish_run — shared, so the three
cannot drift.

Two limits are real and are stated here rather than left to be discovered:

  * **Selenium cannot use an authenticated remote CDP endpoint.** Playwright's
    `connect_over_cdp` and Puppeteer's `browserWSEndpoint` take a full
    `ws://user:pass@host:port` and authenticate on the WebSocket upgrade;
    chromedriver's `debuggerAddress` takes a bare `host:port` with nowhere to
    put a password. So `--cdp-endpoint` with credentials is REFUSED here with
    that reason, rather than failing later as a confusing
    SessionNotCreatedException.
  * **Selenium cannot authenticate a proxy at all.** `--proxy-server` has no
    credential channel, so credentials are stripped and the run WARNS; it does
    not pretend a user:pass URL is doing something.

For either of those, use the Playwright or pyppeteer engine.

Usage
-----
    python3 selenium_scraper.py \\
        --url "https://www.lg.com/ru/televisions" \\
        --pages 3 --out saas_products

Requires: pip install -r requirements.txt -r requirements-selenium.txt
"""

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from typing import List, Optional
from urllib.parse import urlparse

# Module level on purpose — see the note in puppeteer_scraper.py: an engine
# that imports its driver lazily imports cleanly with the driver absent, and
# the offline suite's skip (and the CI job that fails on an unexpected skip)
# both stop meaning anything.
from selenium import webdriver
from selenium.common.exceptions import (TimeoutException, WebDriverException)
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

import env_config
import page_flow
from captcha_solver import (CAPTCHA_DISCOVERY_JS, INJECT_TOKEN_BODY,
                            challenge_from_discovery, detect_in_html,
                            reconcile_detections, solve)
from output_writer import dedupe_by_sku, finish_run
from product_parser import (SELECTORS, category_from_url, page_url,
                            parse_products, unsupported_locale_reason)
from proxy_pool import (ROTATE_MODES, ProxyError, check_exit_or_raise,
                        from_args as proxy_pool_from_args, mask,
                        redact_secret_patterns, to_selenium)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("selenium_scraper")

# --- the whole of this engine's site knowledge, identical to its twins' -----
ITEM_CARD_SELECTOR = SELECTORS["item_card"]
MIN_CARD_MATCHES = 5
RENDER_WAIT_MS = 15000
NEXT_PAGE_SELECTOR = None
# ---------------------------------------------------------------------------

PAGE_LOAD_TIMEOUT_S = 60


@dataclass
class PageOutcome:
    page_num: int
    url: str
    final_url: Optional[str] = None
    products: List = field(default_factory=list)
    state: Optional[page_flow.PageState] = None
    load_failed: bool = False

    @property
    def ok(self) -> bool:
        return not self.load_failed and self.state is not None and \
            not self.state.policy.blocked

    @property
    def complete_here(self) -> bool:
        return self.state is not None and self.state.policy.complete


_PROXY_ERROR_MARKERS = (
    "ERR_PROXY_CONNECTION_FAILED", "ERR_TUNNEL_CONNECTION_FAILED",
    "ERR_PROXY_AUTH_UNSUPPORTED", "ERR_PROXY_AUTH_REQUESTED",
    "ERR_UNEXPECTED_PROXY_AUTH", "ERR_PROXY_CERTIFICATE_INVALID",
)


def _proxy_failure(exc) -> str:
    text = str(exc)
    return next((m for m in _PROXY_ERROR_MARKERS if m in text), "")


def _build_driver(args, pool):
    """Build a Chrome driver on `pool`'s current exit."""
    options = Options()
    if args.cdp_endpoint:
        endpoint = args.cdp_endpoint
        parsed = urlparse(endpoint if "://" in endpoint else f"ws://{endpoint}")
        if parsed.username or parsed.password:
            raise ProxyError(
                "--cdp-endpoint carries credentials, and Selenium cannot use "
                "them: chromedriver's debuggerAddress is a bare host:port with "
                "nowhere to put a password, unlike Playwright's "
                "connect_over_cdp or Puppeteer's browserWSEndpoint. Use "
                "playwright_scraper.py or puppeteer_scraper.py for the "
                "Scraping Browser API.")
        address = f"{parsed.hostname}:{parsed.port}" if parsed.port else parsed.hostname
        logger.info("Attaching to an existing browser at %s", address)
        options.add_experimental_option("debuggerAddress", address)
        try:
            return webdriver.Chrome(options=options)
        except WebDriverException as e:
            raise RuntimeError(f"Could not attach to {address}: "
                               f"{redact_secret_patterns(str(e))}") from None

    if args.headless:
        options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    if pool:
        proxy_arg, warning = to_selenium(pool.current)
        if warning:
            logger.warning("%s", warning)
        if proxy_arg:
            options.add_argument(proxy_arg)
            logger.info("Using proxy exit %s", mask(pool.current))
    driver = webdriver.Chrome(options=options)
    driver.set_page_load_timeout(PAGE_LOAD_TIMEOUT_S)
    return driver


class _BrowserSession:
    """_BrowserSession
    """

    def __init__(self, args, pool):
        self.args, self.pool = args, pool
        self.driver = None

    def open(self):
        self.driver = _build_driver(self.args, self.pool)
        return self

    def relaunch(self):
        if self.args.cdp_endpoint:
            return   # a remote browser's exit is not ours to change
        try:
            self.driver.quit()
        except Exception as e:  # noqa: BLE001
            logger.debug("Ignoring error while quitting driver: %s", e)
        self.open()

    def close(self):
        try:
            self.driver.quit()
        except Exception as e:  # noqa: BLE001
            logger.debug("Ignoring error during teardown: %s", e)


def _count_cards(driver) -> int:
    """Card count through the WebDriver protocol — never an evaluated string."""
    try:
        return len(driver.find_elements(By.CSS_SELECTOR, ITEM_CARD_SELECTOR))
    except WebDriverException as e:
        logger.debug("Counting cards failed: %s", e)
        return 0


def _wait_for_cards(driver, timeout_ms: int = RENDER_WAIT_MS) -> int:
    deadline = time.monotonic() + timeout_ms / 1000.0
    count = 0
    while time.monotonic() < deadline:
        count = _count_cards(driver)
        if count > MIN_CARD_MATCHES:
            return count
        time.sleep(0.4)
    return count


def _discover_captcha(driver, url: str):
    """Run the shared discovery script through execute_script.

    Selenium's execute_script takes a function BODY with an explicit `return`,
    while Playwright and pyppeteer take `() => expr`. The shared module ships
    the arrow function, so it is INVOKED here — `return (fn)();` — rather than
    each engine growing its own dialect of the same script.
    """
    try:
        info = driver.execute_script(f"return ({CAPTCHA_DISCOVERY_JS});")
    except WebDriverException as e:
        logger.debug("Runtime captcha discovery failed: %s", e)
        return None
    return challenge_from_discovery(info, page_url=url)


def handle_captcha_if_present(driver, args) -> None:
    """Runs after EVERY navigation, for ANY page — not scoped to one URL."""
    html = driver.page_source
    url = driver.current_url
    challenge = reconcile_detections(detect_in_html(html, url),
                                     _discover_captcha(driver, url))
    if not challenge:
        return

    state = page_flow.classify(html, url=url)
    if args.solve_captcha == "when-blocked" and not state.policy.may_solve:
        logger.info("%s detected via %s, but the page is %s — not paying to "
                    "solve it.", challenge.kind, challenge.source, state)
        return

    logger.warning("%s detected via %s (sitekey=%s) — attempting to solve.",
                   challenge.kind, challenge.source, challenge.sitekey)
    if not args.twocaptcha_key:
        logger.warning("No 2captcha API key, so this challenge cannot be solved "
                       "— continuing with whatever the page already holds.")
        return
    try:
        token = solve(challenge, args.twocaptcha_key,
                      api_version=args.captcha_api, min_score=args.min_score)
    except Exception as e:  # noqa: BLE001 — a solver failure is not a crash
        logger.error("Solving the challenge failed (%s) — continuing.",
                     redact_secret_patterns(str(e)))
        return
    # The BODY form, not the arrow function: see captcha_solver's two
    # constants and _discover_captcha above.
    driver.execute_script(INJECT_TOKEN_BODY, token)
    if state.policy.blocked:
        logger.info("Token injected. Reloading page to continue.")
        time.sleep(1.5)
        driver.refresh()
    else:
        # See the twins: a reload on a page that was not blocked discards the
        # token, and a form's token is only useful at submit time.
        logger.info("Token injected and left in the page's own response field. "
                    "This page was not blocked, so it is NOT reloaded.")


def _dump_debug(driver, args, page_num: int, html: Optional[str]) -> None:
    debug_html = f"{args.out}_page{page_num}_debug.html"
    with open(debug_html, "w", encoding="utf-8") as f:
        f.write(html or "")
    try:
        driver.save_screenshot(f"{args.out}_page{page_num}_debug.png")
    except Exception as e:  # noqa: BLE001
        logger.warning("Could not capture screenshot: %s", e)
    logger.warning("Wrote %s (and a .png beside it).", debug_html)


def _fetch_one_page(session, args, pool, page_num: int, url: str) -> PageOutcome:
    outcome = PageOutcome(page_num=page_num, url=url)
    block_retries = args.proxy_block_retries if (pool and len(pool) > 1) else 0
    html, state, load_failed = None, None, False

    for block_attempt in range(block_retries + 1):
        logger.info("Fetching page %d/%d: %s", page_num, args.pages, url)
        load_failed, exit_failed = False, None
        for attempt in range(1, args.retries + 1):
            try:
                session.driver.get(url)
                load_failed = False
                break
            except (TimeoutException, WebDriverException) as e:
                reason = _proxy_failure(e)
                if reason:
                    exit_failed, load_failed = reason, True
                    break
                load_failed = True
                if attempt < args.retries:
                    pause = args.retry_delay * (2 ** (attempt - 1))
                    logger.warning("Failed to load %s (attempt %d/%d) — retrying "
                                   "in %.1fs.", url, attempt, args.retries, pause)
                    time.sleep(pause)

        if exit_failed and block_attempt < block_retries:
            logger.warning("Exit %s is unusable (%s) — rotating (%d/%d).",
                           mask(pool.current), exit_failed, block_attempt + 1,
                           block_retries)
            pool.advance(f"unusable exit: {exit_failed}")
            session.relaunch()
            continue
        if exit_failed:
            # Say WHICH failure this was. Chromium reports a dead proxy as a
            # generic error, not a timeout, and the two want opposite
            # responses — "gave up loading" alone sends the reader looking at
            # the site when the exit is what is broken.
            logger.error("The proxy exit %s refused the connection (%s), and "
                         "there is no other exit to try. This is the exit, not "
                         "the site: check the entry, or pass --proxy-file with "
                         "more than one.", mask(pool.current) if pool else "(none)",
                         exit_failed)
        if load_failed:
            break

        handle_captcha_if_present(session.driver, args)
        cards = _wait_for_cards(session.driver)
        html = session.driver.page_source
        state = page_flow.classify(html, url=session.driver.current_url, page_num=page_num)
        logger.info("Page %d is %s (%d card(s) rendered).", page_num, state, cards)

        if state.state == page_flow.UNPAINTED and state.policy.wait_first:
            time.sleep(RENDER_WAIT_MS / 1000.0)
            html = session.driver.page_source
            state = page_flow.classify(html, url=session.driver.current_url, page_num=page_num)
            logger.info("After waiting, page %d is %s.", page_num, state)

        if not state.policy.rotate_exit:
            break
        if block_attempt < block_retries:
            logger.warning("Blocked on page %d from %s (%s) — retrying from "
                           "another exit (%d/%d).", page_num, mask(pool.current),
                           state.reason, block_attempt + 1, block_retries)
            pool.advance(f"blocked on page {page_num}")
            session.relaunch()

    if load_failed:
        logger.error("Gave up loading %s after %d attempt(s).", url, args.retries)
        outcome.load_failed = True
        return outcome

    outcome.state = state
    outcome.final_url = session.driver.current_url

    if args.dump_html:
        dump_path = (args.dump_html if args.pages == 1
                     else f"{args.dump_html}.page{page_num}")
        with open(dump_path, "w", encoding="utf-8") as f:
            f.write(html)
        logger.info("Saved the snapshot the parser sees to %s (%d bytes).",
                    dump_path, len(html))

    if state.policy.blocked:
        _dump_debug(session.driver, args, page_num, html)
        logger.error("Blocked before parsing on page %d (%s) — exit 3, distinct "
                     "from a query that genuinely matches nothing (exit 4).",
                     page_num, state.reason)
        return outcome

    if state.state in (page_flow.EMPTY, page_flow.EXHAUSTED):
        logger.info("Page %d: %s — nothing further to fetch.", page_num, state.reason)
        return outcome

    products = parse_products(html, session.driver.current_url,
                              category=args.category, page=page_num)
    logger.info("Parsed %d product(s) from page %d.", len(products), page_num)
    short = page_flow.shortfall(state.total_results, page_num, len(products))
    if short:
        logger.warning("%s.", short)
    if not products:
        _dump_debug(session.driver, args, page_num, html)
    outcome.products = products
    return outcome


def _addressable(first: PageOutcome, second: PageOutcome) -> bool:
    """Did the constructed page-2 URL actually reach page 2? See the twins."""
    if not second.ok or not second.products:
        return False
    first_ids = {p.sku for p in first.products if p.sku}
    return any(p.sku and p.sku not in first_ids for p in second.products)


def scrape(args) -> int:
    outcomes: List[PageOutcome] = []
    blocked = False
    stop_reason = "completed"
    total_results = None
    addressable = None

    pool = proxy_pool_from_args(args)
    if pool and args.cdp_endpoint:
        logger.warning("Ignoring --proxy/--proxy-file: with --cdp-endpoint the "
                       "remote browser has its own exit.")
        pool = None
    # One small request through the exit before a browser is launched. A
    # rotated password otherwise surfaces as three 60s navigation timeouts per
    # page with nothing naming the proxy — measured 2026-09-21.
    check_exit_or_raise(pool, args.url)
    if args.concurrency > 1:
        logger.warning("--concurrency %d is not implemented in the Selenium "
                       "engine — fetching one page at a time. Use "
                       "playwright_scraper.py for concurrent pages.",
                       args.concurrency)

    session = _BrowserSession(args, pool).open()
    try:
        first = _fetch_one_page(session, args, pool, 1, args.url)
        outcomes.append(first)
        if first.state is not None:
            total_results = first.state.total_results

        if not first.ok:
            stop_reason = ("page_load_timeout" if first.load_failed
                           else f"blocked_{(first.state.vendor if first.state else 'unknown')}")
            blocked = bool(first.state and first.state.policy.blocked)
        elif first.complete_here:
            stop_reason = ("no_results" if first.state.state == page_flow.EMPTY
                           else "listing_exhausted")
        elif args.pages > 1:
            seen = {p.sku for p in first.products if p.sku}
            previous = first
            for page_num in range(2, args.pages + 1):
                if pool and pool.rotates_per_page():
                    pool.advance(f"per-page rotation, page {page_num}")
                    session.relaunch()
                time.sleep(args.delay)
                outcome = _fetch_one_page(session, args, pool, page_num,
                                          page_url(args.url, page_num))
                outcomes.append(outcome)
                if not outcome.ok:
                    stop_reason = ("page_load_timeout" if outcome.load_failed
                                   else f"blocked_{(outcome.state.vendor if outcome.state else 'unknown')}")
                    blocked = bool(outcome.state and outcome.state.policy.blocked)
                    break
                if outcome.complete_here or not outcome.products:
                    stop_reason = "listing_exhausted"
                    break
                if page_num == 2:
                    addressable = _addressable(previous, outcome)
                    if not addressable:
                        logger.error(
                            "Page 2 (%s) returned nothing page 1 did not already "
                            "have, so this listing cannot be paged through by "
                            "URL. Stopping and reporting a PARTIAL run rather "
                            "than a complete-looking one.", outcome.url)
                        stop_reason = "pagination_not_addressable"
                        outcomes.remove(outcome)
                        break
                fresh = [p for p in outcome.products
                         if p.sku is None or p.sku not in seen]
                seen.update(p.sku for p in outcome.products if p.sku)
                if not fresh:
                    logger.info("Page %d added nothing not already seen — "
                                "treating that as the end of the listing.", page_num)
                    stop_reason = "no_new_products"
                    break
                previous = outcome
    finally:
        session.close()

    all_products = []
    merged_seen = set()
    for oc in sorted(outcomes, key=lambda o: o.page_num):
        fresh = dedupe_by_sku(oc.products, merged_seen)
        if len(fresh) < len(oc.products):
            logger.info("Page %d: dropped %d product(s) already seen earlier.",
                        oc.page_num, len(oc.products) - len(fresh))
        all_products.extend(fresh)

    ok_pages = [o for o in outcomes if o.ok]
    failed_pages = [o.page_num for o in outcomes if not o.ok]
    final_url = (max(ok_pages, key=lambda o: o.page_num).final_url
                 if ok_pages else args.url)
    return finish_run(all_products, args.out, args.format, args.allow_empty,
                      blocked=blocked, stop_reason=stop_reason,
                      pages_requested=args.pages, pages_completed=len(ok_pages),
                      pages_failed=failed_pages, total_results=total_results,
                      addressable=addressable,
                      start_url=args.url, final_url=final_url)


def parse_args():
    p = argparse.ArgumentParser(description="LG catalogue scraper (Selenium edition)")
    p.add_argument("--url", default=None,
                   help="A lg.com listing URL: /search with any filter[...] "
                        "parameters, or a category shortcut such as /websites. "
                        "Required unless LG_URL is set.")
    p.add_argument("--category", default=None,
                   help="Label to tag output rows with. Defaults to what the URL says.")
    p.add_argument("--pages", type=env_config.PAGES, default=1, help="Number of pages to fetch")
    p.add_argument("--delay", type=env_config.SECONDS, default=2.0, help="Delay between pages, seconds")
    p.add_argument("--concurrency", type=env_config.CONCURRENCY, default=1, metavar="N",
                   help="Accepted for parity with the flag contract, but this "
                        "engine fetches one page at a time; N>1 warns and is "
                        "ignored. Use playwright_scraper.py for concurrency.")
    p.add_argument("--retries", type=env_config.ATTEMPTS, default=3,
                   help="Attempts per page load before giving up (default 3)")
    p.add_argument("--retry-delay", type=env_config.SECONDS, default=2.0,
                   help="Seconds before the first page-load retry (default 2.0)")
    p.add_argument("--format", choices=["json", "csv", "both"], default="both")
    p.add_argument("--out", default="lg_products", help="Output file prefix")
    p.add_argument("--proxy", default=None,
                   help="Proxy URL. Selenium cannot authenticate one: credentials "
                        "are stripped and the run warns.")
    p.add_argument("--proxy-file", default=None,
                   help="File with one proxy URL per line. Wins over --proxy.")
    p.add_argument("--proxy-rotate", choices=list(ROTATE_MODES), default="per-run",
                   help="per-run (default) or per-page, relaunching the browser.")
    p.add_argument("--proxy-shuffle", action="store_true",
                   help="Shuffle the pool at startup.")
    p.add_argument("--proxy-block-retries", type=env_config.EXTRA_ATTEMPTS, default=2,
                   help="Retries from OTHER exits when a page comes back blocked.")
    p.add_argument("--twocaptcha-key", default=None, help="2captcha.com API key")
    p.add_argument("--captcha-api", choices=["v2", "v1"], default="v2",
                   help="Which 2captcha solver API to use.")
    p.add_argument("--solve-captcha", choices=["when-blocked", "always"],
                   default="when-blocked",
                   help="when-blocked (default): only pay when the products are "
                        "not already readable.")
    p.add_argument("--min-score", type=env_config.SCORE, default=0.7,
                   help="reCAPTCHA v3 minimum score (0.3, 0.7 or 0.9).")
    p.add_argument("--allow-empty", action="store_true",
                   help="Write output files even when 0 products were found.")
    p.add_argument("--fingerprint", action="store_true",
                   help="Not implemented in this engine — Selenium cannot set a "
                        "context's locale, timezone and screen the way the "
                        "fingerprint needs. Warns and continues.")
    p.add_argument("--fp-tags", default="Windows",
                   help="ONE OS-family tag for the Fingerprint API.")
    p.add_argument("--fp-country", default=None, help="Fingerprint country, ISO alpha-2.")
    p.add_argument("--cdp-endpoint", default=None,
                   help="Attach to an already-running browser at host:port. An "
                        "endpoint carrying credentials is REFUSED: chromedriver "
                        "has nowhere to put them.")
    p.add_argument("--dump-html", default=None, metavar="PATH",
                   help="Save the exact HTML the parser is given, on success too.")
    p.add_argument("--headless", action="store_true", default=True)
    p.add_argument("--headful", dest="headless", action="store_false")
    args = p.parse_args()
    env_config.apply(args)
    if not args.url:
        p.error("no --url given, and LG_URL is not set in the environment "
                "or in .env.")
    if args.category is None:
        args.category = category_from_url(args.url)
    reason = unsupported_locale_reason(args.url)
    if reason:
        # Refusing WITH THE REASON: lg.com/uk and lg.com/us are different
        # platforms behind the same domain, and "no products found" would
        # send the reader hunting for a selector that was never there.
        p.error(f"this URL cannot be scraped by this repo — {reason}")
    return args


if __name__ == "__main__":
    args = parse_args()
    if args.fingerprint:
        logger.warning("--fingerprint is not implemented in the Selenium engine "
                       "— continuing without one. playwright_scraper.py applies "
                       "fingerprints; see fingerprint_client.py.")
    try:
        sys.exit(scrape(args))
    except ProxyError as e:
        logger.error("%s", e)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception:  # noqa: BLE001 — see the twin: a traceback is a log
        import traceback
        logger.error("Crashed:\n%s", redact_secret_patterns(traceback.format_exc()))
        sys.exit(1)
