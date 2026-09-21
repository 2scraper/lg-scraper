"""
output_writer.py
-----------------
Shared row model + JSON/CSV writers + the run-status/exit-code mapping used by
every engine in this repo.

Family core. The only per-site parts are the `source` constant and the
site-specific columns at the END of `Product` — the family prefix (source,
scraped_at, url, sku, title, price, currency, original_price, discount_pct,
category, price_source) keeps its names and its order so one consumer can
read every repo in the family.

Two family columns are deliberately ABSENT, each with the measurement behind
it rather than an opinion:

  brand     lg.com lists LG's own products and nothing else, so the column
            would read "LG" on every row of every run — a constant, not data.
  in_stock  This is a manufacturer catalogue, not a shop: no availability is
            published on a listing page, and the site's own "where to buy"
            link sends the shopper to partner retailers instead. That link is
            kept as its own column.

And one column that IS here although it was empty on every product measured:
`price`. The site publishes a price FIELD (microdata `price="0"` with
`priceCurrency="RUB"`, and nine numeric price fields in its catalogue API)
and runs a price service for it — and every one of them was 0 on all 36
products measured across two locales and two categories on 2026-09-21. A
published zero is not a price, so the column stays null; keeping it is what
makes the day LG turns prices on visible in a diff instead of invisible.
"""

import csv
import json
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from typing import Optional, List, Set

SOURCE = "lg.com"


@dataclass
class Product:
    # --- family prefix: same names, same order, across the whole family ----
    source: str = SOURCE
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    url: str = ""
    sku: Optional[str] = None          # LG's model code, e.g. OLED83W69LA
    title: Optional[str] = None        # the site's own long product name
    price: Optional[float] = None
    # No guessed default: a row whose currency could not be established says
    # so (None) rather than silently claiming USD.
    currency: Optional[str] = None
    original_price: Optional[float] = None
    discount_pct: Optional[float] = None
    category: Optional[str] = None     # the --category label for this run
    # Where the row came from, because the same columns can be filled from two
    # views with different confidence and nothing used to say which:
    #   "api"       — the site's own catalogue API
    #                 (retrieveCategoryProductList). 265 fields per product,
    #                 exactly the 12 the grid shows, plus the category names,
    #                 the sibling sizes and the site's own totalCount.
    #   "dom"        — the rendered grid card: microdata plus the site's
    #                 data-model-* attributes. The card does not carry the
    #                 category names or the energy label, so those stay null.
    price_source: Optional[str] = None
    # Which page of the listing this row came from, and its 1-based position
    # within that page. `position` restarts at 1 on every page, so the pair is
    # what is unique — see smoke_test.py, which asserts exactly that.
    page: Optional[int] = None
    position: Optional[int] = None

    # --- site-specific, at the end -----------------------------------------
    model_id: Optional[str] = None          # LG's internal id, e.g. MD07610675
    sales_model_code: Optional[str] = None  # the code with its market suffix
    model_year: Optional[int] = None
    screen_size: Optional[str] = None       # inches, as the site writes them
    sibling_sizes: Optional[str] = None     # the other sizes of the same model
    product_category: Optional[str] = None      # the site's own label, localised
    product_category_slug: Optional[str] = None # "televisions"
    super_category: Optional[str] = None        # "tv-audio-video"
    rating: Optional[float] = None
    review_count: Optional[int] = None
    status: Optional[str] = None            # "ACTIVE"
    energy_label: Optional[str] = None      # EU energy class, where published
    image_url: Optional[str] = None
    where_to_buy_url: Optional[str] = None  # this catalogue's checkout, such
                                            # as it is: a partner-retailer page


def dedupe_by_sku(products: List[Product], seen: Set[str]) -> List[Product]:
    """Drop products whose sku already appeared earlier in this same run.

    `seen` is mutated in place, so callers thread the same set across pages —
    a repeating page then re-parses without duplicating its rows into the
    final output. A product with no sku is always kept: there is nothing to
    key a duplicate check on, and dropping it would be a silent data loss
    rather than a duplicate removal.
    """
    fresh = []
    for p in products:
        if p.sku is None or p.sku not in seen:
            if p.sku is not None:
                seen.add(p.sku)
            fresh.append(p)
    return fresh


def write_json(products: List[Product], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([asdict(p) for p in products], f, ensure_ascii=False, indent=2)


def write_csv(products: List[Product], path: str) -> None:
    # An empty result still gets the header row. A zero-byte file makes a
    # consumer fail on read (no columns to parse) instead of reading a valid
    # table with zero rows.
    if not products:
        with open(path, "w", encoding="utf-8", newline="") as f:
            csv.DictWriter(f, fieldnames=list(asdict(Product()).keys())).writeheader()
        return
    fieldnames = list(asdict(products[0]).keys())
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in products:
            writer.writerow(asdict(p))


# Exit code used when a run completes but produced nothing. Distinct from 1
# (crash) so a caller can tell "ran, found nothing" from "blew up".
EXIT_NO_PRODUCTS = 4

# Exit code for a run blocked by a bot-check/challenge page before parsing
# even started — distinct from EXIT_NO_PRODUCTS so a caller can tell "the
# search genuinely matched nothing" from "something stood between us and the
# content". See page_flow.classify.
EXIT_BLOCKED = 3

# Exit code for a run that gathered SOME listings and then stopped early — a
# page-load timeout, or a challenge, on page 3 of 10. The output file is still
# written (throwing away two good pages would be worse), but it is not a
# complete picture, and a consumer that cannot tell the difference will read
# the pages that were never fetched as products that were delisted.
EXIT_PARTIAL = 6


def write_run_meta(out_prefix: str, meta: dict) -> str:
    """Write a run-metadata sidecar next to the output, return its path.

    Deliberately a separate `<out>.meta.json` rather than columns on every
    row: this describes the RUN, not the listing, and repeating it across 25
    identical rows would both bloat the output and change the schema every
    consumer already parses.

    diff_runs.py reads it to refuse a comparison between runs that are not
    both complete — the failure mode it exists to prevent is a partial run's
    un-fetched pages being reported as delisted listings.
    """
    path = f"{out_prefix}.meta.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print(f"[+] Wrote run metadata -> {path} (status={meta.get('status')})")
    return path


def run_meta(status: str, stop_reason: str, pages_requested: int,
             pages_completed: int, start_url: str, final_url: str,
             products: int, pages_failed: Optional[List[int]] = None,
             total_results: Optional[int] = None,
             addressable: Optional[bool] = None) -> dict:
    """Build the metadata dict for a finished run.

    `status` is the field a consumer branches on:
      complete — every requested page was fetched, or the listing genuinely
                 ran out (nothing more existed to get)
      partial  — rows were gathered, then the run stopped early
      failed   — nothing was gathered at all

    `pages_failed` lists the pages that did not yield data, BY NUMBER: a count
    stops being a description once pages can be fetched independently and page
    3 can fail while 4 and 5 succeed.

    `total_results` is the category's own product count, which makes "did we
    get everything?" checkable rather than a guess. It comes from page 1: a
    page past the end reports 0, which describes that response rather than
    the category.

    `addressable` records whether pages 2..N could be addressed by URL at all
    (see product_parser.page_url): False means the run had to chain, and that
    --concurrency was refused for this listing.
    """
    return {
        "source": SOURCE,
        "status": status,
        "stop_reason": stop_reason,
        "pages_requested": pages_requested,
        "pages_completed": pages_completed,
        "pages_failed": pages_failed or [],
        "products": products,
        "total_results": total_results,
        "addressable": addressable,
        "start_url": start_url,
        "final_url": final_url,
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def save(products: List[Product], out_prefix: str, fmt: str,
         allow_empty: bool = False) -> int:
    """Write JSON/CSV and return a process exit code.

    On zero products, nothing is written at all unless `allow_empty`. A
    page-load timeout that writes `[]` and exits 0 is read by a consuming
    pipeline as a successful run with no listings — and if the file already
    held a good result, that result is now gone: the failure destroyed the
    last known good data.

    `allow_empty=True` is for the legitimate case: a filter that genuinely
    matches nothing — a category the site itself reports as empty — where an
    empty file is the answer.
    """
    if not products and not allow_empty:
        print(f"[!] 0 products — refusing to write {out_prefix}.json/.csv, so an "
              f"earlier good result isn't overwritten with an empty one. "
              f"Pass --allow-empty if an empty result is the expected answer.")
        return EXIT_NO_PRODUCTS

    if fmt in ("json", "both"):
        write_json(products, f"{out_prefix}.json")
        print(f"[+] Saved {len(products)} products -> {out_prefix}.json")
    if fmt in ("csv", "both"):
        write_csv(products, f"{out_prefix}.csv")
        print(f"[+] Saved {len(products)} products -> {out_prefix}.csv")
    return 0 if products else EXIT_NO_PRODUCTS


# Stop reasons that mean the run saw everything there was to see. Anything
# else ended the page loop early, so the result is only a partial view.
#
# "no_new_products" and "listing_exhausted" are both properties of the DATA —
# a page that added nothing new, and the site's own empty grid past the end of
# the category. There is deliberately no selector-based entry: a missing
# "next" control is a property of markup, an exhausted catalogue is a property
# of the data.
COMPLETE_STOP_REASONS = ("completed", "listing_exhausted", "no_new_products",
                         "no_results")


def finish_run(products: List[Product], out_prefix: str, fmt: str,
               allow_empty: bool, *, blocked: bool, stop_reason: str,
               pages_requested: int, pages_completed: int,
               start_url: str, final_url: str,
               pages_failed: Optional[List[int]] = None,
               total_results: Optional[int] = None,
               addressable: Optional[bool] = None) -> int:
    """Write output + the run-metadata sidecar; return the exit code.

    Shared by all engines so the status/exit-code mapping cannot drift between
    them.

    The metadata sidecar is written ONLY when the output file was written.
    Otherwise a failed run would leave a "status": "failed" sidecar next to
    the previous run's still-intact good output (which `save` deliberately
    does not overwrite) — the two files would contradict each other.
    """
    complete = stop_reason in COMPLETE_STOP_REASONS
    rc = save(products, out_prefix, fmt, allow_empty=allow_empty)
    wrote_output = bool(products) or allow_empty

    if wrote_output:
        status = "complete" if (products and complete) else (
            "partial" if products else "failed")
        if not products and allow_empty and complete:
            # An explicitly empty result that the site itself reported as
            # empty is a complete answer, not a failure.
            status = "complete"
        write_run_meta(out_prefix, run_meta(
            status=status, stop_reason=stop_reason,
            pages_requested=pages_requested, pages_completed=pages_completed,
            pages_failed=pages_failed, total_results=total_results,
            addressable=addressable,
            start_url=start_url, final_url=final_url, products=len(products)))

    if not products:
        # Nothing gathered at all: a challenge outranks "empty listing",
        # because it says something stood between the run and the content.
        return EXIT_BLOCKED if blocked else rc
    if not complete:
        print(f"[!] Partial run: stopped after {pages_completed} of "
              f"{pages_requested} page(s) ({stop_reason}). The output holds "
              f"what was gathered, but it is NOT a complete view — see "
              f"{out_prefix}.meta.json.")
        return EXIT_PARTIAL
    return rc
