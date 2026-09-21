#!/usr/bin/env python3
"""
lg-scraper — catalogue API edition (primary engine, no browser)
================================================================

lg.com publishes its own catalogue API, and the category page describes the
call in a form element:

    <form id="categoryFilterForm"
          action="/ru/mkt/ajax/category/retrieveCategoryProductList"
          method="post">
      <input name="categoryId" value="CT20206007">
      <input name="page" value="1">  <input name="bizType" value="B2C">  …

This engine reads that form off the page and then pages through the API. One
plain HTTPS POST per page, 12 products each, 265 fields per product, and the
category's own `totalCount` — no browser, no key, no proxy. Measured
2026-09-21 against `/ru/televisions` and `/ua/televisions`.

**The parameters are read from the page, never hardcoded.** `categoryId`
differs per category AND per locale (CT20206007 for RU televisions,
CT20226005 for UA televisions, CT20206048 for RU refrigerators), so a
hardcoded id would quietly scrape a different catalogue than the URL asked
for — and would report a full, successful run while doing it.

The three browser engines exist for the case this one cannot cover: an exit
that Akamai refuses (lg.com/us answered 403 to the same client that /ru
served), or a future page kind that needs rendering. They read the rendered
grid instead; `price_source` on every row says which path produced it.

Usage
-----
    python3 catalog_client.py --url "https://www.lg.com/ru/televisions" \\
        --pages 3 --out tv_products --format both

Requires: pip install -r requirements.txt   (no browser engine needed)
"""

import argparse
import json
import logging
import sys
import time
from typing import List, Optional, Tuple

import requests

import env_config
import page_flow
from output_writer import dedupe_by_sku, finish_run
from product_parser import (api_page_count, api_products, api_row_count,
                            api_variant_count, catalog_payload,
                            category_from_url, declared_currency, page_url,
                            parse_catalog_form, rows_from_api,
                            unsupported_locale_reason)
from proxy_pool import (ROTATE_MODES, ProxyError, check_exit_or_raise,
                        from_args as proxy_pool_from_args, mask,
                        redact_secret_patterns)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("catalog_client")

# A browser's own header set, minus the fingerprinting surface. The API
# answers without any of this, but a bare python-requests UA is the one
# thing an edge is certain to notice.
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 "
                   "Safari/537.36"),
    "Accept-Language": "ru,en;q=0.9",
}
AJAX_HEADERS = {**HEADERS, "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01"}


def _session(pool) -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    if pool:
        exit_url = pool.current
        session.proxies.update({"http": exit_url, "https": exit_url})
        logger.info("Using proxy exit %s", mask(exit_url))
    return session


def fetch_category_page(session: requests.Session, url: str,
                        timeout: float) -> Tuple[Optional[str], Optional[int]]:
    """GET the category page. Returns (html, status) — never raises for a refusal."""
    try:
        response = session.get(url, timeout=timeout)
    except requests.RequestException as e:
        logger.error("Could not fetch %s: %s", url, redact_secret_patterns(str(e)))
        return None, None
    return response.text, response.status_code


def fetch_api_page(session: requests.Session, form: dict, page_num: int,
                   referer: str, timeout: float) -> Tuple[Optional[dict], Optional[int]]:
    """POST one page of the catalogue API. Returns (payload, status)."""
    try:
        response = session.post(form["url"], data=catalog_payload(form, page_num),
                                headers={**AJAX_HEADERS, "Referer": referer},
                                timeout=timeout)
    except requests.RequestException as e:
        logger.error("Catalogue API request failed: %s",
                     redact_secret_patterns(str(e)))
        return None, None
    if response.status_code != 200:
        return None, response.status_code
    try:
        return response.json(), response.status_code
    except json.JSONDecodeError:
        logger.error("The catalogue API answered %d with %d bytes that are not "
                     "JSON — treating the page as unusable.",
                     response.status_code, len(response.content))
        return None, response.status_code


def scrape(args) -> int:
    pool = proxy_pool_from_args(args)
    check_exit_or_raise(pool, args.url)
    session = _session(pool)

    html, status = fetch_category_page(session, args.url, args.timeout)
    if html is None:
        return finish_run([], args.out, args.format, args.allow_empty,
                          blocked=False, stop_reason="page_load_timeout",
                          pages_requested=args.pages, pages_completed=0,
                          start_url=args.url, final_url=args.url)

    state = page_flow.classify(html, status_code=status, url=args.url, page_num=1)
    if state.policy.blocked:
        _dump(args, 1, html)
        logger.error("Blocked before parsing (%s) — this is exit 3, distinct "
                     "from a category that is genuinely empty (exit 4).",
                     state.reason)
        return finish_run([], args.out, args.format, args.allow_empty,
                          blocked=True, stop_reason=f"blocked_{state.vendor}",
                          pages_requested=args.pages, pages_completed=0,
                          start_url=args.url, final_url=args.url)

    form = parse_catalog_form(html, args.url)
    if form is None:
        _dump(args, 1, html)
        logger.error("This page carries no #categoryFilterForm, so there is no "
                     "catalogue API to call. Either the page is not a category "
                     "listing, or the site has changed the form this engine "
                     "reads its parameters from — use a browser engine "
                     "meanwhile, they read the rendered grid.")
        return 4
    currency = declared_currency(html)
    logger.info("Catalogue %s via %s (currency declared: %s)",
                form["params"].get("categoryId"), form["url"], currency or "none")

    all_rows: List = []
    seen = set()
    total_results = None
    page_count = None
    stop_reason = "completed"
    pages_completed = 0
    pages_failed: List[int] = []

    for page_num in range(1, args.pages + 1):
        if page_count is not None and page_num > page_count:
            # The API told us on page 1 how many pages it has. Asking for one
            # past that returns an empty page, which is a request spent to
            # learn something already known.
            logger.info("The category has %d page(s); stopping there rather "
                        "than asking for page %d.", page_count, page_num)
            stop_reason = "listing_exhausted"
            break
        if page_num > 1:
            time.sleep(args.delay)
        payload = None
        for attempt in range(1, args.retries + 1):
            payload, api_status = fetch_api_page(session, form, page_num,
                                                 args.url, args.timeout)
            if payload is not None:
                break
            if attempt < args.retries:
                pause = args.retry_delay * (2 ** (attempt - 1))
                logger.warning("Catalogue API page %d failed (HTTP %s) — "
                               "retrying in %.1fs.", page_num, api_status, pause)
                time.sleep(pause)
        if payload is None:
            pages_failed.append(page_num)
            stop_reason = "api_error"
            break

        records = api_products(payload)
        if page_num == 1:
            total_results = api_row_count(payload)
            page_count = api_page_count(payload)
            variants = api_variant_count(payload)
            # Two different numbers, and only the first one is a product
            # count: `pageInfo.totalCount` is what the API pages through,
            # while the `totalCount` beside the product list counts size
            # variants across those rows (measured: the sibling sizes of the
            # 50 RU television rows add up to exactly its 203).
            logger.info("The category pages through %s product(s) in %s "
                        "page(s)%s.", total_results, page_count,
                        f" — {variants} size variants across them"
                        if variants else "")
        state = page_flow.classify("", url=args.url, page_num=page_num,
                                   record_count=len(records),
                                   total_results=total_results)
        logger.info("Page %d is %s.", page_num, state)
        if state.policy.blocked:
            # Not reachable from an empty payload — page_flow answers a JSON
            # caller on the record count alone. It is reachable if the API
            # ever starts answering with a refusal the parser can read, and
            # a run that stops on one is partial, never "complete, no more
            # products".
            logger.error("Page %d came back blocked (%s) — stopping here "
                         "rather than reporting the run as finished.",
                         page_num, state.reason)
            pages_failed.append(page_num)
            stop_reason = f"blocked_{state.vendor}"
            break
        if args.dump_html:
            path = (args.dump_html if args.pages == 1
                    else f"{args.dump_html}.page{page_num}")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=1)
            logger.info("Saved the exact API response to %s.", path)

        if state.policy.complete:
            stop_reason = ("no_results" if state.state == page_flow.EMPTY
                           else "listing_exhausted")
            pages_completed += 1
            break

        rows = rows_from_api(payload, args.url, category=args.category,
                             page=page_num, currency=currency)
        short = page_flow.shortfall(total_results, page_num, len(rows))
        if short:
            logger.warning("%s.", short)

        fresh = [r for r in rows if r.sku is None or r.sku not in seen]
        seen.update(r.sku for r in rows if r.sku)
        all_rows.extend(rows)
        pages_completed += 1
        if not fresh:
            # A page that contributes nothing new means the end of the
            # catalogue — or pagination looping back on itself. Either way
            # there is nothing further to fetch, and this is a property of
            # the DATA rather than of a parameter we guessed.
            logger.info("Page %d added nothing not already seen — treating "
                        "that as the end of the category.", page_num)
            stop_reason = "no_new_products"
            break

    merged, merged_seen = [], set()
    for row in all_rows:
        merged.extend(dedupe_by_sku([row], merged_seen))

    return finish_run(merged, args.out, args.format, args.allow_empty,
                      blocked=False, stop_reason=stop_reason,
                      pages_requested=args.pages, pages_completed=pages_completed,
                      pages_failed=pages_failed, total_results=total_results,
                      addressable=True,
                      start_url=args.url, final_url=page_url(args.url, pages_completed))


def _dump(args, page_num: int, html: str) -> None:
    path = f"{args.out}_page{page_num}_debug.html"
    with open(path, "w", encoding="utf-8") as f:
        f.write(html)
    logger.warning("Wrote %s so you can see what actually came back.", path)


def parse_args():
    p = argparse.ArgumentParser(
        description="LG catalogue scraper — the site's own API, no browser")
    p.add_argument("--url", default=None,
                   help="An lg.com/ru or lg.com/ua category URL, e.g. "
                        "https://www.lg.com/ru/televisions. Required unless "
                        "LG_URL is set in the environment or in .env.")
    p.add_argument("--category", default=None,
                   help="Label to tag output rows with. Defaults to the "
                        "category segment of the URL.")
    p.add_argument("--pages", type=int, default=1,
                   help="Number of pages to fetch (12 products each)")
    p.add_argument("--delay", type=float, default=1.0,
                   help="Delay between pages, seconds (default 1.0)")
    p.add_argument("--retries", type=int, default=3,
                   help="Attempts per page before giving up (default 3); the "
                        "pause between attempts doubles each time.")
    p.add_argument("--retry-delay", type=float, default=2.0,
                   help="Seconds before the first retry (default 2.0)")
    p.add_argument("--timeout", type=float, default=30.0,
                   help="Per-request timeout, seconds (default 30)")
    p.add_argument("--format", choices=["json", "csv", "both"], default="both")
    p.add_argument("--out", default="lg_products", help="Output file prefix")
    p.add_argument("--proxy", default=None,
                   help="Proxy URL, e.g. http://ACCOUNT:PASSWORD@HOST:9999")
    p.add_argument("--proxy-file", default=None,
                   help="File with one proxy URL per line. Wins over --proxy.")
    p.add_argument("--proxy-rotate", choices=list(ROTATE_MODES), default="per-run")
    p.add_argument("--proxy-shuffle", action="store_true",
                   help="Shuffle the pool at startup.")
    p.add_argument("--allow-empty", action="store_true",
                   help="Write output files even when 0 products were found.")
    p.add_argument("--dump-html", default=None, metavar="PATH",
                   help="Save the exact API responses, on success as well as "
                        "failure. (JSON here, not HTML — the flag keeps the "
                        "family's name so one wrapper script drives every "
                        "engine.)")
    args = p.parse_args()
    env_config.apply(args)
    if not args.url:
        p.error("no --url given, and LG_URL is not set in the environment "
                "or in .env.")
    if args.category is None:
        args.category = category_from_url(args.url)
    reason = unsupported_locale_reason(args.url)
    if reason:
        p.error(f"this URL cannot be scraped by this repo — {reason}")
    return args


if __name__ == "__main__":
    try:
        sys.exit(scrape(parse_args()))
    except ProxyError as e:
        logger.error("%s", e)
        sys.exit(2)
    except KeyboardInterrupt:
        sys.exit(1)
    except Exception:  # noqa: BLE001 — a traceback is a log
        import traceback
        logger.error("Crashed:\n%s", redact_secret_patterns(traceback.format_exc()))
        sys.exit(1)
