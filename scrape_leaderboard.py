#!/usr/bin/env python3
"""Scrape the full HackerRank Orchestrate June26 'multi-modal-review' leaderboard.

Pulls every participant (all pages) into leaderboard.json with their score and
per-stage breakdown. Re-run this each time a new evaluation stage is released
(output_csv, then code_zip) to capture the new scores.

Usage:
    # cookie + csrf can be passed as args or via env vars HR_COOKIE / HR_CSRF
    python3 scrape_leaderboard.py --cookie "_hrank_session=...; web_browser_id=..." \
                                  --csrf "<x-csrf-token>"

How to get a fresh cookie + csrf (they expire — re-grab when you get 401/403):
    1. Open the leaderboard page in Chrome, logged in.
    2. DevTools > Network > Fetch/XHR, reload, click the
       'leaderboard?contest_id=385706&challenge_id=581108' request.
    3. Right-click > Copy > Copy as cURL.
    4. From that cURL: the value after `-b` is the cookie; the
       `x-csrf-token:` header is the csrf. You only really need
       `_hrank_session`, `web_browser_id`, `user_type`, `hrc_l_i` from the cookie.
"""
import argparse, gzip, json, math, os, sys, time, urllib.request

# Known leaderboards (slug -> ids + page url). Pass --contest-id/--challenge-id to
# scrape any other one; find its ids via the page's __NEXT_DATA__ (the challenge
# object's `id` is challenge_id, its `primary_contest.id` is contest_id).
KNOWN = {
    "june26-multi-modal-review": dict(
        contest_id=385706, challenge_id=581108,
        url="https://www.hackerrank.com/contests/hackerrank-orchestrate-june26/challenges/multi-modal-review/leaderboard"),
    "may26-support-agent": dict(
        contest_id=381073, challenge_id=574790,
        url="https://www.hackerrank.com/contests/hackerrank-orchestrate-may26/challenges/support-agent/leaderboard"),
}
API = "https://www.hackerrank.com/community/hackathon/leaderboard"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "Chrome/149.0.0.0 Safari/537.36")
PAGE_SIZE = 30


def fetch(page, cookie, csrf, contest_id, challenge_id, referer):
    url = f"{API}?contest_id={contest_id}&challenge_id={challenge_id}&page={page}"
    req = urllib.request.Request(url, headers={
        "accept": "*/*", "content-type": "application/json",
        "accept-encoding": "gzip", "cookie": cookie, "x-csrf-token": csrf,
        "user-agent": UA, "referer": referer})
    with urllib.request.urlopen(req, timeout=30) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return json.loads(raw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cookie", default=os.environ.get("HR_COOKIE"))
    ap.add_argument("--csrf", default=os.environ.get("HR_CSRF"))
    ap.add_argument("--board", choices=list(KNOWN), default="june26-multi-modal-review",
                    help="which known leaderboard to scrape")
    ap.add_argument("--contest-id", type=int, help="override contest_id (for an unlisted board)")
    ap.add_argument("--challenge-id", type=int, help="override challenge_id")
    ap.add_argument("--out", default="", help="output file (default leaderboard-<board>.json)")
    ap.add_argument("--scraped-at", default="", help="date stamp for metadata, e.g. 2026-06-26")
    a = ap.parse_args()
    if not a.cookie or not a.csrf:
        sys.exit("Need --cookie and --csrf (or HR_COOKIE / HR_CSRF env vars). See file header.")

    board = KNOWN[a.board]
    contest_id = a.contest_id or board["contest_id"]
    challenge_id = a.challenge_id or board["challenge_id"]
    source = board["url"]
    out_path = a.out or f"leaderboard-{a.board}.json"

    def get(p):
        return fetch(p, a.cookie, a.csrf, contest_id, challenge_id, source)["data"]

    try:
        first = get(1)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} on page 1 — cookie/csrf likely expired, re-grab them. ({e})")

    total = first["total_participants"]
    stages = first["evaluation"]["stages"]
    pages = math.ceil(total / PAGE_SIZE)
    print(f"total_participants={total}, pages={pages}")
    print("stages:", ", ".join(f"{s['key']}({'released' if s['released'] else 'pending'})" for s in stages))

    entries = list(first["entries"])
    seen = {e["rank"] for e in entries}
    for p in range(2, pages + 1):
        for attempt in (1, 2):
            try:
                d = get(p)
                break
            except Exception as ex:
                if attempt == 2:
                    raise
                print(f"  page {p} error ({ex}); retrying"); time.sleep(2)
        for e in d["entries"]:
            if e["rank"] not in seen:
                seen.add(e["rank"]); entries.append(e)
        if p % 10 == 0 or p == pages:
            print(f"page {p}: collected {len(entries)}/{total}")
        time.sleep(0.25)

    entries.sort(key=lambda e: e["rank"])
    out = {
        "board": a.board, "contest_id": contest_id, "challenge_id": challenge_id,
        "source": source, "scraped_at": a.scraped_at,
        "total_participants": total, "evaluation_stages": stages,
        "entries": entries,
    }
    with open(out_path, "w") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
    print(f"\nWROTE {out_path} with {len(entries)} entries (unique ranks={len(seen)})")


if __name__ == "__main__":
    main()
