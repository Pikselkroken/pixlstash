"""Collect download and engagement metrics from GitHub, PyPI, and pypistats.

Appends one entry per day to metrics/history.json.
Run via the collect-metrics GitHub Actions workflow.
"""

import json
import os
import urllib.error
import urllib.request
from datetime import datetime, timezone

OWNER = "pikselkroken"
REPO = "pixlstash"
PYPI_PACKAGE = "pixlstash"
HISTORY_PATH = "metrics/history.json"


def gh_get(path, token=None):
    if token is None:
        token = os.environ["GITHUB_TOKEN"]
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def pypi_get(path):
    import time

    req = urllib.request.Request(
        f"https://pypistats.org/api{path}",
        headers={"User-Agent": f"{REPO}-metrics-collector/1.0"},
    )
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < 3:
                wait = 10 * (attempt + 1)
                print(f"pypistats rate-limited, retrying in {wait}s...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError(f"Failed to fetch PyPI stats for path {path!r} after 4 attempts")


def fetch_stars_and_forks():
    data = gh_get(f"/repos/{OWNER}/{REPO}")
    return data["stargazers_count"], data["forks_count"]


def fetch_clones_today():
    # Traffic API requires a PAT with repo scope (GITHUB_TOKEN is insufficient).
    token = os.environ.get("METRICS_TOKEN")
    if not token:
        print("WARNING: METRICS_TOKEN not set — skipping clone traffic.")
        return {"count": None, "uniques": None, "source_date": None}
    try:
        data = gh_get(f"/repos/{OWNER}/{REPO}/traffic/clones?per=day", token=token)
    except urllib.error.HTTPError as e:
        print(f"WARNING: Could not fetch clone traffic ({e}) — skipping.")
        return {"count": None, "uniques": None, "source_date": None}

    clones = data.get("clones", [])
    if not clones:
        return {"count": None, "uniques": None, "source_date": None}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    for entry in clones:
        if entry["timestamp"] == today:
            return {
                "count": entry["count"],
                "uniques": entry["uniques"],
                "source_date": entry["timestamp"][:10],
            }

    # The traffic endpoint can lag by a day. Use the latest available day
    # instead of writing an incorrect zero for "today".
    latest = max(clones, key=lambda entry: entry.get("timestamp", ""))
    print(
        "INFO: Today's clone traffic is not yet available; "
        f"using latest snapshot from {latest.get('timestamp', 'unknown')}."
    )
    return {
        "count": latest.get("count"),
        "uniques": latest.get("uniques"),
        "source_date": (latest.get("timestamp") or "")[:10] or None,
    }


def fetch_recent_clones_by_date():
    """Return recent clone traffic keyed by date (YYYY-MM-DD)."""
    token = os.environ.get("METRICS_TOKEN")
    if not token:
        print("WARNING: METRICS_TOKEN not set — cannot backfill clone traffic.")
        return {}
    try:
        data = gh_get(f"/repos/{OWNER}/{REPO}/traffic/clones?per=day", token=token)
    except urllib.error.HTTPError as e:
        print(f"WARNING: Could not fetch clone traffic for backfill ({e}).")
        return {}

    by_date = {}
    for entry in data.get("clones", []):
        source_date = (entry.get("timestamp") or "")[:10]
        if not source_date:
            continue
        by_date[source_date] = {
            "count": entry.get("count"),
            "uniques": entry.get("uniques"),
            "source_date": source_date,
        }
    return by_date


def backfill_clone_history(history):
    """Backfill clone metrics for historical entries using traffic API data."""
    recent_clones = fetch_recent_clones_by_date()
    if not recent_clones:
        return 0, 0

    updated = 0
    matched = 0
    for entry in history.get("history", []):
        date_key = entry.get("date")
        if date_key not in recent_clones:
            continue

        matched += 1
        current = entry.get("clones_today") or {}
        replacement = recent_clones[date_key]
        if (
            current.get("count") != replacement.get("count")
            or current.get("uniques") != replacement.get("uniques")
            or current.get("source_date") != replacement.get("source_date")
        ):
            entry["clones_today"] = replacement
            updated += 1

    return updated, matched


def fetch_release_downloads():
    releases = gh_get(f"/repos/{OWNER}/{REPO}/releases")
    by_release = {}
    total = 0
    for release in releases:
        count = sum(a["download_count"] for a in release.get("assets", []))
        by_release[release["tag_name"]] = count
        total += count
    return {"total": total, "by_release": by_release}


def fetch_pypi_downloads():
    try:
        data = pypi_get(f"/packages/{PYPI_PACKAGE}/recent")
        d = data.get("data", {})
        return {
            "last_day": d.get("last_day", 0),
            "last_week": d.get("last_week", 0),
            "last_month": d.get("last_month", 0),
        }
    except urllib.error.HTTPError as e:
        print(f"WARNING: Could not fetch PyPI downloads ({e}) — skipping.")
        return {"last_day": None, "last_week": None, "last_month": None}


def load_history():
    os.makedirs("metrics", exist_ok=True)
    try:
        with open(HISTORY_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"history": []}


def save_history(history):
    with open(HISTORY_PATH, "w") as f:
        json.dump(history, f, indent=2)
        f.write("\n")


def main():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    history = load_history()

    if os.environ.get("BACKFILL_CLONES") == "1":
        updated, matched = backfill_clone_history(history)
        if matched == 0:
            print("Backfill complete: no history dates matched available traffic window.")
            return
        save_history(history)
        print(
            "Backfill complete: "
            f"updated={updated}, matched_dates={matched}, total_entries={len(history['history'])}"
        )
        return

    stars, forks = fetch_stars_and_forks()
    clones = fetch_clones_today()
    releases = fetch_release_downloads()
    pypi = fetch_pypi_downloads()

    entry = {
        "date": today,
        "stars": stars,
        "forks": forks,
        "clones_today": clones,
        "release_downloads": releases,
        "pypi_downloads": pypi,
        # GHCR pull counts are not exposed via the GitHub Packages API
        # (the /orgs/{org}/packages/container/{name}/versions endpoint returns
        # only version metadata — no download_count field).
        "ghcr_pulls": None,
    }

    # Replace any existing entry for today (re-runs overwrite rather than duplicate).
    history["history"] = [e for e in history["history"] if e.get("date") != today]
    history["history"].append(entry)

    save_history(history)
    print(
        f"Recorded metrics for {today}: "
        f"stars={stars}, "
        f"release_downloads={releases['total']}, "
        f"pypi_last_month={pypi['last_month']}, "
        f"clones_today={clones['count']}"
        + (
            f" (source_date={clones['source_date']})"
            if clones.get("source_date")
            else ""
        )
    )


if __name__ == "__main__":
    main()
