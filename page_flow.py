"""
page_flow.py
-------------
What kind of page came back, and what a run should do about it.

lg.com answers a category request in five distinguishable ways, and four of
them want a different response — which is the test for putting this in its
own module rather than writing the triage three times, once per engine, and
letting the three drift:

    content      the grid holds products                       parse
    empty        page 1 of a category with nothing in it        stop, exit 4
    exhausted    a page past the last one: the grid is empty
                 but the page is plainly served                 stop, complete
    unpainted    served, but the grid has not rendered yet      wait, then retry
    blocked      Akamai refused: 403, "Access Denied", or a
                 response not built out of LG's own assets      rotate, exit 3

The decision is DATA (`STATE_POLICY`), not three copies of an if-chain.

**The page NUMBER is part of the classification here, which is unusual.**
Past the end of a category the site answers 200 with an empty grid and its
own `totalCount: 0` — the same shape as a category that is genuinely empty.
Measured 2026-09-21: `/ru/televisions` page 1 reports `totalCount: 203`,
page 99 reports 0 products and `totalCount: 0`. Only the page number tells
"this category has nothing" from "you have run off the end of it", and the
two are different answers: one is exit 4, the other is a complete run.

**Signal order is by how much a signal proves, not by how cheap it is.** The
grid's own contents outrank every heuristic, so a real page with few assets
cannot be reported as blocked; the asset-count check only ever runs on a
response that produced no products at all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from product_parser import (MIN_ASSET_REFERENCES, asset_reference_count,
                            count_cards, detect_bot_challenge)

CONTENT = "content"
EMPTY = "empty"
EXHAUSTED = "exhausted"
UNPAINTED = "unpainted"
BLOCKED = "blocked"

# What a full page of the grid holds, measured on every capture: the API
# ignores the form's `length` field and returns 12 either way.
PAGE_SIZE = 12

# Statuses that are a refusal rather than a page. lg.com/ru answers a normal
# request with 200; lg.com/us answered 403 to the same client on 2026-09-21,
# which is what this list is for.
REFUSAL_STATUSES = (401, 403, 405, 406, 429, 503)


@dataclass(frozen=True)
class PagePolicy:
    """What to do about a page in one state."""
    retry: bool           # fetching it again could plausibly change the answer
    wait_first: bool      # give the page time to paint before retrying
    rotate_exit: bool     # a different proxy exit is what might help
    may_solve: bool       # a captcha here is worth paying to solve
    blocked: bool         # counts as exit 3 if the run ends with nothing
    complete: bool        # the category genuinely ended here


# Consulted by every engine — a constant nothing reads is the same defect as
# dead code, so the engines take their retry, rotation and solve decisions
# from HERE and nowhere else.
STATE_POLICY = {
    CONTENT:   PagePolicy(retry=False, wait_first=False, rotate_exit=False,
                          may_solve=False, blocked=False, complete=False),
    EMPTY:     PagePolicy(retry=False, wait_first=False, rotate_exit=False,
                          may_solve=False, blocked=False, complete=True),
    EXHAUSTED: PagePolicy(retry=False, wait_first=False, rotate_exit=False,
                          may_solve=False, blocked=False, complete=True),
    UNPAINTED: PagePolicy(retry=True, wait_first=True, rotate_exit=False,
                          may_solve=False, blocked=False, complete=False),
    BLOCKED:   PagePolicy(retry=True, wait_first=False, rotate_exit=True,
                          may_solve=True, blocked=True, complete=False),
}


@dataclass
class PageState:
    """The classification of one response, with the evidence behind it."""
    state: str
    reason: str
    vendor: Optional[str] = None
    total_results: Optional[int] = None
    record_count: int = 0
    card_count: int = 0
    status_code: Optional[int] = None

    @property
    def policy(self) -> PagePolicy:
        return STATE_POLICY[self.state]

    def __str__(self) -> str:
        return f"{self.state} ({self.reason})"


def expected_records(total_results, page_num: int, page_size: int = PAGE_SIZE):
    """How many products page `page_num` should hold, or None if unknowable.

    The site publishes its own match count, so "did this page bring back
    everything it should have?" is arithmetic rather than a threshold. The
    count comes from PAGE 1: past the end the API reports `totalCount: 0`,
    which describes that response rather than the category.
    """
    if not isinstance(total_results, int) or total_results <= 0:
        return None
    remaining = total_results - page_size * (page_num - 1)
    if remaining <= 0:
        return 0
    return min(page_size, remaining)


def shortfall(total_results, page_num: int, got: int, page_size: int = PAGE_SIZE):
    """A message naming a page that came back short, or None.

    A page that returns 7 of the 12 products the site's own count says are
    there is the signature of a snapshot taken mid-render — the failure that
    otherwise looks like a successful run with less data in it.
    """
    expected = expected_records(total_results, page_num, page_size)
    if expected is None or got >= expected:
        return None
    return (f"page {page_num} returned {got} product(s) where the category's "
            f"own count of {total_results} implies {expected} — the page may "
            f"have been read before the grid finished rendering")


def classify(html: Optional[str], status_code: Optional[int] = None,
             url: Optional[str] = None, page_num: int = 1,
             record_count: Optional[int] = None,
             total_results: Optional[int] = None) -> PageState:
    """Classify one response.

    `record_count`/`total_results` come from the catalogue API when the
    caller used it; a browser engine passes neither and the grid is counted
    out of `html` instead.
    """
    html = html or ""
    cards = count_cards(html) if html else 0
    found = record_count if record_count is not None else cards

    if found:
        source = "API" if record_count is not None else "rendered grid"
        return PageState(CONTENT, f"{found} product(s) in the {source}",
                         total_results=total_results, record_count=found,
                         card_count=cards, status_code=status_code)

    if status_code in REFUSAL_STATUSES:
        return PageState(BLOCKED, f"HTTP {status_code}", vendor="http",
                         status_code=status_code)

    vendor = detect_bot_challenge(html)
    if vendor:
        return PageState(BLOCKED, f"a refusal page from {vendor}", vendor=vendor,
                         status_code=status_code)

    assets = asset_reference_count(html)
    if assets < MIN_ASSET_REFERENCES:
        return PageState(
            BLOCKED,
            f"the response references LG's own asset paths {assets} time(s) "
            f"(a served page references them dozens of times), so it was not "
            f"built by the site — an interstitial or the browser's own error "
            f"page", vendor="unknown", status_code=status_code)

    # Served, by the site, with no products on it. Which of the two that is
    # depends on the page number — see the module docstring.
    if page_num > 1:
        return PageState(EXHAUSTED,
                         f"page {page_num} is past the end of this category",
                         total_results=total_results, card_count=cards,
                         status_code=status_code)
    if html:
        return PageState(EMPTY, "page 1 of this category holds no products",
                         total_results=total_results, card_count=cards,
                         status_code=status_code)
    return PageState(UNPAINTED,
                     "nothing to read yet — the response was empty",
                     status_code=status_code)
