#!/usr/bin/env python3
#!/usr/bin/env python3
"""
lg-scraper — 2Captcha Scraper API edition (fourth engine)
===========================================================

Fetches the category page through 2Captcha's Scraper API
(https://scraper.2captcha.com) — no browser and no CDP session of its own —
and reads the rendered grid out of the HTML that comes back.

**When you need it.** `catalog_client.py` is the cheaper path on this site:
LG's own catalogue API answers a plain HTTPS POST with no key at all. This
engine exists for the case that path cannot cover — an exit Akamai refuses.
Measured 2026-09-21: `lg.com/ru` served an ordinary request from an ordinary
connection, while `lg.com/us` answered the same client `403 Access Denied`
from Akamai. When your address is the problem, fetching through someone
else's is the answer, and that is what this buys.

It reads the GRID, not the API, so its rows carry what a card carries: the
model code, the title, the URL, the image, the year and the review counters,
but not the category names, the sibling sizes or the energy label.
`price_source` on every row says `dom`, so the two paths never look like a
data change.

API surface used (per https://2captcha.com/scraper/scraper-api/api)
-------------------------------------------------------------------
  POST https://scraper.2captcha.com/tasks/sync
    Authorization: Bearer <API_KEY>
    {"task_type": "scrape", "url": ..., "data_format": "raw",
     "format": "json", "timeout": 1..120,
     "waitFor": "<JSON *string*, not an object>",
     "cdpurl": "ws://user:pass@host:port"}
  -> 200 {"status": 200, "headers": {...}, "body": "<!DOCTYPE html>..."}

`waitFor` must be a JSON *string* (double-encoded), and the parameter is
spelled `cdpurl` (all lowercase) while `waitFor` is camelCase — the API's
own inconsistency, not a typo here.

Usage
-----
    python3 scraper_api_client.py --key "$TWOCAPTCHA_KEY" \\
        --url "https://www.lg.com/ru/televisions" --pages 3 --out tv_products

Requires: pip install -r requirements.txt
"""

import argparse
import json
import logging
import os
import sys
import time
from typing import List, Optional, Tuple

import requests

import env_config
import page_flow
from output_writer import dedupe_by_sku, finish_run
from product_parser import (category_from_url, page_url, parse_products,
                            unsupported_locale_reason)
from proxy_pool import redact_secret_patterns

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("scraper_api_client")

API_BASE = "https://scraper.2captcha.com"
SYNC_ENDPOINT = f"{API_BASE}/tasks/sync"

# The API caps `timeout` at 120s.
MAX_API_TIMEOUT = 120

# Kept distinct from 2 (bad usage) on purpose: a remote API failing is not the
# operator passing wrong arguments, and a harness that lumps them together
# sends you looking in the wrong place.
EXIT_API_ERROR = 5


def _build_wait_for(args) -> Optional[str]:
    """`waitFor` as the JSON STRING the API wants, or None.

    Not needed on lg.com (see the module docstring); kept because a
    future page kind, or a run routed through `--cdp-url`, may want it.
    """
    if args.wait_text:
        return json.dumps({"text": args.wait_text})
    if args.wait_element:
        return json.dumps({"element": args.wait_element, "checkVisible": True})
    if args.wait_state:
        return json.dumps({"state": args.wait_state})
    return None


def fetch_html(args, url: str) -> Tuple[str, Optional[int]]:
    """One Scraper API task. Returns (html, upstream_status)."""
    payload = {
        "task_type": "scrape",
        "url": url,
        "data_format": "raw",    # we want HTML; product_parser does the rest
        "format": "json",        # so we get {"status", "headers", "body"}
        "timeout": min(args.timeout, MAX_API_TIMEOUT),
    }
    wait_for = _build_wait_for(args)
    if wait_for:
        payload["waitFor"] = wait_for
        logger.info("waitFor: %s", wait_for)
    if args.cdp_url:
        payload["cdpurl"] = args.cdp_url
        logger.info("Routing through an existing browser session: %s",
                    redact_secret_patterns(args.cdp_url))

    logger.info("POST %s (url=%s)", SYNC_ENDPOINT, url)
    resp = requests.post(
        SYNC_ENDPOINT,
        headers={"Authorization": f"Bearer {args.key}",
                 "Content-Type": "application/json"},
        json=payload,
        # More headroom than the API-side task timeout, so a task that
        # legitimately runs the full 120s does not look like a network failure.
        timeout=min(args.timeout, MAX_API_TIMEOUT) + 30,
    )

    # The API returns its per-task metadata (price, timings, status) in an
    # x-debug header — the only place the real cost of the call shows up.
    debug = resp.headers.get("x-debug")
    if debug:
        logger.info("x-debug: %s", debug)

    if resp.status_code != 200:
        # 422 = the task ran and errored (an unreachable cdpurl does this);
        # 402 = out of balance; 408 = the sync wait was exceeded.
        raise RuntimeError(
            f"Scraper API returned HTTP {resp.status_code}: "
            f"{redact_secret_patterns(resp.text[:500])}")

    body = resp.json()
    html = body.get("body") or ""
    upstream = body.get("status")
    logger.info("Upstream page status %s, %d bytes of HTML.", upstream, len(html))
    return html, upstream if isinstance(upstream, int) else None


def _fetch_page(args, page_num: int, url: str):
    """Fetch and classify one page; returns (state, products) or (None, []).

    A challenge page is not a final answer, so a blocked response is retried
    `--retries` times before the run concludes anything — each attempt is a
    separate billable task, which is why the default is low.
    """
    for attempt in range(1, max(1, args.retries + 1) + 1):
        try:
            html, upstream = fetch_html(args, url)
        except requests.RequestException as e:
            logger.error("Network error talking to the Scraper API: %s",
                         redact_secret_patterns(str(e)))
            return None, []
        except RuntimeError as e:
            logger.error("%s", e)
            return None, []

        if args.dump_html:
            path = args.dump_html if args.pages == 1 else f"{args.dump_html}.page{page_num}"
            with open(path, "w", encoding="utf-8") as f:
                f.write(html)
            logger.info("Raw HTML written to %s", path)

        state = page_flow.classify(html, status_code=upstream, url=url, page_num=page_num)
        logger.info("Page %d is %s.", page_num, state)

        if state.policy.blocked and attempt <= args.retries:
            logger.warning("Blocked (%s) on attempt %d — retrying in %ds. A "
                           "challenge page is not a final answer.",
                           state.reason, attempt, args.retry_delay)
            time.sleep(args.retry_delay)
            continue

        if state.policy.blocked:
            dump = f"{args.out}_page{page_num}_debug.html"
            with open(dump, "w", encoding="utf-8") as f:
                f.write(html)
            logger.error("Blocked before parsing (%s) — saved the response to "
                         "%s. This is exit 3, distinct from a query that "
                         "genuinely matches nothing (exit 4).", state.reason, dump)
            return state, []

        if state.state in (page_flow.EMPTY, page_flow.EXHAUSTED):
            return state, []

        products = parse_products(html, url, category=args.category, page=page_num)
        logger.info("Parsed %d product(s) from page %d.", len(products), page_num)
        short = page_flow.shortfall(state.total_results, page_num, len(products))
        if short:
            logger.warning("%s.", short)
        if not products:
            dump = f"{args.out}_page{page_num}_debug.html"
            with open(dump, "w", encoding="utf-8") as f:
                f.write(html)
            logger.warning("0 products parsed from a page classified as %s — "
                           "saved the raw response to %s.", state.state, dump)
        return state, products
    return None, []


def scrape(args) -> int:
    outcomes: List[Tuple[int, object, list]] = []
    blocked = False
    stop_reason = "completed"
    total_results = None
    addressable = None

    state, products = _fetch_page(args, 1, args.url)
    outcomes.append((1, state, products))
    if state is not None:
        total_results = state.total_results

    if state is None:
        stop_reason = "api_error"
    elif state.policy.blocked:
        stop_reason = f"blocked_{state.vendor}"
        blocked = True
    elif state.policy.complete:
        stop_reason = ("no_results" if state.state == page_flow.EMPTY
                       else "listing_exhausted")
    elif args.pages > 1:
        seen = {p.sku for p in products if p.sku}
        first_products = products
        for page_num in range(2, args.pages + 1):
            time.sleep(args.delay)
            state, products = _fetch_page(args, page_num, page_url(args.url, page_num))
            if state is None:
                stop_reason = "api_error"
                break
            outcomes.append((page_num, state, products))
            if state.policy.blocked:
                stop_reason = f"blocked_{state.vendor}"
                blocked = True
                break
            if state.policy.complete or not products:
                stop_reason = "listing_exhausted"
                break
            if page_num == 2:
                # The same addressability check the browser engines run: this
                # site publishes no next-page link, so the constructed URL is
                # verified against the DATA before more pages are planned.
                addressable = any(p.sku and p.sku not in
                                  {q.sku for q in first_products if q.sku}
                                  for p in products)
                if not addressable:
                    logger.error("Page 2 returned nothing page 1 did not already "
                                 "have — this listing cannot be paged through by "
                                 "URL. Stopping and reporting a PARTIAL run.")
                    outcomes.pop()
                    stop_reason = "pagination_not_addressable"
                    break
            fresh = [p for p in products if p.sku is None or p.sku not in seen]
            seen.update(p.sku for p in products if p.sku)
            if not fresh:
                logger.info("Page %d added nothing not already seen — treating "
                            "that as the end of the listing.", page_num)
                stop_reason = "no_new_products"
                break

    all_products = []
    merged_seen = set()
    for page_num, _state, products in sorted(outcomes, key=lambda o: o[0]):
        all_products.extend(dedupe_by_sku(products, merged_seen))

    ok_pages = [o for o in outcomes if o[1] is not None and not o[1].policy.blocked]
    failed = [o[0] for o in outcomes if o[1] is None or o[1].policy.blocked]
    return finish_run(all_products, args.out, args.format, args.allow_empty,
                      blocked=blocked, stop_reason=stop_reason,
                      pages_requested=args.pages, pages_completed=len(ok_pages),
                      pages_failed=failed, total_results=total_results,
                      addressable=addressable,
                      start_url=args.url, final_url=args.url)


def parse_args():
    p = argparse.ArgumentParser(
        description="LG catalogue scraper — 2Captcha Scraper API edition (no local browser)")
    # NOT required as a flag: a key on the command line is visible to anything
    # that can run `ps` and lands in shell history.
    p.add_argument("--key", default=os.environ.get("TWOCAPTCHA_KEY"),
                   help="2captcha.com API key, sent as a Bearer token. Defaults "
                        "to $TWOCAPTCHA_KEY, which is the safer way to pass it.")
    p.add_argument("--url", default=None,
                   help="An lg.com/ru or lg.com/ua category URL. Required unless LG_URL is set.")
    p.add_argument("--category", default=None,
                   help="Label to tag output rows with. Defaults to what the URL says.")
    p.add_argument("--pages", type=env_config.PAGES, default=1, help="Number of pages to fetch")
    p.add_argument("--delay", type=env_config.SECONDS, default=1.0,
                   help="Delay between pages, seconds (default 1.0)")
    p.add_argument("--format", choices=["json", "csv", "both"], default="both")
    p.add_argument("--out", default="lg_products", help="Output file prefix")
    p.add_argument("--timeout", type=env_config.int_range(1, 600), default=60,
                   help=f"API-side task timeout in seconds (1-{MAX_API_TIMEOUT}, default 60)")
    p.add_argument("--cdp-url", default=None,
                   help="Route the fetch through an existing browser session over "
                        "CDP (the API's `cdpurl` parameter). Not needed on this "
                        "site — the listing data is in the served HTML.")
    wait = p.add_mutually_exclusive_group()
    wait.add_argument("--wait-text", default=None,
                      help="Wait until this string appears on the page.")
    wait.add_argument("--wait-element", default=None,
                      help="Wait until this CSS selector is visible, e.g. "
                           "'.product-list-box .item'.")
    wait.add_argument("--wait-state", choices=["load", "domcontentloaded"], default=None,
                      help="Wait for a page load state instead of specific content")
    p.add_argument("--allow-empty", action="store_true",
                   help="Write output files even when 0 products were parsed.")
    p.add_argument("--retries", type=env_config.EXTRA_ATTEMPTS, default=1,
                   help="Extra attempts if a challenge page comes back. Each "
                        "attempt is a separate billable task, so this defaults to 1.")
    p.add_argument("--retry-delay", type=env_config.SECONDS, default=10,
                   help="Seconds between retries (default 10)")
    p.add_argument("--dump-html", default=None,
                   help="Also write the raw returned HTML, on success as well as failure")
    args = p.parse_args()
    # This client uses --key and --cdp-url rather than --twocaptcha-key and
    # --cdp-endpoint, so the mapping is spelled out instead of defaulted.
    env_config.apply(args, keys={
        "TWOCAPTCHA_KEY": "key",
        "LG_CDP_ENDPOINT": "cdp_url",
        "LG_URL": "url",
    })
    if not args.url:
        p.error("no --url given, and LG_URL is not set in the environment "
                "or in .env.")
    if args.category is None:
        args.category = category_from_url(args.url)
    reason = unsupported_locale_reason(args.url)
    if reason:
        p.error(f"this URL cannot be scraped by this repo — {reason}")
    return args


def main() -> int:
    args = parse_args()
    if not args.key:
        logger.error("No 2captcha API key. Pass --key, or better, export "
                     "TWOCAPTCHA_KEY.")
        return 2
    return scrape(args)


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
