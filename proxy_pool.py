"""
proxy_pool.py
--------------
A pool of proxy URLs and the rules for moving between them.

Family core: no site knowledge lives here. Carried over from
farfetch-scraper, with the credential-redaction helper from the newer repos
folded in — see `redact_secret_patterns`.

Why this exists as its own module: `--proxy` was a single static string
applied once at browser launch and never changed. That is the shape of a
demo, not of the thing proxies are bought for — the reason to hold a pool is
to spread a run across exits and to leave an exit that has started getting
challenged.

Kept engine-agnostic and side-effect free (no browser, no network) so the
rotation rules are covered by the offline suite rather than only by a live
run.

**Rotating the IP alone is not enough.** Carrying the same browser session
across two exits is itself a contradiction: cookies a bot manager issued
against IP A, replayed from IP B, are a stronger signal than either address
on its own. So a caller must build a FRESH browser context (new cookie jar,
new storage) for every exit this pool hands out — see playwright_scraper.py,
which tears the browser down and relaunches rather than swapping the proxy
under a live session.
"""

from __future__ import annotations

import logging
import random
import re
from typing import List, Optional
from urllib.parse import urlparse

logger = logging.getLogger("proxy_pool")

# `per-run` keeps one exit for the whole run — the safest default, since a
# single session that changes address mid-flight is more suspicious than one
# that does not. `per-page` takes a new exit for every page, which is what
# spreads volume; it costs a browser relaunch per page (see the module
# docstring for why that cost is mandatory rather than incidental).
ROTATE_MODES = ("per-run", "per-page")

# Schemes Playwright's `proxy.server` accepts. socks5 carries no credentials
# there (Chromium does not support authenticated SOCKS), so a socks5:// entry
# with a user:pass in it is rejected on load rather than silently ignored at
# request time.
_SUPPORTED_SCHEMES = ("http", "https", "socks5")

# The rule this family settled on: an EXCEPTION MESSAGE is a log. `requests` puts the full URL,
# query string included, into the text of HTTPError and of every connection
# error, and Playwright repeats a CDP endpoint five times in one error. So any
# endpoint that takes its key as a query parameter leaks it the moment
# anything goes wrong. These two patterns are what `redact_secret_patterns`
# below removes — globally, not once: a masker that handles the first
# occurrence prints the password the other four times and looks like it is
# working.
_CREDENTIAL_URL_RE = re.compile(r"([a-z][a-z0-9+.\-]*://)[^\s/@:]+:[^\s/@]+(@)",
                                re.IGNORECASE)
_KEY_PARAM_RE = re.compile(
    r"\b((?:client)?key|token|api[_-]?key|password)=[^&\s\"'<>]+", re.IGNORECASE)


def redact_secret_patterns(text: str) -> str:
    """Remove credentials from a string that is about to be logged or raised.

    Keeps the endpoint, the host and the status — the useful half — and drops
    only the secret. Applied to EVERY occurrence, because the leak this exists
    to stop repeats the same URL several times in one Playwright error.
    """
    text = _CREDENTIAL_URL_RE.sub(r"\1***:***\2", text)
    return _KEY_PARAM_RE.sub(lambda m: m.group(1) + "=***", text)


class ProxyError(ValueError):
    """A proxy list that cannot be used as given."""


def parse_proxy_line(line: str, source: str = "<arg>") -> Optional[str]:
    """Validate one proxy URL. Returns it, or None for a blank/comment line.

    Raises ProxyError with the offending line named — REDACTED, since that
    line can carry a real username:password and an exception's text is a log
    the moment anything catches and prints it.
    """
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    parsed = urlparse(line)
    safe = redact_secret_patterns(line)
    if parsed.scheme not in _SUPPORTED_SCHEMES:
        raise ProxyError(
            f"{source}: {safe!r} — scheme must be one of "
            f"{', '.join(_SUPPORTED_SCHEMES)} (got {parsed.scheme or 'none'}). "
            f"A bare host:port is not enough; write http://host:port.")
    if not parsed.hostname:
        raise ProxyError(f"{source}: {safe!r} — no host in that URL.")
    if parsed.scheme == "socks5" and (parsed.username or parsed.password):
        raise ProxyError(
            f"{source}: {safe!r} — Chromium cannot authenticate a SOCKS5 "
            f"proxy, so credentials here would be silently dropped. Use an "
            f"http:// entry for an authenticated proxy.")
    return line


def load_proxy_file(path: str) -> List[str]:
    """Read a proxy-per-line file. Blank lines and `#` comments are skipped."""
    proxies = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, raw in enumerate(f, 1):
            entry = parse_proxy_line(raw, source=f"{path}:{lineno}")
            if entry:
                proxies.append(entry)
    if not proxies:
        raise ProxyError(f"{path}: no proxy entries found (only blanks/comments?)")
    return proxies


def mask(url: Optional[str]) -> str:
    """A proxy URL safe to log: credentials replaced, host and port kept.

    Host and port stay visible on purpose — knowing WHICH exit a run used is
    the whole point of a rotation log, and it is not the secret.
    """
    if not url:
        return "(none)"
    parsed = urlparse(url)
    host = parsed.hostname or "?"
    port = f":{parsed.port}" if parsed.port else ""
    creds = "***:***@" if (parsed.username or parsed.password) else ""
    return f"{parsed.scheme}://{creds}{host}{port}"


def to_playwright(url: Optional[str]) -> Optional[dict]:
    """Playwright's `proxy=` dict for a proxy URL, or None.

    Credentials go in their own fields rather than in `server`. Playwright
    passes `server` down to Chromium as a command-line switch, so a
    user:pass left in there would land in the browser process's argv — where
    anything on the machine that can run `ps` can read it.
    """
    if not url:
        return None
    parsed = urlparse(url)
    port = f":{parsed.port}" if parsed.port else ""
    proxy = {"server": f"{parsed.scheme}://{parsed.hostname}{port}"}
    if parsed.username:
        proxy["username"] = parsed.username
    if parsed.password:
        proxy["password"] = parsed.password
    return proxy


def to_pyppeteer(url: Optional[str]):
    """(launch_arg, credentials) for pyppeteer, or (None, None).

    pyppeteer has no proxy field: the server goes in as a Chromium switch and
    the credentials go through `page.authenticate`. Splitting them here is
    what keeps a password out of the browser's argv.
    """
    if not url:
        return None, None
    parsed = urlparse(url)
    port = f":{parsed.port}" if parsed.port else ""
    arg = f"--proxy-server={parsed.scheme}://{parsed.hostname}{port}"
    creds = None
    if parsed.username:
        creds = {"username": parsed.username, "password": parsed.password or ""}
    return arg, creds


def to_selenium(url: Optional[str]):
    """(launch_arg, warning_or_None) for Selenium.

    Selenium's only proxy channel is the `--proxy-server` switch, which cannot
    carry credentials at all: chromedriver has nowhere to put a password.
    Strip them and WARN rather than letting a user believe a user:pass URL is
    doing something.
    """
    if not url:
        return None, None
    parsed = urlparse(url)
    port = f":{parsed.port}" if parsed.port else ""
    arg = f"--proxy-server={parsed.scheme}://{parsed.hostname}{port}"
    if parsed.username or parsed.password:
        return arg, (f"Selenium cannot authenticate a proxy: the credentials in "
                     f"{mask(url)} were dropped, and this exit will refuse the "
                     f"connection if it requires them. Use the Playwright or "
                     f"pyppeteer engine for an authenticated proxy.")
    return arg, None


# What a preflight learned about an exit. `fatal` means the exit cannot work
# at all and another attempt through it is wasted time; anything else is worth
# continuing on, because the target — not the exit — may be the slow part.
PREFLIGHT_OK = "ok"
PREFLIGHT_REJECTED = "rejected"      # the proxy refused the credentials
PREFLIGHT_UNUSABLE = "unusable"      # the tunnel could not be opened at all
PREFLIGHT_SLOW = "slow"              # timed out; may be the target, not the exit


def classify_preflight(error_text: str) -> str:
    """What an exception from a request through a proxy actually means.

    Separated from the request itself so the mapping is covered by the offline
    suite rather than only by a live run.

    Why this exists at all, measured on 2026-09-21: a proxy whose password had
    been rotated answered `407 Proxy Authentication Required` in 0.2s to a
    plain `requests` call — while the SAME exit under Chromium produced
    nothing but navigation timeouts, three attempts of 60s per page, with no
    mention of the proxy anywhere in the log. The family rule is that a proxy
    failure is not a timeout; this is the case where the browser reports one
    anyway, so the check happens before the browser is involved.
    """
    text = (error_text or "").lower()
    if "407" in text or "proxy authentication required" in text:
        return PREFLIGHT_REJECTED
    if "403" in text and "proxy" in text:
        return PREFLIGHT_REJECTED
    if "timed out" in text or "timeout" in text:
        return PREFLIGHT_SLOW
    if ("tunnel connection failed" in text or "unable to connect to proxy" in text
            or "connection refused" in text or "proxyerror" in text):
        return PREFLIGHT_UNUSABLE
    return PREFLIGHT_SLOW


def preflight(url: Optional[str], target: str, timeout: float = 15.0):
    """Check one exit before a browser is launched. Returns (verdict, detail).

    One small GET to the site the run is about to scrape: it proves the
    credentials authenticate AND that the exit can reach the target, in under
    a second, instead of discovering it as three minutes of browser timeouts
    with nothing naming the cause.

    `requests` is already a core dependency, and every message is redacted
    before it is handed back — the proxy URL travels inside these exceptions.
    """
    if not url:
        return PREFLIGHT_OK, "no proxy configured"
    try:
        import requests
    except ImportError:      # pragma: no cover — requests is a core dependency
        return PREFLIGHT_OK, "requests not installed; skipping the preflight"
    try:
        response = requests.get(target, proxies={"http": url, "https": url},
                                timeout=timeout,
                                headers={"User-Agent": "Mozilla/5.0"})
    except Exception as e:   # noqa: BLE001 — every failure shape is classified
        detail = redact_secret_patterns(str(e))
        return classify_preflight(detail), detail
    if response.status_code == 407:
        return PREFLIGHT_REJECTED, "the proxy answered 407 to the request itself"
    return PREFLIGHT_OK, f"HTTP {response.status_code} through {mask(url)}"


def check_exit_or_raise(pool, target: str, timeout: float = 15.0) -> None:
    """Preflight `pool`'s current exit; raise ProxyError if it cannot work.

    A rejected or unusable exit is a configuration problem, not a transient
    one, so it ends the run with exit 2 rather than being retried — while a
    slow one only warns, because the target may be what is slow.
    """
    if not pool:
        return
    verdict, detail = preflight(pool.current, target, timeout=timeout)
    if verdict == PREFLIGHT_OK:
        logger.info("Proxy preflight: %s", detail)
        return
    if verdict == PREFLIGHT_SLOW:
        logger.warning("Proxy preflight through %s did not answer in %.0fs (%s). "
                       "Continuing — this may be the target rather than the "
                       "exit.", mask(pool.current), timeout, detail)
        return
    raise ProxyError(
        f"The proxy exit {mask(pool.current)} cannot be used: {detail}. "
        f"This is the exit, not the site — check the credentials in .env "
        f"(a rotated password is the usual cause), or pass --proxy-file with "
        f"another entry.")


class ProxyPool:
    """An ordered pool of exits, plus a cursor and a rotation policy."""

    def __init__(self, proxies: List[str], rotate: str = "per-run",
                 shuffle: bool = False, rng: Optional[random.Random] = None):
        if not proxies:
            raise ProxyError("a proxy pool needs at least one entry")
        if rotate not in ROTATE_MODES:
            raise ProxyError(f"rotate must be one of {ROTATE_MODES}, got {rotate!r}")
        self._proxies = list(proxies)
        if shuffle:
            # Two runs started at the same minute otherwise hammer the same
            # first exit in the list.
            (rng or random).shuffle(self._proxies)
        self.rotate = rotate
        self._index = 0
        # Counted so a run can report how many exits it actually burned.
        self.rotations = 0

    def __len__(self) -> int:
        return len(self._proxies)

    @property
    def proxies(self) -> List[str]:
        """A copy of the exits, for handing a rotated view to each worker.

        A copy rather than the list itself: a worker builds its own pool from
        this, and two threads sharing one mutable list is the bug that makes
        concurrency stop being worth it.
        """
        return list(self._proxies)

    @property
    def current(self) -> str:
        return self._proxies[self._index % len(self._proxies)]

    def advance(self, reason: str) -> str:
        """Move to the next exit and return it. Wraps around the list.

        Wrapping rather than exhausting: a pool of 3 used across 50 pages is
        a legitimate configuration, and refusing to continue would be worse
        than reusing an exit. The log line says which exit and why, so a run
        that is cycling a too-small pool is visible rather than silent.
        """
        if len(self._proxies) == 1:
            logger.warning("Asked to rotate (%s) but the pool holds one exit "
                           "(%s) — staying on it. Add more with --proxy-file.",
                           reason, mask(self.current))
            return self.current
        self._index = (self._index + 1) % len(self._proxies)
        self.rotations += 1
        logger.info("Rotated proxy (%s) -> %s [exit %d/%d, rotation #%d]",
                    reason, mask(self.current), (self._index % len(self._proxies)) + 1,
                    len(self._proxies), self.rotations)
        return self.current

    def rotates_per_page(self) -> bool:
        return self.rotate == "per-page"


def from_args(args) -> Optional[ProxyPool]:
    """Build a pool from --proxy-file / --proxy, or None if neither is set.

    `--proxy-file` wins when both are given, and says so: silently ignoring
    one of two conflicting options is how a run ends up on an exit the
    operator did not choose.
    """
    proxy_file = getattr(args, "proxy_file", None)
    single = getattr(args, "proxy", None)
    rotate = getattr(args, "proxy_rotate", "per-run")

    if proxy_file:
        if single:
            logger.warning("--proxy-file and --proxy both given; using the file "
                           "and ignoring the single --proxy.")
        proxies = load_proxy_file(proxy_file)
        logger.info("Loaded %d proxy exit(s) from %s, rotation: %s",
                    len(proxies), proxy_file, rotate)
        return ProxyPool(proxies, rotate=rotate,
                         shuffle=getattr(args, "proxy_shuffle", False))

    if single:
        entry = parse_proxy_line(single, source="--proxy")
        if entry is None:
            raise ProxyError("--proxy was given but is empty")
        return ProxyPool([entry], rotate="per-run")

    return None
