#!/usr/bin/env python3
"""
fingerprint_client.py
----------------------
Client for 2Captcha's Fingerprint API (https://2captcha.com/fingerprints/api),
plus the glue that applies a fingerprint to a browser this project launched
itself.

Why this is separate from the Scraping Browser API: that product already
brings its own fingerprint. This is for the other case — your own Chromium,
no `--cdp-endpoint` — and the two are alternatives, not layers. Stacking a
second fingerprint on a remote browser creates a contradiction rather than
better cover, which is why every engine here ignores `--fingerprint` when
`--cdp-endpoint` is set.

**Read this before trusting the mapping below.** this family's template lists five
defects that lived in this file across four repos for months, every one of
them a wrong key rather than a crash: a user agent never applied because the
code read a key the API does not return, a locale invented as `en-{country}`,
a timezone never applied at all, a documented `--tags` example that returns
HTTP 400 every time, and the API key printed to the terminal on any error.
None of them raises; each just makes the tool do less than it says.

So this version:

  * **Tries several spellings for every field and says which one it found.**
    `--explain` prints the mapping for a real response. A field this module
    cannot find is a WARNING naming the field and the keys it looked for —
    never a silent no-op, which is what hid four of the five defects.
  * **Never builds a locale out of a country.** A German fingerprint is not
    `en-DE`. The locale comes from the response's own locale/language field,
    or it is not set at all.
  * **Applies the timezone**, which the API states and the old code dropped.
  * **Sends ONE OS-family tag by default.** Measured against the live API on
    2026-09-10: `Windows` succeeds, while `Windows,Chrome,Desktop`, `Chrome`
    and `Desktop` each return 400. The old default was the three-tag string,
    in every engine, so `--fingerprint` could not work for anyone.
  * **Redacts before it raises.** The key rides in the query string here, and
    `requests` copies the full URL into the text of every error it raises.

The field mapping in this repo has NOT been verified against a live response.
It was tried on 2026-09-19 with a key that has a working captcha-solving
balance, and `/fingerprint/random` answered **HTTP 403**: fingerprints are a
separate subscription, and this key does not carry it. What that run DID
verify is the redaction — the failure printed
`…/fingerprint/random?format=chromium&tags=Windows&key=***` rather than the
key itself.

So run `python3 fingerprint_client.py --explain` with a subscribed key before
relying on any of it. The output names, field by field, which response key
each setting came from and which settings could not be applied at all — which
is the whole reason this module reports instead of guessing.
"""

import argparse
import hashlib
import json
import logging
import os
import sys
from typing import Optional

import requests

from proxy_pool import redact_secret_patterns

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("fingerprint_client")

API_BASE = "https://api.2captcha.com"
RANDOM_URL = f"{API_BASE}/fingerprint/random"
GENERATE_URL = f"{API_BASE}/fingerprint/generate"

DEFAULT_CACHE_DIR = os.path.join(os.path.expanduser("~"), ".cache", "2captcha-fingerprints")

# ONE OS-family tag. See the module docstring: a list is rejected with 400.
DEFAULT_TAGS = "Windows"


def _cache_path(cache_dir: str, params: dict, generate: bool) -> str:
    key = json.dumps({"generate": generate, **params}, sort_keys=True)
    digest = hashlib.sha256(key.encode()).hexdigest()[:16]
    return os.path.join(cache_dir, f"{digest}.json")


def get_fingerprint(api_key: str, *, tags: Optional[str] = DEFAULT_TAGS,
                    country: Optional[str] = None,
                    min_browser_version: Optional[int] = None,
                    browser_version: Optional[int] = None,
                    build_version: Optional[str] = None,
                    fmt: str = "chromium", generate: bool = False,
                    cache_dir: Optional[str] = DEFAULT_CACHE_DIR,
                    refresh: bool = False, timeout: int = 30) -> dict:
    """Fetch one fingerprint. Cached on disk unless cache_dir is None.

    Caching matters for a boring reason: both endpoints are billed per
    successful response and capped per minute, so an uncached call inside a
    scrape loop turns into a bill and then into rate-limit errors.
    """
    if tags and "," in tags:
        logger.warning("--tags %r is a list. The API accepts ONE OS-family tag "
                       "(Windows / macOS / Linux / Android / iOS) and answers "
                       "400 to a list — sending only %r.",
                       tags, tags.split(",")[0].strip())
        tags = tags.split(",")[0].strip()

    params = {"format": fmt}
    if tags:
        params["tags"] = tags
    if country:
        params["country"] = country
    if min_browser_version:
        params["min_browser_version"] = min_browser_version
    if browser_version:
        params["browser_version"] = browser_version
    if build_version:
        if not generate:
            raise ValueError("build_version is only accepted by /fingerprint/generate")
        params["build_version"] = build_version

    if cache_dir:
        path = _cache_path(cache_dir, params, generate)
        if os.path.exists(path) and not refresh:
            with open(path, encoding="utf-8") as f:
                fp = json.load(f)
            logger.info("Using cached fingerprint %s (%s)", fp.get("id"), path)
            return fp

    url = GENERATE_URL if generate else RANDOM_URL
    logger.info("GET %s %s", url, params)
    try:
        resp = requests.get(url, params={**params, "key": api_key}, timeout=timeout)
        if resp.status_code in (401, 403):
            # Measured against the live API on 2026-09-19 with a key that has a
            # working captcha-solving balance: /fingerprint/random answers 403,
            # not 401. Both mean the same thing here and both used to surface
            # as a bare stack trace with the key in the URL.
            raise RuntimeError(
                f"Fingerprint API rejected the key ({resp.status_code}). This is "
                f"a SEPARATE subscription from captcha solving — a key with a "
                f"working solver balance is not automatically enabled for "
                f"fingerprints, and answers 403 when it is not.")
        if resp.status_code == 400:
            raise RuntimeError(
                f"Fingerprint API rejected the request (400). The usual cause "
                f"is --tags: send ONE OS-family tag (got {tags!r}). Body: "
                f"{redact_secret_patterns(resp.text[:300])}")
        if resp.status_code == 429:
            raise RuntimeError(
                "Fingerprint API rate limit hit (429). The per-minute cap "
                "depends on your plan — cache the result instead of fetching "
                "per request.")
        resp.raise_for_status()
        fp = resp.json()
    except requests.RequestException as e:
        # The key is in the query string on this endpoint, and `requests` puts
        # the whole URL into the message. Redact before it reaches a log.
        raise RuntimeError(f"Fingerprint API request failed: "
                           f"{redact_secret_patterns(str(e))}") from None

    if cache_dir:
        os.makedirs(cache_dir, exist_ok=True)
        with open(_cache_path(cache_dir, params, generate), "w", encoding="utf-8") as f:
            json.dump(fp, f, indent=2)
    logger.info("Fingerprint %s (%s)", fp.get("id"), fp.get("country"))
    return fp


def _pick(fp: dict, *paths):
    """First value found at any of `paths`, plus the path it came from.

    Each path is a tuple of keys, walked leniently. Returning the PATH as well
    is what makes `--explain` able to say where each applied value came from —
    and what turns "this field was never applied" from a silent no-op into a
    reported one.
    """
    for path in paths:
        node = fp
        for key in path:
            if not isinstance(node, dict) or key not in node:
                node = None
                break
            node = node[key]
        if isinstance(node, (str, int, float)) and str(node).strip():
            return node, ".".join(path)
    return None, None


def describe(fp: dict) -> dict:
    """What this module can and cannot apply from `fp`, field by field.

    Used by `playwright_context_kwargs` for its warnings and by `--explain`.
    """
    fields = {
        "user_agent": _pick(fp, ("userAgent",), ("userAgent", "value"),
                            ("user_agent",), ("navigator", "userAgent")),
        "locale": _pick(fp, ("locale",), ("language",), ("navigator", "language"),
                        ("navigator", "languages", 0)),
        "timezone": _pick(fp, ("timezone",), ("timezone", "id"), ("timezoneId",),
                          ("timeZone",)),
        "screen_width": _pick(fp, ("screen", "width"), ("screenWidth",)),
        "screen_height": _pick(fp, ("screen", "height"), ("screenHeight",)),
        "platform": _pick(fp, ("navigator", "platform"), ("platform",)),
        "hardware_concurrency": _pick(fp, ("navigator", "hardwareConcurrency"),
                                      ("hardwareConcurrency",)),
        "device_memory": _pick(fp, ("navigator", "deviceMemory"), ("deviceMemory",)),
        "webgl_vendor": _pick(fp, ("webgl", "vendor"), ("webglVendor",)),
        "webgl_renderer": _pick(fp, ("webgl", "renderer"), ("webglRenderer",)),
    }
    return {name: {"value": value, "from": source}
            for name, (value, source) in fields.items()}


def playwright_context_kwargs(fp: dict) -> dict:
    """The parts of a fingerprint Playwright can set natively on a context.

    Every field this cannot find is warned about by name. A fingerprint that
    was fetched, billed and then applied to nothing is the failure mode this
    module exists to make impossible to miss.
    """
    found = describe(fp)
    kwargs = {}

    if found["user_agent"]["value"]:
        kwargs["user_agent"] = str(found["user_agent"]["value"])
    else:
        logger.warning("This fingerprint carries no user agent under any known "
                       "key (tried userAgent, userAgent.value, user_agent, "
                       "navigator.userAgent) — the browser will keep its own. "
                       "Run --explain to see what the response does contain.")

    if found["locale"]["value"]:
        kwargs["locale"] = str(found["locale"]["value"])
    else:
        # Deliberately NOT en-{country}: that is the exact bug §16 names, and
        # it produced "en-DE" for a German fingerprint.
        logger.warning("This fingerprint carries no locale — leaving the "
                       "browser's own rather than inventing one from the "
                       "country.")

    if found["timezone"]["value"]:
        kwargs["timezone_id"] = str(found["timezone"]["value"])
    else:
        logger.warning("This fingerprint carries no timezone — leaving the "
                       "machine's own, which may contradict the exit IP.")

    width = found["screen_width"]["value"]
    height = found["screen_height"]["value"]
    if width and height:
        width, height = int(width), int(height)
        # A real window is smaller than the screen; a viewport exactly equal
        # to screen size is itself a signal.
        kwargs["screen"] = {"width": width, "height": height}
        kwargs["viewport"] = {"width": width, "height": max(400, height - 120)}
    return kwargs


def playwright_init_script(fp: dict) -> str:
    """JS to run before page scripts, patching what Playwright cannot set.

    Shallow by construction: it changes what `navigator.*` and
    WEBGL_debug_renderer_info REPORT, not what the GPU is, so a fingerprinter
    that cross-checks reported WebGL strings against real rendering output can
    still tell. Treat it as raising the floor, not as a disguise.

    Values are baked in as JSON rather than interpolated as bare text, so a
    string from the API cannot terminate the script it is embedded in.
    """
    found = describe(fp)
    payload = json.dumps({
        "platform": found["platform"]["value"],
        "hardwareConcurrency": found["hardware_concurrency"]["value"],
        "deviceMemory": found["device_memory"]["value"],
        "webglVendor": found["webgl_vendor"]["value"],
        "webglRenderer": found["webgl_renderer"]["value"],
    })
    return """
( => {
  const fp = %s;
  const def = (obj, prop, value) => {
    if (value === null || value === undefined) return;
    try { Object.defineProperty(obj, prop, {get:  => value, configurable: true}); }
    catch (e) { /* already non-configurable: leave it rather than throw */ }
  };
  def(Navigator.prototype, 'platform', fp.platform);
  def(Navigator.prototype, 'hardwareConcurrency', fp.hardwareConcurrency);
  def(Navigator.prototype, 'deviceMemory', fp.deviceMemory);

  // WEBGL_debug_renderer_info: 37445 = UNMASKED_VENDOR, 37446 = UNMASKED_RENDERER.
  // Patch WebGL1 and WebGL2 alike — a fingerprinter reading only WebGL2 would
  // otherwise see the real values, and the mismatch is itself a signal.
  for (const proto of [window.WebGLRenderingContext, window.WebGL2RenderingContext]) {
    if (!proto) continue;
    const original = proto.prototype.getParameter;
    proto.prototype.getParameter = function (p) {
      if (p === 37445 && fp.webglVendor) return fp.webglVendor;
      if (p === 37446 && fp.webglRenderer) return fp.webglRenderer;
      return original.apply(this, arguments);
    };
  }
});
""" % payload


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch a browser fingerprint from 2Captcha")
    p.add_argument("--key", default=os.environ.get("TWOCAPTCHA_KEY"),
                   help="API key. Defaults to $TWOCAPTCHA_KEY (safer than argv).")
    p.add_argument("--tags", default=DEFAULT_TAGS,
                   help="ONE OS-family tag, not a list: Windows / macOS / Linux / "
                        "Android / iOS. Chrome and Desktop are each rejected with "
                        "400, and so is any comma-separated combination.")
    # --country is legitimate HERE (it picks a fingerprint's locale) and is
    # banned on the scrapers, where it could disagree with the URL. The
    # offline suite asserts exactly that split.
    p.add_argument("--country", default=None, help="ISO 3166-1 alpha-2, e.g. us")
    p.add_argument("--min-browser-version", type=int, default=None)
    p.add_argument("--browser-version", type=int, default=None)
    p.add_argument("--build-version", default=None, help="/generate only")
    p.add_argument("--format", dest="fmt", choices=["chromium", "raw"], default="chromium")
    p.add_argument("--generate", action="store_true",
                   help="Use /fingerprint/generate instead of /fingerprint/random")
    p.add_argument("--no-cache", action="store_true")
    p.add_argument("--refresh", action="store_true", help="Bypass a cached copy")
    p.add_argument("--explain", action="store_true",
                   help="Print which response field each applied setting came "
                        "from, and which settings could not be applied at all.")
    p.add_argument("--show-init-script", action="store_true",
                   help="Print the Playwright init script for this fingerprint")
    args = p.parse_args()

    if not args.key:
        logger.error("No API key. Pass --key, or better, export TWOCAPTCHA_KEY.")
        return 2

    try:
        fp = get_fingerprint(
            args.key, tags=args.tags, country=args.country,
            min_browser_version=args.min_browser_version,
            browser_version=args.browser_version, build_version=args.build_version,
            fmt=args.fmt, generate=args.generate,
            cache_dir=None if args.no_cache else DEFAULT_CACHE_DIR,
            refresh=args.refresh)
    except (requests.RequestException, RuntimeError, ValueError) as e:
        logger.error("%s", redact_secret_patterns(str(e)))
        return 2

    if args.explain:
        print("--- what this module can apply from that response ---")
        for name, info in describe(fp).items():
            if info["value"] is None:
                print(f"  {name:22s} NOT FOUND — nothing will be applied")
            else:
                print(f"  {name:22s} {info['from']:28s} {str(info['value'])[:60]}")
        print("\n--- Playwright context kwargs ---")
        print(json.dumps(playwright_context_kwargs(fp), indent=2))
        return 0

    print(json.dumps(fp, indent=2, ensure_ascii=False))
    if args.show_init_script:
        print("\n// --- Playwright context kwargs ---")
        print("// " + json.dumps(playwright_context_kwargs(fp)))
        print("\n// --- add_init_script ---")
        print(playwright_init_script(fp))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(1)
