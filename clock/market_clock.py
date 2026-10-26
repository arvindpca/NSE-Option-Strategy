"""Market clock: market time me sahi minute pe paper trading workflows chalata hai.

GitHub ka cron schedule 5/15 min wale jobs ko bahut late ya bilkul nahi chalata.
Ye script ek lambe job me chalti hai aur har minute check karke
workflow_dispatch se runs start karti hai.

Usage: python clock/market_clock.py HH:MM   (IST, is time tak chalegi)
"""
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
OWN_REPO = os.environ.get("GITHUB_REPOSITORY", "arvindpca/NSE-Option-Strategy")
OWN_TOKEN = os.environ.get("GITHUB_TOKEN", "")
# Private repo ke workflows chalane ke liye PAT chahiye (secret DISPATCH_PAT).
PAT = os.environ.get("DISPATCH_PAT", "")
STOCK_REPO = "arvindpca/Indian-Stock-Intraday-15Minuts-Chart"


def hm(t):
    return t.hour * 60 + t.minute


def between(m, start, end):
    return hm_s(start) <= m <= hm_s(end)


def hm_s(s):
    h, mi = s.split(":")
    return int(h) * 60 + int(mi)


# (repo, workflow file, token, due(minute_of_day_IST) -> bool)
JOBS = [
    # Option paper: 9:32 se 15:32 tak har 15 min
    (OWN_REPO, "option-paper-live.yml", "own",
     lambda m: between(m, "09:32", "15:32") and m % 15 == 2),
    # Reliance paper: 9:30 se 15:45 tak har 5 min
    (STOCK_REPO, "reliance-paper.yml", "pat",
     lambda m: between(m, "09:30", "15:45") and m % 5 == 0),
    # Intraday scanner: 9:15 se 15:30 tak har 15 min
    (STOCK_REPO, "intraday-scanner.yml", "pat",
     lambda m: between(m, "09:15", "15:30") and m % 15 == 0),
    (STOCK_REPO, "paper-swing-3pm.yml", "pat", lambda m: m == hm_s("14:48")),
    (STOCK_REPO, "paper-trade-settle.yml", "pat", lambda m: m == hm_s("15:40")),
    (STOCK_REPO, "paper-swing.yml", "pat",
     lambda m: m in (hm_s("16:37"), hm_s("18:37"))),
]


def dispatch(repo, wf, token):
    req = urllib.request.Request(
        f"https://api.github.com/repos/{repo}/actions/workflows/{wf}/dispatches",
        data=json.dumps({"ref": "main"}).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Accept": "application/vnd.github+json",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status
    except urllib.error.HTTPError as e:
        return f"{e.code} {e.read()[:200]!r}"
    except Exception as e:  # network hiccup: agle minute fir try nahi, bas log
        return f"error {e}"


def main():
    end = hm_s(sys.argv[1])
    now = datetime.now(IST)
    if now.weekday() >= 5:
        print("Aaj Saturday/Sunday hai, kuch nahi chalana.")
        return
    if not PAT:
        print("DISPATCH_PAT secret nahi hai: sirf Option paper chalega "
              "(Reliance/Scanner/Swing ke liye PAT add karein).")
    fired = set()
    while True:
        now = datetime.now(IST)
        m = hm(now)
        if m >= end:
            print(f"{now:%H:%M} IST: clock khatam.")
            return
        for repo, wf, which, due in JOBS:
            key = (wf, now.date(), m)
            if key in fired or not due(m):
                continue
            fired.add(key)
            token = OWN_TOKEN if which == "own" else PAT
            if not token:
                continue
            print(f"{now:%H:%M} IST  {repo.split('/')[1]} / {wf} -> {dispatch(repo, wf, token)}",
                  flush=True)
        time.sleep(15)


if __name__ == "__main__":
    main()
