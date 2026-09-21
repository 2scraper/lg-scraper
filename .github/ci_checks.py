#!/usr/bin/env python3
"""
CI checks that are too long to live inside the workflow YAML.

**One implementation, invoked from both CI and the offline suite.** The
family's older repos carried this file AND an inline `grep` in tests.yml doing
a narrower version of the same job, and the two disagreed in the direction
that matters: the inline version matched only `ws://` and `wss://`, so an
`http://user:pass@` credential would have sailed past CI, while the shipped
file failed on its own repository because its allowlist was stale. A check
that fails on its own repo is a check nobody can read; a check nothing runs is
not a check. smoke_test.py calls `secret_check()` directly and
asserts that the workflow calls THIS script rather than reimplementing it.

Run any of these from the repo root:

    python3 .github/ci_checks.py --secret-check
    python3 .github/ci_checks.py --sample-check
    python3 .github/ci_checks.py --help-check
    python3 .github/ci_checks.py --all
"""

import argparse
import csv
import json
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Engine libraries are deliberately absent in the offline CI job. An
# ImportError naming one of these is expected, not a failure.
ENGINE_LIBS = ("playwright", "pyppeteer", "selenium")

CLIS = ["playwright_scraper.py", "puppeteer_scraper.py", "selenium_scraper.py",
        "scraper_api_client.py", "fingerprint_client.py", "env_config.py",
        "diff_runs.py"]

SAMPLE_FILES = ("sample_output.json", "sample_output.csv")

# Phrases that show up in hand-written or templated sample data. The point of
# committing a sample is that it came from a real run; a placeholder teaches
# readers field names and value shapes that do not exist.
FABRICATION_MARKERS = ("sample-listing", "example listing", "lorem ipsum",
                       "your_api_key", "example.com/12345")

# A URL carrying real credentials: scheme://something:something@host.
# Deliberately not spelled out as an example anywhere in this file — it scans
# itself, and an illustrative credential in a comment turns the build red for
# no reason. That happened on the first run in an older repo.
CREDENTIALLED_URL = re.compile(r"(?:ws|wss|https?|socks5)://[^\s\"'/]+:[^\s\"'/]+@")

# Documented placeholders, which are supposed to look like the real thing,
# and the offline suite's own masking fixtures. Both halves matter: the
# family's shipped version of this check FAILED ON ITS OWN MAIN for want of
# the second, and a check that is red on a clean tree is a check nobody reads.
CREDENTIAL_ALLOWED = ("USER:PASS", "user:pass", "ACCOUNT:PASSWORD", "{login}",
                      "{user}", "***", "password}@", "LOGIN:PASSWORD",
                      "real-login:realpass", "login:pass@",
                      # the offline suite's redaction fixture: a deliberately
                      # fake Scraping Browser endpoint, which has to LOOK like
                      # a credential for that test to mean anything
                      "s3cr3tpassw0rd")

# RFC 2606 reserves these for documentation: a credential pointed at one of
# them cannot be a real exit, so the fixtures the suite needs are allowed
# without widening the pattern itself.
EXAMPLE_HOSTS = re.compile(r"@[\w.-]*(?:example\.(?:com|org|net)|localhost|"
                           r"127\.0\.0\.1|host:\d+)\b")

# A 2Captcha API key is a 32-character hex string.
HEX32 = re.compile(r"\b[0-9a-f]{32}\b")
HEX32_ALLOWED = ("sha", "hash", "nonce", "example", "md5", "digest", "checksum",
                 '"0" * 32', "0123456789abcdef0123456789abcdef")

# Session material and personal data that must never reach a committed
# fixture. Matched as PATTERNS, not as the literals of one old capture, so the
# next capture's values are caught too.
SESSION_PATTERNS = (
    (re.compile(r'"?(?:session[_-]?id|sessionId|csrf|authenticity_token|'
                r'access[_-]?token|api[_-]?token)"?\s*[:=]\s*"[^"]{8,}"',
                re.IGNORECASE), "a session or CSRF token"),
    (re.compile(r'"broker_name"\s*:\s*"(?!Broker name redacted)[^"]+"'),
     "a real broker's name (redact it in committed samples)"),
)

SCANNED_SUFFIXES = (".py", ".md", ".txt", ".yml", ".yaml", ".example", ".toml",
                    ".json", ".csv")
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "captures", "legacy",
             ".pytest_cache"}


def scanned_files():
    for path in sorted(REPO.rglob("*")):
        if not path.is_file() or path.suffix not in SCANNED_SUFFIXES:
            continue
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        yield path


def help_check():
    failed = []
    for name in CLIS:
        script = REPO / name
        if not script.is_file():
            print(f"missing  {name}")
            failed.append(name)
            continue
        result = subprocess.run([sys.executable, str(script), "--help"],
                                capture_output=True, text=True, cwd=REPO)
        if result.returncode == 0:
            print(f"ok       {name}")
            continue
        blob = result.stdout + result.stderr
        if "ModuleNotFoundError" in blob and any(lib in blob for lib in ENGINE_LIBS):
            print(f"skipped  {name} (engine library not installed here)")
            continue
        print(f"FAILED   {name}\n{blob}")
        failed.append(name)
    return failed


def sample_check():
    failed = []
    for name in SAMPLE_FILES:
        if not (REPO / name).is_file():
            failed.append(f"{name} is missing — regenerate it from a real run")
    if failed:
        return failed

    rows = json.loads((REPO / "sample_output.json").read_text(encoding="utf-8"))
    if not rows:
        return ["sample_output.json is empty — a run that found nothing is not a sample"]

    blob = json.dumps(rows).lower()
    hits = [m for m in FABRICATION_MARKERS if m in blob]
    if hits:
        failed.append(f"sample_output.json looks fabricated: {hits}")

    # The committed sample doubles as a schema test: rename a field in the
    # code and forget the sample, and this fails rather than the docs rotting.
    sys.path.insert(0, str(REPO))
    from dataclasses import asdict
    from output_writer import Product
    expected = list(asdict(Product()).keys())

    for i, row in enumerate(rows):
        if list(row.keys()) != expected:
            failed.append(f"sample_output.json row {i}: columns differ from "
                          f"output_writer.Product")
            break
    with (REPO / "sample_output.csv").open(newline="", encoding="utf-8") as handle:
        header = next(csv.reader(handle))
    if header != expected:
        failed.append("sample_output.csv header differs from output_writer.Product")

    if not failed:
        discounted = sum(1 for r in rows if r.get("original_price"))
        print(f"ok       {len(rows)} rows, {len(expected)} columns, "
              f"{discounted} with a price drop, schema matches")
    return failed


def secret_check():
    failed = []
    scanned = 0
    for path in scanned_files():
        scanned += 1
        rel = path.relative_to(REPO)
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            if (CREDENTIALLED_URL.search(line)
                    and not EXAMPLE_HOSTS.search(line)
                    and not any(token in line for token in CREDENTIAL_ALLOWED)):
                failed.append(f"{rel}:{lineno} looks like a URL with real "
                              f"credentials in it")
            for match in HEX32.findall(line):
                if any(token in line.lower() for token in HEX32_ALLOWED):
                    continue
                failed.append(f"{rel}:{lineno} contains {match[:6]}… — a 32-char "
                              f"hex string, the shape of a 2captcha key")
        for pattern, what in SESSION_PATTERNS:
            for match in pattern.finditer(text):
                line_no = text[:match.start()].count("\n") + 1
                failed.append(f"{rel}:{line_no} contains {what}")

    if not failed:
        print(f"ok       {scanned} files scanned, nothing credential-shaped, "
              f"no session material, no personal data")
    return failed


CHECKS = {"help": help_check, "sample": sample_check, "secret": secret_check}


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--help-check", action="store_true",
                        help="Every shipped CLI answers --help")
    parser.add_argument("--sample-check", action="store_true",
                        help="sample_output.* exist, are real, match the schema")
    parser.add_argument("--secret-check", action="store_true",
                        help="No credentials, session material or personal data committed")
    parser.add_argument("--all", action="store_true", help="All of the above")
    args = parser.parse_args()

    selected = [name for name in CHECKS
                if args.all or getattr(args, f"{name}_check")]
    if not selected:
        parser.error("pick at least one check, or --all")

    failures = []
    for name in selected:
        print(f"--- {name} check")
        failures += [f"[{name}] {line}" for line in CHECKS[name]()]

    if failures:
        print()
        for line in failures:
            print("FAILED:", line)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
