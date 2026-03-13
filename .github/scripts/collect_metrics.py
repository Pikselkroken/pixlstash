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


def gh_get(path):
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
    req = urllib.request.Request(
        f"https://pypistats.org/api{path}",
        headers={"User-Agent": f"{REPO}-metrics-collector/1.0"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def fetch_stars_and_forks():
    data = gh_get(f"/repos/{OWNER}/{REPO}")
    return data["stargazers_count"], data["forks_count"]


def fetch_clones_today():
    data = gh_get(f"/repos/{OWNER}/{REPO}/traffic/clones?per=day")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT00:00:00Z")
    for entry in data.get("clones", []):
        if entry["timestamp"] == today:
            return {"count": entry["count"], "uniques": entry["uniques"]}
    return {"count": 0, "uniques": 0}


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
    data = pypi_get(f"/packages/{PYPI_PACKAGE}/recent")
    d = data.get("data", {})
    return {
        "last_day": d.get("last_day", 0),
        "last_week": d.get("last_week", 0),
        "last_month": d.get("last_month", 0),
    }


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

    history = load_history()
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
    )


if __name__ == "__main__":
    main()
