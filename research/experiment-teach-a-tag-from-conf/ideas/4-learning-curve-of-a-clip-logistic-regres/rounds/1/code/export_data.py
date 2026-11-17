"""Export human tag verdicts and CLIP picture embeddings through the PixlStash REST API.

Run by the library owner (the labels route is owner-only); it reads nothing but the API.

    PIXLSTASH_TOKEN=<owner token> python export_data.py OUT_DIR \
        [--url https://127.0.0.1:9537] [--background 0|N|all]

Writes OUT_DIR/labels.csv (picture_id, tag, label_state) and OUT_DIR/embeddings.npz
(ids int64, emb float32 (n, 512)). --background adds that many unjudged library pictures
(default all) so the near-neighbour vote sees the same pool it sees in the app. Keep
OUT_DIR outside the repository: it is the owner's data, not evidence.
"""

import argparse
import base64
import csv
import json
import os
import random
import ssl
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

EMB_BYTES = 512 * 4


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("--url", default="https://127.0.0.1:9537")
    ap.add_argument("--background", default="all")
    ap.add_argument("--threads", type=int, default=16)
    args = ap.parse_args()
    token = os.environ["PIXLSTASH_TOKEN"].strip()
    ctx = ssl.create_default_context()
    ctx.check_hostname = False  # the desktop server's certificate is self-signed
    ctx.verify_mode = ssl.CERT_NONE
    base = args.url.rstrip("/") + "/api/v1"

    def get(path, **params):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        req = urllib.request.Request(
            base + path + q, headers={"Authorization": f"Bearer {token}"}
        )
        with urllib.request.urlopen(req, context=ctx, timeout=60) as r:
            return json.loads(r.read())

    labels, offset = [], 0
    while True:
        page = get("/tag_predictions/labels", label_source="human", offset=offset, limit=5000)
        labels += page["labels"]
        offset += len(page["labels"])
        if not page["labels"] or offset >= page["total"]:
            break
    print(f"{len(labels)} human verdicts", file=sys.stderr)

    ids = {int(r["picture_id"]) for r in labels}
    if args.background != "0":
        all_ids, offset = [], 0
        while True:
            page = get("/pictures", fields="id", offset=offset, limit=1000)
            rows = page if isinstance(page, list) else page.get("pictures", page.get("items", []))
            all_ids += [int(r["id"]) for r in rows]
            offset += len(rows)
            if not rows:  # not `< limit`: the server may cap the page size
                break
        extra = sorted(set(all_ids) - ids)
        if args.background != "all":
            random.Random(0).shuffle(extra)
            extra = extra[: int(args.background)]
        ids |= set(extra)
        print(f"{len(extra)} background pictures", file=sys.stderr)

    def fetch(pid):
        try:
            b = base64.b64decode(get(f"/pictures/{pid}/image_embedding")["image_embedding"] or b"")
        except Exception as exc:  # a missing embedding is skipped, and counted below
            print(f"picture {pid}: {exc}", file=sys.stderr)
            return pid, None
        return pid, (np.frombuffer(b, np.float32) if len(b) == EMB_BYTES else None)

    with ThreadPoolExecutor(args.threads) as ex:
        got = [(p, e) for p, e in ex.map(fetch, sorted(ids)) if e is not None]
    print(f"{len(got)} of {len(ids)} pictures have an embedding", file=sys.stderr)

    os.makedirs(args.out_dir, exist_ok=True)
    with open(os.path.join(args.out_dir, "labels.csv"), "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["picture_id", "tag", "label_state"])
        for r in labels:
            w.writerow([r["picture_id"], r["tag"], r["label_state"]])
    np.savez_compressed(
        os.path.join(args.out_dir, "embeddings.npz"),
        ids=np.array([p for p, _ in got], np.int64),
        emb=np.stack([e for _, e in got]).astype(np.float32),
    )


if __name__ == "__main__":
    main()
