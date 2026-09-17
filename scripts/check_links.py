#!/usr/bin/env python3
"""
Local link checker for awesome-kicad-projects.

Replacement for the GitHub Action (awesome_bot) so you can run the check
directly on your machine:

    python scripts/check_links.py
    python scripts/check_links.py README.md launch-kit.md   # specific files
    python scripts/check_links.py --strict                  # fail on redirects/timeouts too

Exit code is 0 when no broken links are found, 1 otherwise (2 on misuse).
"""

import argparse
import concurrent.futures
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Mirrors the workflow's --white-list
WHITELIST = {
    "https://github.com/way2pramil/awesome-kicad-projects/stargazers",
    "http://github.com/way2pramil/awesome-kicad-projects/stargazers",
}

DEFAULT_FILES = ["README.md", "CONTRIBUTING.md", "CODE_OF_CONDUCT.md", "launch-kit.md"]

# Markdown links/images + bare URLs
MD_LINK_RE = re.compile(r"!?\[[^\]]*\]\(\s*(<)?([^)\s>]+)(?(1)>|(?:\s+\"[^\"]*\")?\s*)\)")
BARE_URL_RE = re.compile(r"https?://[^\s)\]>\"'`]+")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

_ctx = ssl.create_default_context()


def extract_links(path: Path):
    """Return list of (line_number, url) for http(s) links in a markdown file."""
    links = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        print(f"!! cannot read {path}: {e}")
        return links
    in_fence = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        found = set()
        for m in MD_LINK_RE.finditer(line):
            url = m.group(2).strip()
            if url.startswith(("http://", "https://")):
                found.add(url)
        for m in BARE_URL_RE.finditer(line):
            found.add(m.group(0).rstrip(".,;"))
        for url in sorted(found):
            links.append((lineno, url))
    return links


def check_url(url: str, timeout: float = 20.0):
    """
    Check one URL. Returns (status, ok, detail).
    status: 'ok' | 'redirect' | 'timeout' | 'broken'
    """
    if url in WHITELIST:
        return "ok", True, "whitelisted"

    def attempt(method):
        req = urllib.request.Request(
            url,
            method=method,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
            },
        )
        return urllib.request.urlopen(req, timeout=timeout, context=_ctx)

    try:
        resp = attempt("HEAD")
    except urllib.error.HTTPError as e:
        if e.code in (403, 405, 501):  # HEAD not allowed / blocked -> retry with GET
            try:
                resp = attempt("GET")
            except urllib.error.HTTPError as e2:
                if e2.code == 403:
                    # Bot protection (e.g. Codeberg challenge) - verify in a browser
                    return "blocked", True, "HTTP 403 (bot protection; open in browser to verify)"
                return "broken", False, f"HTTP {e2.code} {e2.reason}"
            except (urllib.error.URLError, TimeoutError, OSError) as e2:
                return classify_error(e2)
        else:
            return "broken", False, f"HTTP {e.code} {e.reason}"
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return classify_error(e)

    final_url = resp.geturl()
    code = resp.getcode()
    redirected = final_url.rstrip("/") != url.rstrip("/")
    if 200 <= code < 300:
        if redirected:
            return "redirect", True, f"-> {final_url}"
        return "ok", True, f"HTTP {code}"
    return "broken", False, f"HTTP {code}"


def classify_error(e: Exception):
    reason = getattr(e, "reason", e)
    msg = str(reason)
    if isinstance(reason, TimeoutError) or "timed out" in msg.lower():
        return "timeout", True, f"timeout: {msg}"  # allowed, like awesome_bot
    if isinstance(reason, ssl.SSLError):
        return "ok", True, f"ssl issue (allowed): {msg}"  # allowed, like awesome_bot
    return "broken", False, msg


def main():
    ap = argparse.ArgumentParser(description="Check links in repo markdown files.")
    ap.add_argument("files", nargs="*", help="Markdown files to check (default: repo docs)")
    ap.add_argument("--strict", action="store_true",
                    help="Also fail on redirects and timeouts (default: only real failures)")
    ap.add_argument("--workers", type=int, default=8, help="Parallel checks (default: 8)")
    ap.add_argument("--delay", type=float, default=0.2,
                    help="Delay in seconds between launches per worker (default: 0.2)")
    args = ap.parse_args()

    files = args.files or DEFAULT_FILES
    all_links = []  # (file, lineno, url)
    seen = set()
    dupes = 0
    for f in files:
        p = Path(f)
        if not p.is_absolute():
            p = REPO_ROOT / f
        if not p.exists():
            print(f"!! file not found: {f}")
            return 2
        for lineno, url in extract_links(p):
            if url in seen:
                dupes += 1  # --allow-dupe: duplicates are fine
                continue
            seen.add(url)
            all_links.append((p.name, lineno, url))

    print(f"Checking {len(all_links)} unique links across {len(files)} file(s) "
          f"({dupes} duplicates skipped)...\n")

    results = {"ok": [], "redirect": [], "timeout": [], "blocked": [], "broken": []}

    def worker(item):
        fname, lineno, url = item
        time.sleep(args.delay)  # mild politeness, like --request-delay
        status, ok, detail = check_url(url)
        return fname, lineno, url, status, detail

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = [ex.submit(worker, item) for item in all_links]
        done = 0
        for fut in concurrent.futures.as_completed(futs):
            fname, lineno, url, status, detail = fut.result()
            results[status].append((fname, lineno, url, detail))
            done += 1
            mark = {"ok": ".", "redirect": "r", "timeout": "t", "blocked": "b", "broken": "X"}[status]
            print(mark, end="", flush=True)
    print("\n")

    for status, label in (("broken", "BROKEN"), ("blocked", "BLOCKED (verify in browser)"),
                          ("timeout", "TIMEOUT"), ("redirect", "REDIRECT"), ("ok", "OK")):
        items = sorted(results[status], key=lambda x: (x[0], x[1]))
        if not items:
            continue
        print(f"== {label} ({len(items)}) ==")
        if status != "ok":
            for fname, lineno, url, detail in items:
                print(f"  {fname}:{lineno}  {url}\n      {detail}")
        print()

    broken = len(results["broken"])
    timeouts = len(results["timeout"])
    redirects = len(results["redirect"])
    blocked = len(results["blocked"])
    print(f"Summary: {len(all_links)} checked | "
          f"{len(results['ok'])} ok | {redirects} redirect | "
          f"{timeouts} timeout | {blocked} blocked | {broken} broken")

    if args.strict:
        failures = broken + timeouts + redirects
    else:
        failures = broken
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
