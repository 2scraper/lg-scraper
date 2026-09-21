"""
captcha_solver.py
------------------
Detection and solving, shared by every engine here. Family core with one
site-shaped part: what a challenge looks like ON THIS SITE.

What lg.com actually does (the question worth asking is "is one CONFIGURED,
and would we recognise it if it appeared?", not "did we meet one"):

  * **No captcha of any kind on a category page.** Nine captures across two
    locales and three categories, plus every API response, carry no
    reCAPTCHA, hCaptcha, Turnstile, DataDome or PerimeterX markup, no
    challenge iframe and no `data-sitekey`.
  * **reCAPTCHA v3 on the support forms.** `/ru/support/email-to-ceo`
    renders `<captcha-widget data-captcha-type="recaptcha" data-version="v3"
    data-sitekey="6LeE…">` once its JavaScript has run — it is absent from
    the served HTML. So the site is wired for reCAPTCHA v3, on
    account-facing forms rather than for a visitor reading a catalogue, and
    this scraper never touches those forms.
  * **What refuses this scraper is not a captcha at all**, it is Akamai
    answering 403 with an "Access Denied" page — see
    product_parser.BOT_CHALLENGE_MARKERS, where the measurement lives.
    Nothing here can solve that; a different exit can.

Turnstile support is kept even though this site shows none: detection stays
broad by decision, different geos surface different challenges, and the same
module is what the rest of the family runs.

Solving goes through 2Captcha and nothing else. Over the Scraping Browser API
you often need none of this: `Captcha.setAutoSolve` can clear a challenge
inside the browser before this module gets a turn — treat
`Captcha.solveFinished` as the success signal and keep this as the fallback.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests

from product_parser import strip_extension_scripts
from proxy_pool import redact_secret_patterns

logger = logging.getLogger("captcha_solver")

# 2Captcha has two generations of solver API and both are live.
#
#   v2 (current, https://2captcha.com/api-docs):
#       POST https://api.2captcha.com/createTask     {clientKey, task:{...}}
#       POST https://api.2captcha.com/getTaskResult  {clientKey, taskId}
#     The key rides in a JSON body, so it cannot leak through a URL in an
#     exception message.
#
#   v1 (legacy, still accepted):
#       POST https://2captcha.com/in.php   form-encoded
#       GET  https://2captcha.com/res.php  polling — THE KEY IS IN THE QUERY
#     STRING, which `requests` copies verbatim into the text of every
#     HTTPError and connection error it raises. Every call on that path is
#     therefore wrapped and re-raised redacted.
TWOCAPTCHA_API_V2 = "https://api.2captcha.com"
TWOCAPTCHA_CREATE_TASK_URL = f"{TWOCAPTCHA_API_V2}/createTask"
TWOCAPTCHA_GET_RESULT_URL = f"{TWOCAPTCHA_API_V2}/getTaskResult"
TWOCAPTCHA_BALANCE_URL = f"{TWOCAPTCHA_API_V2}/getBalance"
TWOCAPTCHA_IN_URL = "https://2captcha.com/in.php"
TWOCAPTCHA_RES_URL = "https://2captcha.com/res.php"

# v2 rejects an arbitrary minScore: these three are the documented values.
V3_ALLOWED_MIN_SCORES = (0.3, 0.7, 0.9)

# Cloudflare Turnstile sitekeys start 0x and are ~24 chars; reCAPTCHA keys
# start 6L. Both are public values that appear in page markup — neither is a
# credential — but they are matched by SHAPE so a random attribute cannot be
# mistaken for one.
_TURNSTILE_KEY_RE = r"0x[A-Za-z0-9_-]{10,}"
_RECAPTCHA_KEY_RE = r"6L[\w-]{20,}"


@dataclass
class CaptchaChallenge:
    kind: str            # "turnstile" | "recaptcha_v3" | "recaptcha_v2_invisible" | "recaptcha_v2"
    sitekey: Optional[str]
    action: str = "verify"
    page_url: str = ""
    # How it was found: "html" (static markup) or "runtime" (the live page's
    # own captcha client config). Recorded because the two can disagree about
    # the version, and the runtime one is authoritative when they do.
    source: str = "html"
    # Raw `size` from a reCAPTCHA client config: "invisible" for v2-invisible
    # and for v3, absent for a v2 checkbox.
    size: Optional[str] = None

    @property
    def is_turnstile(self) -> bool:
        return self.kind == "turnstile"

    @property
    def is_v3(self) -> bool:
        return self.kind == "recaptcha_v3"

    @property
    def is_invisible_v2(self) -> bool:
        return self.kind == "recaptcha_v2_invisible"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------
_MOUNT_RE = re.compile(r"<captcha-widgets\b[^>]*>(.*?)</captcha-widgets>",
                       re.IGNORECASE | re.DOTALL)


def detect_autosolver_mount(html: str) -> Optional[str]:
    """The contents of the autosolver extension's `<captcha-widgets>`, if any.

    A FUNCTION rather than a substring, because the EMPTY element appears on
    every page loaded through the Scraping Browser, and matching the bare tag
    would report every run as challenged. Returns None both when the element
    is absent and when it is empty; returns the inner markup when the
    extension has actually rendered a widget into it, which means IT found a
    challenge — a useful second opinion, and not evidence about the site's
    own markup. See the module docstring for how that was established.
    """
    match = _MOUNT_RE.search(strip_extension_scripts(html))
    if not match:
        return None
    inner = match.group(1).strip()
    return inner or None


def detect_turnstile(html: str, page_url: str = "") -> Optional[CaptchaChallenge]:
    """A Cloudflare Turnstile widget in `html`, or None.

    Three shapes, all seen across this family:
        <div class="cf-turnstile" data-sitekey="0x…">   Cloudflare's own
        <captcha-widget data-captcha-type="turnstile" data-sitekey="0x…">
        <script src="…challenges.cloudflare.com/turnstile/v0/api.js">

    A widget whose sitekey cannot be read returns a challenge with
    `sitekey=None` rather than None outright, so the caller can report "a
    challenge is here and I cannot solve it" instead of "no challenge" —
    `solve` refuses it with that reason rather than sending a nonsensical
    null sitekey to the API.
    """
    cleaned = strip_extension_scripts(html)
    if "turnstile" not in cleaned.lower():
        return None

    mount = detect_autosolver_mount(cleaned) or ""
    for pattern in (
            r'class=["\'][^"\']*cf-turnstile[^"\']*["\'][^>]*data-sitekey=["\'](' + _TURNSTILE_KEY_RE + r')["\']',
            r'data-sitekey=["\'](' + _TURNSTILE_KEY_RE + r')["\'][^>]*class=["\'][^"\']*cf-turnstile',
            r'data-captcha-type=["\']turnstile["\'][^>]*data-sitekey=["\'](' + _TURNSTILE_KEY_RE + r')["\']',
            r'data-sitekey=["\'](' + _TURNSTILE_KEY_RE + r')["\']'):
        m = re.search(pattern, cleaned)
        if m:
            return CaptchaChallenge(kind="turnstile", sitekey=m.group(1),
                                    page_url=page_url)

    if mount or "challenges.cloudflare.com/turnstile" in cleaned:
        logger.warning("A Turnstile widget is on this page but its sitekey could "
                       "not be read — reporting it unsolvable rather than "
                       "sending a null sitekey to the API.")
        return CaptchaChallenge(kind="turnstile", sitekey=None, page_url=page_url)
    return None


def detect_recaptcha_v3(html: str, page_url: str = "") -> Optional[CaptchaChallenge]:
    """A reCAPTCHA in the static markup, in either real-world shape.

    1. `<captcha-widget data-version="v3" data-sitekey="…">` — a wrapper
       element carrying the config as attributes.
    2. an inline `grecaptcha.execute('SITEKEY', {action: '…'})` call.

    The second shape is exactly what lg.com/ru/support renders once its JS
    has run: `data-captcha-type="recaptcha" data-version="v3"`.
    """
    cleaned = strip_extension_scripts(html)
    if "recaptcha" not in cleaned.lower():
        return None

    for widget in re.finditer(r"<captcha-widget\b([^>]*)>", cleaned, re.IGNORECASE):
        attrs = widget.group(1)
        version = re.search(r'data-version=["\']v(\d)["\']', attrs)
        sitekey = re.search(r'data-sitekey=["\'](' + _RECAPTCHA_KEY_RE + r')["\']', attrs)
        if version and version.group(1) == "3" and sitekey:
            action = re.search(r'data-action=["\']([\w_]+)["\']', attrs)
            return CaptchaChallenge(
                kind="recaptcha_v3", sitekey=sitekey.group(1),
                action=(action.group(1) if action and action.group(1) != "null"
                        else "verify"),
                page_url=page_url)

    if "grecaptcha" not in cleaned:
        return None
    execute = re.search(
        r"grecaptcha\.execute\(\s*['\"](" + _RECAPTCHA_KEY_RE + r")['\"]\s*,"
        r"\s*\{\s*action:\s*['\"]([\w_]+)['\"]", cleaned)
    if execute:
        return CaptchaChallenge(kind="recaptcha_v3", sitekey=execute.group(1),
                                action=execute.group(2), page_url=page_url)
    key = re.search(r'data-sitekey=["\'](' + _RECAPTCHA_KEY_RE + r')["\']', cleaned)
    if key and "grecaptcha.render" in cleaned:
        return CaptchaChallenge(kind="recaptcha_v3", sitekey=key.group(1),
                                page_url=page_url)
    return None


def detect_in_html(html: str, page_url: str = "") -> Optional[CaptchaChallenge]:
    """Whatever challenge the static markup shows, Turnstile first.

    Turnstile leads because it is the one this site actually configures.
    """
    return (detect_turnstile(html, page_url)
            or detect_recaptcha_v3(html, page_url))


# The live-page detector. Handed to `detect_in_page` as a callable that
# evaluates it — `page.evaluate` (Playwright), an awaited `page.evaluate`
# (pyppeteer), or `driver.execute_script` wrapped so the arrow function is
# invoked (Selenium). It reads what the HTML cannot show: a widget configured
# entirely in JavaScript.
CAPTCHA_DISCOVERY_JS = r"""
 => {
  const out = {found: false, kind: null, sitekey: null, size: null,
               action: null, renderParam: null, challengeFrame: false,
               mountFilled: false, hints: []};
  const TURNSTILE_RE = /^0x[A-Za-z0-9_-]{10,}$/;
  const RECAPTCHA_RE = /^6L[\w-]{20,}$/;

  const fromExtension = (el) => {
    const src = (el.getAttribute && el.getAttribute('src')) || '';
    return src.indexOf('chrome-extension://') === 0 ||
           src.indexOf('moz-extension://') === 0;
  };

  try {
    const mount = document.querySelector('captcha-widgets');
    out.mountFilled = !!(mount && mount.children.length > 0);
    if (out.mountFilled) out.hints.push('the site rendered into its captcha mount');
  } catch (e) { out.hints.push('mount check failed: ' + e.message); }

  try {
    for (const el of document.querySelectorAll('[data-sitekey]')) {
      if (fromExtension(el)) continue;
      const key = el.getAttribute('data-sitekey') || '';
      if (TURNSTILE_RE.test(key)) {
        out.found = true; out.kind = 'turnstile'; out.sitekey = key;
        out.hints.push('turnstile sitekey from a data-sitekey attribute');
        break;
      }
      if (RECAPTCHA_RE.test(key) && !out.sitekey) {
        out.found = true; out.kind = 'recaptcha'; out.sitekey = key;
        out.hints.push('recaptcha sitekey from a data-sitekey attribute');
      }
    }
  } catch (e) { out.hints.push('attribute scan failed: ' + e.message); }

  try {
    for (const f of document.querySelectorAll('iframe[src*="challenges.cloudflare.com"]')) {
      out.found = true; out.kind = out.kind || 'turnstile';
      const m = /[?&]k=([\w-]+)/.exec(f.getAttribute('src') || '');
      if (m && !out.sitekey) { out.sitekey = m[1]; out.hints.push('sitekey from the turnstile iframe'); }
    }
  } catch (e) { out.hints.push('turnstile iframe scan failed: ' + e.message); }

  try {
    const clients = (window.___grecaptcha_cfg || {}).clients || null;
    if (clients) {
      for (const id of Object.keys(clients)) {
        const seen = new Set;
        const walk = (o, depth) => {
          if (!o || depth > 4 || seen.has(o)) return;
          if (typeof o === 'object') seen.add(o);
          for (const k of Object.keys(o)) {
            let v; try { v = o[k]; } catch (e) { continue; }
            if (typeof v === 'string') {
              if (!out.sitekey && RECAPTCHA_RE.test(v)) {
                out.sitekey = v; out.found = true; out.kind = 'recaptcha';
                out.hints.push('sitekey from ___grecaptcha_cfg');
              } else if (v === 'invisible' || v === 'normal' || v === 'compact') {
                out.size = out.size || v;
              }
            } else if (v && typeof v === 'object') { walk(v, depth + 1); }
          }
        };
        walk(clients[id], 0);
      }
    }
    for (const sc of document.querySelectorAll('script[src*="recaptcha"]')) {
      const m = /[?&]render=([^&]+)/.exec(sc.getAttribute('src') || '');
      if (m) { out.renderParam = decodeURIComponent(m[1]); break; }
    }
    out.challengeFrame = !!document.querySelector('iframe[src*="bframe"]');
  } catch (e) { out.hints.push('recaptcha scan failed: ' + e.message); }

  return out;
}
"""


def challenge_from_discovery(info, page_url: str = "") -> Optional[CaptchaChallenge]:
    """Turn CAPTCHA_DISCOVERY_JS's result into a challenge, or None.

    Split out from `detect_in_page` so an ASYNC engine can await its own
    `page.evaluate` and hand the dict straight here. The alternative — making
    the async engine fake a synchronous callable — means calling
    `run_until_complete` from inside a running loop, which raises.

    reCAPTCHA version inference, strongest signal first, and it matters:
    v3 parameters sent for a v2-invisible widget buy a token the site rejects.
      * `api.js?render=<sitekey>` means v3; `render=explicit` means v2.
      * `size` exists only for v2: invisible / normal / compact.
      * a bframe (interactive challenge) iframe is v2 only.
    """
    if not info or not info.get("found"):
        return None

    hints = "; ".join(info.get("hints") or [])
    if info.get("kind") == "turnstile":
        logger.info("Turnstile found at runtime: sitekey=%s (%s)",
                    info.get("sitekey"), hints)
        return CaptchaChallenge(kind="turnstile", sitekey=info.get("sitekey"),
                                page_url=page_url, source="runtime")

    size, render = info.get("size"), info.get("renderParam")
    if render and render != "explicit" and render == info.get("sitekey"):
        kind = "recaptcha_v3"
    elif render == "explicit":
        kind = "recaptcha_v2_invisible" if size == "invisible" else "recaptcha_v2"
    elif size in ("normal", "compact"):
        kind = "recaptcha_v2"
    elif info.get("challengeFrame") and size == "invisible":
        kind = "recaptcha_v2_invisible"
    else:
        kind = "recaptcha_v3"
    logger.info("reCAPTCHA found at runtime: kind=%s sitekey=%s size=%s render=%s (%s)",
                kind, info.get("sitekey"), size, render, hints)
    return CaptchaChallenge(kind=kind, sitekey=info.get("sitekey"),
                            page_url=page_url, source="runtime", size=size)


def detect_in_page(evaluate, page_url: str = "") -> Optional[CaptchaChallenge]:
    """Detect a challenge by inspecting the LIVE page rather than its HTML.

    `evaluate` runs CAPTCHA_DISCOVERY_JS in the page and returns the dict —
    `page.evaluate` under Playwright, or `driver.execute_script` under
    Selenium wrapped so the arrow function is invoked. An async engine calls
    `challenge_from_discovery` directly instead.
    """
    try:
        info = evaluate(CAPTCHA_DISCOVERY_JS)
    except Exception as e:  # noqa: BLE001 — any engine's evaluate can raise
        logger.debug("In-page captcha discovery failed: %s", e)
        return None
    return challenge_from_discovery(info, page_url)


def reconcile_detections(html_challenge: Optional[CaptchaChallenge],
                         runtime_challenge: Optional[CaptchaChallenge]
                         ) -> Optional[CaptchaChallenge]:
    """Pick between the two detectors when both found something.

    Both always run; neither short-circuits the other. They can disagree on
    the same page — a site's own wrapper element can assert one version while
    the Google/Cloudflare loader it ships implements another — and the RUNTIME
    reading wins, because that describes the widget the vendor will actually
    validate against. The disagreement is logged rather than quietly resolved.
    """
    if runtime_challenge and not html_challenge:
        return runtime_challenge
    if html_challenge and not runtime_challenge:
        return html_challenge
    if not html_challenge and not runtime_challenge:
        return None
    if html_challenge.kind != runtime_challenge.kind:
        logger.warning("Detectors disagree: static markup says %s, the live page "
                       "says %s. Trusting the live page — that is the widget the "
                       "vendor validates.", html_challenge.kind, runtime_challenge.kind)
    if not runtime_challenge.sitekey and html_challenge.sitekey:
        runtime_challenge.sitekey = html_challenge.sitekey
    if html_challenge.action and html_challenge.action != "verify":
        runtime_challenge.action = html_challenge.action
    return runtime_challenge


# ---------------------------------------------------------------------------
# Solving
# ---------------------------------------------------------------------------
def _task_for(challenge: CaptchaChallenge, min_score: float) -> dict:
    """The API-v2 `task` object for a challenge (https://2captcha.com/api-docs).

      turnstile      -> TurnstileTaskProxyless
      recaptcha v3   -> RecaptchaV3TaskProxyless, with pageAction + minScore
      v2 invisible   -> RecaptchaV2TaskProxyless with isInvisible: true
      v2 checkbox    -> RecaptchaV2TaskProxyless

    The `*Proxyless` types let 2Captcha use its own addresses. The
    non-proxyless variants exist for when the token must come from the same IP
    that submits it; over the Scraping Browser API the page and the solve
    already share an exit.
    """
    if challenge.is_turnstile:
        return {"type": "TurnstileTaskProxyless",
                "websiteURL": challenge.page_url,
                "websiteKey": challenge.sitekey}
    if challenge.is_v3:
        score = min(V3_ALLOWED_MIN_SCORES, key=lambda a: abs(a - min_score))
        if score != min_score:
            logger.info("minScore %.2f is not one of %s — using %.1f.",
                        min_score, V3_ALLOWED_MIN_SCORES, score)
        task = {"type": "RecaptchaV3TaskProxyless",
                "websiteURL": challenge.page_url,
                "websiteKey": challenge.sitekey,
                "minScore": score}
        if challenge.action and challenge.action != "verify":
            task["pageAction"] = challenge.action
        return task
    task = {"type": "RecaptchaV2TaskProxyless",
            "websiteURL": challenge.page_url,
            "websiteKey": challenge.sitekey}
    if challenge.is_invisible_v2:
        task["isInvisible"] = True
    return task


def _post_json(url: str, payload: dict, timeout: int = 30) -> dict:
    """POST JSON and return the decoded body, redacting any leak on the way out.

    The key rides in the body on this path, but an exception can still carry a
    proxy URL or a redirected location, and redacting unconditionally costs
    nothing (measured elsewhere in this family: mask globally, not once).
    """
    try:
        resp = requests.post(url, json=payload, timeout=timeout)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as e:
        raise RuntimeError(f"2captcha request failed: "
                           f"{redact_secret_patterns(str(e))}") from None


def _solve_v2(api_key: str, challenge: CaptchaChallenge, min_score: float = 0.7,
              poll_interval: int = 5, max_wait: int = 180) -> str:
    """Solve via API v2: createTask, then poll getTaskResult."""
    task = _task_for(challenge, min_score)
    logger.info("createTask: %s (sitekey=%s)", task["type"], challenge.sitekey)
    payload = _post_json(TWOCAPTCHA_CREATE_TASK_URL,
                         {"clientKey": api_key, "task": task})
    if payload.get("errorId"):
        raise RuntimeError(f"createTask failed: {payload.get('errorCode')} — "
                           f"{payload.get('errorDescription')}")
    task_id = payload["taskId"]

    waited = 0
    while waited < max_wait:
        time.sleep(poll_interval)
        waited += poll_interval
        result = _post_json(TWOCAPTCHA_GET_RESULT_URL,
                            {"clientKey": api_key, "taskId": task_id})
        if result.get("errorId"):
            raise RuntimeError(f"getTaskResult failed: {result.get('errorCode')} — "
                               f"{result.get('errorDescription')}")
        if result.get("status") == "ready":
            solution = result.get("solution") or {}
            token = (solution.get("token")
                     or solution.get("gRecaptchaResponse"))
            if not token:
                raise RuntimeError(f"task ready but no token in solution: "
                                   f"{sorted(solution)}")
            logger.info("2captcha solved %s in ~%ds.", challenge.kind, waited)
            return token
    raise TimeoutError(f"2captcha did not return a token within {max_wait}s")


def _solve_v1(api_key: str, challenge: CaptchaChallenge, min_score: float = 0.7,
              poll_interval: int = 5, max_wait: int = 180) -> str:
    """Legacy API v1: submit to in.php, poll res.php.

    **res.php takes the key as a QUERY PARAMETER**, so every exception
    `requests` raises on this path carries the key in its text — which is why
    both calls are wrapped and re-raised through `redact_secret_patterns`.
    That is the single most-copied bug in this family.
    """
    payload = {"key": api_key, "pageurl": challenge.page_url, "json": 1}
    if challenge.is_turnstile:
        payload.update({"method": "turnstile", "sitekey": challenge.sitekey})
    else:
        payload.update({"method": "userrecaptcha", "googlekey": challenge.sitekey})
        if challenge.is_v3:
            payload.update({"version": "v3", "action": challenge.action,
                            "min_score": min_score})
        elif challenge.is_invisible_v2:
            payload["invisible"] = 1

    logger.info("Submitting to 2captcha (v1) as %s (sitekey=%s)",
                challenge.kind, challenge.sitekey)
    try:
        submit = requests.post(TWOCAPTCHA_IN_URL, data=payload, timeout=30)
        submit.raise_for_status()
        body = submit.json()
    except requests.RequestException as e:
        raise RuntimeError(f"2captcha submit failed: "
                           f"{redact_secret_patterns(str(e))}") from None
    if body.get("status") != 1:
        raise RuntimeError(f"2captcha submit error: {body.get('request')}")

    task_id = body["request"]
    waited = 0
    while waited < max_wait:
        time.sleep(poll_interval)
        waited += poll_interval
        try:
            result = requests.get(TWOCAPTCHA_RES_URL, params={
                "key": api_key, "action": "get", "id": task_id, "json": 1,
            }, timeout=30).json()
        except requests.RequestException as e:
            raise RuntimeError(f"2captcha polling failed: "
                               f"{redact_secret_patterns(str(e))}") from None
        if result.get("status") == 1:
            logger.info("2captcha solved %s in ~%ds.", challenge.kind, waited)
            return result["request"]
        if result.get("request") != "CAPCHA_NOT_READY":
            raise RuntimeError(f"2captcha polling error: {result.get('request')}")
    raise TimeoutError(f"2captcha did not return a token within {max_wait}s")


def get_balance(api_key: str) -> float:
    """Account balance via API v2 — a cheap preflight that a key is live."""
    body = _post_json(TWOCAPTCHA_BALANCE_URL, {"clientKey": api_key})
    if body.get("errorId"):
        raise RuntimeError(f"getBalance failed: {body.get('errorCode')}")
    return float(body["balance"])


def solve(challenge: CaptchaChallenge, twocaptcha_api_key: Optional[str],
          api_version: str = "v2", min_score: float = 0.7) -> str:
    """Solve `challenge` through 2Captcha and return the token."""
    if not challenge.sitekey:
        raise RuntimeError(
            "A challenge is on this page but its sitekey could not be read, so "
            "there is nothing to send to the API. Re-run with --dump-html and "
            "look at the captured markup.")
    if not twocaptcha_api_key:
        raise RuntimeError(
            "A captcha was detected but no 2captcha API key was given. Pass "
            "--twocaptcha-key, or set TWOCAPTCHA_KEY in .env. Over the "
            "Scraping Browser API you may need neither: Captcha.setAutoSolve "
            "can clear it inside the browser.")
    solver = _solve_v1 if api_version == "v1" else _solve_v2
    return solver(twocaptcha_api_key, challenge, min_score=min_score)


# ---------------------------------------------------------------------------
# Token injection
# ---------------------------------------------------------------------------
# Two spellings of the same operation, and they are NOT interchangeable:
# Playwright and pyppeteer take an arrow function, while Selenium's
# execute_script takes a function BODY with an explicit `return`. Shipping one
# and letting an engine improvise is how the three quietly stop agreeing
# — so both are written out here and the offline suite asserts
# that each engine uses the right one.
INJECT_TOKEN_FN = """
(token) => {
  const write = (name) => {
    let el = document.querySelector('[name="' + name + '"]');
    if (!el) {
      el = document.createElement('textarea');
      el.name = name;
      el.id = name;
      el.style.display = 'none';
      document.body.appendChild(el);
    }
    el.value = token;
  };
  write('cf-turnstile-response');
  write('g-recaptcha-response');
  try {
    const widget = document.querySelector('.cf-turnstile[data-callback]');
    const cb = widget && widget.getAttribute('data-callback');
    if (cb && typeof window[cb] === 'function') window[cb](token);
  } catch (e) { /* best effort, non-fatal */ }
  try {
    if (window.___grecaptcha_cfg && window.___grecaptcha_cfg.clients) {
      Object.values(window.___grecaptcha_cfg.clients).forEach((client) => {
        Object.values(client).forEach((prop) => {
          if (prop && typeof prop === 'object') {
            Object.values(prop).forEach((cb) => {
              if (typeof cb === 'function') { try { cb(token); } catch (e) {} }
            });
          }
        });
      });
    }
  } catch (e) { /* best effort, non-fatal */ }
  return true;
}
"""

# The same thing as a function BODY, for Selenium: `arguments[0]` is the token.
INJECT_TOKEN_BODY = """
var token = arguments[0];
function write(name) {
  var el = document.querySelector('[name="' + name + '"]');
  if (!el) {
    el = document.createElement('textarea');
    el.name = name;
    el.id = name;
    el.style.display = 'none';
    document.body.appendChild(el);
  }
  el.value = token;
}
write('cf-turnstile-response');
write('g-recaptcha-response');
try {
  var widget = document.querySelector('.cf-turnstile[data-callback]');
  var cb = widget && widget.getAttribute('data-callback');
  if (cb && typeof window[cb] === 'function') { window[cb](token); }
} catch (e) { /* best effort, non-fatal */ }
return true;
"""
