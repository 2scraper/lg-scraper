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
import os
import tempfile
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


def _atomic_write(path: str, write, newline: Optional[str] = None) -> None:
    """Write `path` via a temporary file in the same directory, then rename.

    Opening the real path with "w" truncates it first, so a run killed while
    writing leaves last night's good output as a half-written file — exactly
    what `save` refusing to write an empty result exists to prevent. A rename
    within one directory is atomic: a reader sees the old file or the new
    one, never a torn one. The parent directory is created, so `--out
    results/tv` does not fail after every page has been fetched.
    """
    parent = os.path.dirname(os.path.abspath(path))
    os.makedirs(parent, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=parent, prefix=".tmp-",
                               suffix=os.path.basename(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline=newline) as f:
            write(f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_json(products: List[Product], path: str) -> None:
    _atomic_write(path, lambda f: json.dump([asdict(p) for p in products], f,
                                            ensure_ascii=False, indent=2))


def write_csv(products: List[Product], path: str) -> None:
    # An empty result still gets the header row. A zero-byte file makes a
    # consumer fail on read (no columns to parse) instead of reading a valid
    # table with zero rows.
    fieldnames = list(asdict(products[0] if products else Product()).keys())

    def write(f):
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for p in products:
            writer.writerow(asdict(p))
    _atomic_write(path, write, newline="")


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
    _atomic_write(path, lambda f: json.dump(meta, f, ensure_ascii=False, indent=2))
    print(f"[+] Wrote run metadata -> {path} (status={meta.get('status')})")
    return path


def run_meta(status: str, stop_reason: str, pages_requested: int,
             pages_completed: int, start_url: str, final_url: str,
             products: int, pages_failed: Optional[List[int]] = None,
             total_results: Optional[int] = None,
             addressable: Optional[bool] = None,
             page_count: Optional[int] = None) -> dict:
    """Build the metadata dict for a finished run.

    `status` is the field a consumer branches on:
      complete — the WHOLE listing was read: the site ran out of products,
                 every page it reported was fetched, or the product count
                 reached its own total
      limited  — every page asked for was fetched, but --pages stopped the
                 run before the listing was shown to end. A successful run
                 (exit 0) and a sample, never an assortment snapshot
      partial  — rows were gathered, then the run stopped early
      failed   — nothing was gathered at all

    `listing_complete` is the same fact as `status == "complete"`, kept as a
    boolean so a consumer does not have to know every status word, and
    `scope` says whether the run was a full listing or a sample of it.

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
        "schema_version": 2,
        "source": SOURCE,
        "status": status,
        "listing_complete": status == "complete",
        "scope": "limited_pages" if status == "limited" else "full_listing",
        "stop_reason": stop_reason,
        "pages_requested": pages_requested,
        "pages_completed": pages_completed,
        "pages_failed": pages_failed or [],
        "products": products,
        "total_results": total_results,
        "page_count": page_count,
        "completeness_ratio": (round(products / total_results, 4)
                               if total_results else None),
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


# Stop reasons that PROVE the run saw everything there was to see — each is
# a property of the DATA: the site's own empty grid past the end of the
# category, a page that added nothing new, a category with no results at all.
# There is deliberately no selector-based entry: a missing "next" control is
# a property of markup, an exhausted catalogue is a property of the data.
#
# "completed" is deliberately NOT here. Engines start from it and keep it
# when the page loop simply ran out of --pages, which says the REQUESTED
# range was read, not that the listing was. Until v0.2.0 it counted as
# complete, so `--pages 2` of a 4-page category wrote status=complete and
# diff_runs.py reported the unread half as 24 delisted models. finish_run now
# decides what "completed" means from the evidence — see _range_covers_listing.
COMPLETE_STOP_REASONS = ("listing_exhausted", "no_new_products", "no_results")

# What the loop reports when it used up --pages: a finished task, not a
# failed one, so it stays exit 0 — but not a complete listing either.
RANGE_DONE = "completed"


def _range_covers_listing(products: int, pages_completed: int,
                          total_results: Optional[int],
                          page_count: Optional[int]) -> bool:
    """Did reading the requested pages happen to read the whole listing?

    Two proofs, both the site's own numbers: every page it said it has was
    fetched, or the distinct products reached the total it pages through.
    Without either, a run that stopped because --pages ran out has no
    evidence the next page was empty, and must not claim it was.
    """
    if page_count is not None and pages_completed >= page_count:
        return True
    return total_results is not None and total_results > 0 and products >= total_results


def finish_run(products: List[Product], out_prefix: str, fmt: str,
               allow_empty: bool, *, blocked: bool, stop_reason: str,
               pages_requested: int, pages_completed: int,
               start_url: str, final_url: str,
               pages_failed: Optional[List[int]] = None,
               total_results: Optional[int] = None,
               addressable: Optional[bool] = None,
               page_count: Optional[int] = None) -> int:
    """Write output + the run-metadata sidecar; return the exit code.

    Shared by all engines so the status/exit-code mapping cannot drift between
    them.

    The metadata sidecar is written ONLY when the output file was written.
    Otherwise a failed run would leave a "status": "failed" sidecar next to
    the previous run's still-intact good output (which `save` deliberately
    does not overwrite) — the two files would contradict each other.

    Order matters when it IS written: the old sidecar is removed first and the
    new one is written last. A run killed between the two then leaves new
    data with no sidecar — which diff_runs.py refuses without --force —
    rather than new data beside an old sidecar vouching for it.
    """
    limited = False
    if stop_reason == RANGE_DONE:
        if _range_covers_listing(len(products), pages_completed,
                                 total_results, page_count):
            stop_reason = "listing_exhausted"
        else:
            stop_reason, limited = "page_limit", True
    complete = stop_reason in COMPLETE_STOP_REASONS
    wrote_output = bool(products) or allow_empty

    if wrote_output:
        try:
            os.unlink(f"{out_prefix}.meta.json")
        except FileNotFoundError:
            pass
    rc = save(products, out_prefix, fmt, allow_empty=allow_empty)

    if wrote_output:
        if products and complete:
            status = "complete"
        elif products and limited:
            status = "limited"
        elif products:
            status = "partial"
        else:
            status = "failed"
        if not products and allow_empty and complete:
            # An explicitly empty result that the site itself reported as
            # empty is a complete answer, not a failure.
            status = "complete"
        write_run_meta(out_prefix, run_meta(
            status=status, stop_reason=stop_reason,
            pages_requested=pages_requested, pages_completed=pages_completed,
            pages_failed=pages_failed, total_results=total_results,
            addressable=addressable, page_count=page_count,
            start_url=start_url, final_url=final_url, products=len(products)))

    if not products:
        # Nothing gathered at all: a challenge outranks "empty listing",
        # because it says something stood between the run and the content.
        return EXIT_BLOCKED if blocked else rc
    if limited:
        total = f" of the {total_results} the listing holds" if total_results else ""
        print(f"[i] Limited run: read the {pages_completed} page(s) asked for, "
              f"{len(products)} product(s){total}. That is a sample, not the "
              f"whole listing — the sidecar says status=limited, and "
              f"diff_runs.py will not treat it as an assortment snapshot. "
              f"Raise --pages past the listing's end for a full one.")
        return rc
    if not complete:
        print(f"[!] Partial run: stopped after {pages_completed} of "
              f"{pages_requested} page(s) ({stop_reason}). The output holds "
              f"what was gathered, but it is NOT a complete view — see "
              f"{out_prefix}.meta.json.")
        return EXIT_PARTIAL
    return rc
