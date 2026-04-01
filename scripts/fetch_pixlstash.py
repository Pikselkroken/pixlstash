#!/usr/bin/env python3
"""
fetch_pixlstash.py — Download training/evaluation picture sets from a PixlStash server.

Reads configuration from (in order of precedence):
  1. CLI arguments
  2. pixlstash.json in the current directory or repo root
  3. Environment variables: PIXLSTASH_URL, PIXLSTASH_TOKEN
  4. train_config.json keys prefixed with pixlstash_ (for non-secret fields)

Output structure:
  {cache_dir}/train/   — training set pictures + .txt tag files
  {cache_dir}/eval/    — evaluation set pictures + .txt tag files

Files already present in the cache are skipped unless --force is used.

Example:
  python scripts/fetch_pixlstash.py \\
    --url http://localhost:7860 \\
    --token MY_API_TOKEN \\
    --project "My Project" \\
    --train-set "Training Set" \\
    --eval-set "Evaluation Set"

Alternatively, create a pixlstash.json (gitignored) in the repo root:
  {
    "url": "http://localhost:7860",
    "token": "MY_API_TOKEN",
    "project": "My Project",
    "train_set": "Training Set",
    "eval_set": "Evaluation Set",
    "cache_dir": "./pixlstash_cache"
  }
"""

import argparse
import concurrent.futures
import http.cookiejar
import json
import os
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Dict, List, Optional, Tuple

TRAIN_SUBDIR = "train"
EVAL_SUBDIR = "eval"
MANIFEST_FILE = ".cache_manifest.json"
MAX_WORKERS = 8
BULK_TAG_BATCH = 200

# Normalise a raw format/MIME string to the extension string used by the API URL.
# The API requires the extension to match the stored format exactly, so we must
# NOT convert jpeg→jpg. We only strip MIME prefixes like "image/".
_EXT_NORM = {
    "jpeg": "jpeg",
    "jpg": "jpg",
    "png": "png",
    "webp": "webp",
    "gif": "gif",
    "image/jpeg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
}


def _find_config_file(filename: str) -> Optional[str]:
    candidates = [
        os.path.join(os.getcwd(), filename),
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))), filename
        ),
    ]
    for p in candidates:
        if os.path.isfile(p):
            return p
    return None


def _load_pixlstash_config() -> dict:
    path = _find_config_file("pixlstash.json")
    if path:
        with open(path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        print(f"[INFO] Loaded pixlstash.json from {path}")
        return cfg
    return {}


def _load_train_config() -> dict:
    path = _find_config_file("train_config.json")
    if path:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


class PixlStashClient:
    def __init__(self, base_url: str, token: str, verify_ssl: bool = True):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self._ssl_ctx = None if verify_ssl else ssl._create_unverified_context()
        self._opener = self._build_opener()

    def _build_opener(self) -> urllib.request.OpenerDirector:
        cookie_jar = http.cookiejar.CookieJar()
        https_handler = urllib.request.HTTPSHandler(context=self._ssl_ctx)
        cookie_handler = urllib.request.HTTPCookieProcessor(cookie_jar)
        return urllib.request.build_opener(https_handler, cookie_handler)

    def login(self) -> None:
        """Authenticate with the server using the API token and store the session cookie."""
        url = self.base_url + "/api/v1/login"
        data = json.dumps({"token": self.token}).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(req, timeout=30) as resp:
                resp.read()
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError(f"Login failed (HTTP {e.code}): {body}") from e
        print("[INFO] Authenticated with PixlStash.")

    def _request(
        self,
        method: str,
        path: str,
        params: Optional[dict] = None,
        payload: Optional[dict] = None,
    ) -> object:
        url = self.base_url + path
        if params:
            url += "?" + urllib.parse.urlencode(params, doseq=True)
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        if payload is not None:
            req.add_header("Content-Type", "application/json")
        try:
            with self._opener.open(req, timeout=30) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            body = e.read().decode(errors="replace")
            raise RuntimeError(
                f"HTTP {e.code} {e.reason} for {url}: {body}"
            ) from e

    def get_project_by_name(self, name: str) -> dict:
        return self._request(
            "GET",
            f"/api/v1/projects/{urllib.parse.quote(name, safe='')}",
        )

    def get_picture_sets(self, project_name: Optional[str] = None) -> list:
        if project_name is not None:
            return self._request("GET", f"/api/v1/projects/{urllib.parse.quote(project_name, safe='')}/picture_sets")
        return self._request("GET", "/api/v1/picture_sets")

    def get_set_members(self, set_id: int) -> list:
        return self._request("GET", f"/api/v1/picture_sets/{set_id}/members")

    def get_picture_metadata(self, picture_id: int) -> dict:
        return self._request("GET", f"/api/v1/pictures/{picture_id}/metadata")

    def bulk_fetch_tags(self, picture_ids: List[int]) -> Dict[str, list]:
        """Returns {str(picture_id): [tag_objects_or_strings]}. Batches 200 at a time."""
        result: Dict[str, list] = {}
        for i in range(0, len(picture_ids), BULK_TAG_BATCH):
            batch = picture_ids[i : i + BULK_TAG_BATCH]
            data = self._request(
                "POST", "/api/v1/pictures/tags/bulk_fetch", payload={"ids": batch}
            )
            if isinstance(data, dict):
                result.update(data)
        return result

    def download_picture(self, picture_id: int, ext: str, dest_path: str) -> None:
        url = f"{self.base_url}/api/v1/pictures/{picture_id}.{ext}"
        req = urllib.request.Request(url)
        with self._opener.open(req, timeout=120) as resp:
            with open(dest_path, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)


def _find_sets_by_prefix(sets: list, prefix: str) -> List[str]:
    """Return names of all sets whose name starts with prefix (case-insensitive)."""
    p = prefix.lower()
    return [s["name"] for s in sets if s.get("name", "").lower().startswith(p)]


def _find_set_by_name(sets: list, name: str) -> Optional[dict]:
    for s in sets:
        if s.get("name") == name:
            return s
    return None


def _normalize_ext(fmt: str) -> Optional[str]:
    return _EXT_NORM.get(fmt.lower().lstrip("."))


def _tag_names(raw_tags: list) -> List[str]:
    """Extract tag name strings from whatever the API returns (objects or plain strings)."""
    names = []
    for t in raw_tags:
        if isinstance(t, dict):
            # API returns {"id": ..., "tag": "..."}
            names.append(t.get("tag") or t.get("name", ""))
        else:
            names.append(str(t))
    return [n for n in names if n]


def _write_tags_file(tags: List[str], path: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(", ".join(tags))


def _load_manifest(cache_dir: str) -> dict:
    path = os.path.join(cache_dir, MANIFEST_FILE)
    if os.path.isfile(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_manifest(cache_dir: str, manifest: dict) -> None:
    path = os.path.join(cache_dir, MANIFEST_FILE)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)


def download_sets(
    client: PixlStashClient,
    set_names: List[str],
    sets_all: list,
    project_id: Optional[int],
    subdir: str,
    cache_dir: str,
    manifest: dict,
    force: bool = False,
) -> int:
    """Download pictures from one or more named picture sets into subdir.

    All sets are merged into the same output directory. Per-set membership is
    tracked in the manifest so that pruning removes pictures only when they
    have been removed from ALL contributing sets.

    Returns count of newly downloaded files.
    """
    # Resolve set names → member picture IDs
    set_membership: Dict[str, List[int]] = {}
    for set_name in set_names:
        target = _find_set_by_name(sets_all, set_name)
        if target is None:
            available = [s.get("name", "?") for s in sets_all]
            raise SystemExit(
                f"Picture set '{set_name}' not found in project.\n"
                f"Available sets: {available}"
            )
        set_id = target["id"]
        print(f"[INFO] Found set '{set_name}' (id={set_id}), fetching members...")
        members_raw = client.get_set_members(set_id)
        if isinstance(members_raw, dict):
            members_raw = members_raw.get("picture_ids", [])
        pids = [int(m) for m in (members_raw or [])]
        if not pids:
            print(f"[WARNING] Picture set '{set_name}' has no members.")
        set_membership[set_name] = pids

    # Union of all member IDs (deduped, insertion-ordered)
    seen: Dict[int, None] = {}
    for pids in set_membership.values():
        for pid in pids:
            seen[pid] = None
    picture_ids = list(seen.keys())

    if not picture_ids:
        print(f"[WARNING] No pictures found across sets: {set_names}")
        return 0

    out_dir = os.path.join(cache_dir, subdir)
    os.makedirs(out_dir, exist_ok=True)
    set_manifest: dict = manifest.setdefault(subdir, {})

    # Update per-set membership tracking in the manifest
    tracked: dict = set_manifest.setdefault("_sets", {})
    # Remove sets that are no longer being fetched (e.g. removed from project)
    defunct_sets = [name for name in list(tracked.keys()) if name not in set_membership]
    for name in defunct_sets:
        print(f"[INFO] Set '{name}' is no longer tracked; removing from manifest.")
        del tracked[name]
    tracked.update({name: pids for name, pids in set_membership.items()})

    # A picture is stale only if it has been removed from ALL tracked sets
    all_tracked_ids = {
        str(pid)
        for pids in tracked.values()
        for pid in pids
    }
    stale_keys = [
        k for k in list(set_manifest.keys())
        if k != "_sets" and k not in all_tracked_ids
    ]
    if stale_keys:
        # Report which sets the stale pictures came from (for transparency)
        stale_set = set(stale_keys)
        removed_from: dict = {}
        for set_name, pids in tracked.items():
            gone = [str(p) for p in pids if str(p) in stale_set]
            if gone:
                removed_from[set_name] = len(gone)
        if removed_from:
            details = ", ".join(f"{n} from '{s}'" for s, n in removed_from.items())
            print(f"[INFO] Removing {len(stale_keys)} pictures no longer in any tracked set ({details})...")
        else:
            print(f"[INFO] Removing {len(stale_keys)} pictures no longer in any tracked set...")
        for key in stale_keys:
            ext = set_manifest[key].get("ext", "")
            for suffix in ([f".{ext}", ".txt"] if ext else [".txt"]):
                fpath = os.path.join(out_dir, f"{key}{suffix}")
                if os.path.isfile(fpath):
                    os.remove(fpath)
            del set_manifest[key]

    # Determine which pictures need metadata (not yet in manifest or forced)
    need_meta = [
        pid for pid in picture_ids
        if force or str(pid) not in set_manifest
    ]
    print(
        f"[INFO] Set has {len(picture_ids)} pictures; "
        f"{len(need_meta)} need metadata fetch."
    )

    # Fetch metadata in parallel to get file format
    meta_map: Dict[int, dict] = {}
    if need_meta:
        def _fetch_meta(pid: int) -> Tuple[int, dict]:
            return pid, client.get_picture_metadata(pid)

        with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {pool.submit(_fetch_meta, pid): pid for pid in need_meta}
            done = 0
            for fut in concurrent.futures.as_completed(futs):
                pid, meta = fut.result()
                meta_map[pid] = meta
                done += 1
                if done % 50 == 0 or done == len(need_meta):
                    print(f"[INFO]   Metadata: {done}/{len(need_meta)}", end="\r")
        print()

    # Build download list: skip pictures already on disk (unless forced)
    to_download: List[Tuple[int, str]] = []
    for pid in picture_ids:
        key = str(pid)
        if not force and key in set_manifest:
            ext = set_manifest[key].get("ext", "")
            if ext and os.path.isfile(os.path.join(out_dir, f"{pid}.{ext}")):
                continue

        meta = meta_map.get(pid)
        if meta is None:
            continue
        raw_fmt = meta.get("format", "")
        ext = _normalize_ext(raw_fmt) if raw_fmt else None
        if not ext:
            print(f"[WARNING] Unrecognised format '{raw_fmt}' for picture {pid}, skipping.")
            continue
        to_download.append((pid, ext))

    print(f"[INFO] {len(to_download)} pictures to download.")
    if not to_download:
        return 0

    # Tags are already present in the metadata we fetched — no extra API call needed.
    tags_map: Dict[int, list] = {
        pid: meta_map[pid].get("tags", [])
        for pid, _ in to_download
        if pid in meta_map
    }

    # Download images + write tag files in parallel
    downloaded = 0
    errors = 0

    def _download_one(pid_ext: Tuple[int, str]) -> Tuple[int, str]:
        pid, ext = pid_ext
        img_path = os.path.join(out_dir, f"{pid}.{ext}")
        txt_path = os.path.join(out_dir, f"{pid}.txt")

        client.download_picture(pid, ext, img_path)
        # Only write the tags file after the image download succeeds
        raw_tags = tags_map.get(pid) or []
        _write_tags_file(_tag_names(raw_tags), txt_path)
        return pid, ext

    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futs = {pool.submit(_download_one, item): item for item in to_download}
        for fut in concurrent.futures.as_completed(futs):
            pid, _ = futs[fut]
            try:
                done_pid, ext = fut.result()
                set_manifest[str(done_pid)] = {"ext": ext}
                downloaded += 1
                if downloaded % 20 == 0 or downloaded == len(to_download):
                    print(f"[INFO]   Downloaded {downloaded}/{len(to_download)}", end="\r")
            except Exception as exc:
                errors += 1
                print(f"\n[ERROR] Failed to download picture {pid}: {exc}")

    print()
    if errors:
        print(f"[WARNING] {errors} downloads failed.")
    print(f"[INFO] Done: {downloaded} new files in {out_dir}/")
    return downloaded


def download_set(
    client: PixlStashClient,
    set_name: str,
    sets_all: list,
    project_id: Optional[int],
    subdir: str,
    cache_dir: str,
    manifest: dict,
    force: bool = False,
) -> int:
    """Convenience wrapper: download a single named set."""
    return download_sets(
        client, [set_name], sets_all, project_id, subdir, cache_dir, manifest, force
    )


def main() -> None:
    px_cfg = _load_pixlstash_config()
    tr_cfg = _load_train_config()

    parser = argparse.ArgumentParser(
        description="Download PixlStash picture sets for training/evaluation.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--url",
        default=None,
        help="PixlStash server base URL (or env PIXLSTASH_URL or pixlstash.json)",
    )
    parser.add_argument(
        "--token",
        default=None,
        help="API token (or env PIXLSTASH_TOKEN or pixlstash.json)",
    )
    parser.add_argument(
        "--project",
        default=None,
        help="PixlStash project name to scope picture set lookup",
    )
    parser.add_argument(
        "--train-set",
        default=None,
        help="Picture set name to use as training data",
    )
    parser.add_argument(
        "--eval-set",
        default=None,
        help="Picture set name to use as evaluation data",
    )
    parser.add_argument(
        "--cache-dir",
        default=None,
        help="Local directory for cached images and tags (default: ./pixlstash_cache)",
    )
    parser.add_argument(
        "--no-verify-ssl",
        action="store_true",
        default=False,
        help="Disable SSL certificate verification (for self-signed certs)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download all files, ignoring existing cache",
    )
    parser.add_argument(
        "--extra-prefix",
        action="append",
        dest="extra_prefixes",
        default=None,
        metavar="PREFIX",
        help="Auto-include all picture sets whose name starts with PREFIX as extra training "
             "data. Repeat to add multiple prefixes (e.g. --extra-prefix anomaly --extra-prefix "
             "uncertainty). Can also be set via pixlstash.json key 'extra_prefixes' (list). "
             "The legacy key 'uncertainty_prefix' is still supported as a single-prefix fallback.",
    )
    parser.add_argument(
        "--train-only",
        action="store_true",
        help="Only download the training set",
    )
    parser.add_argument(
        "--eval-only",
        action="store_true",
        help="Only download the evaluation set",
    )

    # Build defaults: pixlstash.json > train_config.json (pixlstash_* keys) > env vars
    defaults: dict = {}
    for key in ("url", "token", "project", "train_set", "eval_set", "cache_dir", "no_verify_ssl"):
        val = px_cfg.get(key) or tr_cfg.get(f"pixlstash_{key}")
        if val:
            defaults[key] = val
    # extra_prefixes: prefer new key, fall back to legacy uncertainty_prefix
    cfg_prefixes = px_cfg.get("extra_prefixes") or tr_cfg.get("pixlstash_extra_prefixes")
    if not cfg_prefixes:
        legacy = px_cfg.get("uncertainty_prefix") or tr_cfg.get("pixlstash_uncertainty_prefix")
        if legacy:
            cfg_prefixes = [legacy]
    if cfg_prefixes:
        defaults["extra_prefixes"] = list(cfg_prefixes)
    # Environment variables override config files for sensitive values
    if os.environ.get("PIXLSTASH_URL"):
        defaults["url"] = os.environ["PIXLSTASH_URL"]
    if os.environ.get("PIXLSTASH_TOKEN"):
        defaults["token"] = os.environ["PIXLSTASH_TOKEN"]

    parser.set_defaults(**defaults)
    args = parser.parse_args()

    if not args.url:
        parser.error(
            "--url is required. Set it via CLI, PIXLSTASH_URL env var, or pixlstash.json."
        )
    if not args.token:
        parser.error(
            "--token is required. Set it via CLI, PIXLSTASH_TOKEN env var, or pixlstash.json."
        )
    if not args.eval_only and not args.train_set:
        parser.error("--train-set is required (or use --eval-only).")
    if not args.train_only and not args.eval_set:
        parser.error("--eval-set is required (or use --train-only).")

    cache_dir = args.cache_dir or "./pixlstash_cache"
    os.makedirs(cache_dir, exist_ok=True)

    verify_ssl = not args.no_verify_ssl
    if not verify_ssl:
        print("[WARNING] SSL certificate verification is disabled.")
    client = PixlStashClient(args.url, args.token, verify_ssl=verify_ssl)
    client.login()

    # Resolve project to an ID (optional — scopes picture set lookup)
    project_id: Optional[int] = None
    if args.project:
        print(f"[INFO] Resolving project '{args.project}'...")
        project = client.get_project_by_name(args.project)
        project_id = project["id"]
        print(f"[INFO] Project '{args.project}' → id={project_id}")

    manifest = _load_manifest(cache_dir)

    if not args.eval_only:
        # Fetch the full set list once so we can both discover uncertainty sets
        # and pass it to download_sets without a redundant API call.
        print(f"[INFO] Fetching picture set list...")
        sets_all = client.get_picture_sets(project_name=args.project)

        train_sets = [args.train_set]
        extra_prefixes = args.extra_prefixes or []
        for prefix in extra_prefixes:
            extra = _find_sets_by_prefix(sets_all, prefix)
            extra = [s for s in extra if s not in train_sets]
            if extra:
                print(f"[INFO] Auto-including {len(extra)} set(s) matching prefix '{prefix}': {extra}")
            train_sets.extend(extra)

        print(f"\n=== Training sets: {train_sets} ===")
        download_sets(
            client, train_sets, sets_all, project_id, TRAIN_SUBDIR, cache_dir, manifest, args.force
        )
        _save_manifest(cache_dir, manifest)
    else:
        sets_all = None  # not needed for eval-only

    if not args.train_only:
        if sets_all is None:
            print(f"[INFO] Fetching picture set list...")
            sets_all = client.get_picture_sets(project_name=args.project)
        print(f"\n=== Evaluation set: '{args.eval_set}' ===")
        download_set(
            client, args.eval_set, sets_all, project_id, EVAL_SUBDIR, cache_dir, manifest, args.force
        )
        _save_manifest(cache_dir, manifest)

    abs_cache = os.path.abspath(cache_dir)
    print(f"\n[INFO] Cache ready at: {abs_cache}")
    print(f"\nTo train with these sets, pass to finetune.py:")
    print(
        f"  --data {os.path.join(abs_cache, TRAIN_SUBDIR)} "
        f"--eval-folder {os.path.join(abs_cache, EVAL_SUBDIR)}"
    )


if __name__ == "__main__":
    main()
