"""Idea 5: put each tag's list in front of a person, then count what they confirmed.

    PIXLSTASH_TOKEN=<owner token> python review.py create LISTS_DIR \
        [--tags ...] [--control-share 0.1]
    PIXLSTASH_TOKEN=<owner token> python review.py score LISTS_DIR EVIDENCE_DIR [--tags ...]

create  POST /reviews with the tag's shuffled list as ``picture_ids`` (a list review,
        #2676) and ``control_share`` known-answer pictures mixed in unmarked. Prints the
        review id; the person judges it in PixlStash's Reviews screen.
score   reads the tag's human verdicts (GET /tag_predictions/labels) and each review's
        receipt (GET /reviews, ``receipt.controls``), then writes EVIDENCE_DIR/confirms_<tags>.csv:
        per tag and method column in the list (random too, in a short list),
        how many of its pictures were confirmed, rejected or skipped, and
        how many confirmed pictures are near-twins (cosine >= 0.95) of a training picture.
"""

import argparse
import csv
import json
import os
import ssl
import sys
import urllib.parse
import urllib.request

from build_lists import DEFAULT_TAGS, TWIN, write_csv


def api(url, token):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False  # the desktop server's certificate is self-signed
    ctx.verify_mode = ssl.CERT_NONE
    base = url.rstrip("/") + "/api/v1"

    def call(path, body=None, **params):
        q = ("?" + urllib.parse.urlencode(params)) if params else ""
        req = urllib.request.Request(
            base + path + q,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, context=ctx, timeout=120) as r:
            return json.loads(r.read())

    return call


def read_list(lists_dir, tag):
    with open(os.path.join(lists_dir, tag.replace(" ", "_") + ".csv")) as f:
        return list(csv.DictReader(f))


def create(call, lists_dir, tags, control_share):
    for tag in tags:
        rows = read_list(lists_dir, tag)
        r = call(
            "/reviews",
            {
                "tag": tag,
                "picture_ids": [int(x["picture_id"]) for x in rows],
                "control_share": control_share,
            },
        )
        print(f"{tag}: review {r.get('id')} with {len(rows)} listed pictures", file=sys.stderr)


def verdicts_for(call, tag):
    out, offset = {}, 0
    while True:
        page = call(
            "/tag_predictions/labels", tag=tag, label_source="human", offset=offset, limit=5000
        )
        for r in page["labels"]:
            out[int(r["picture_id"])] = r["label_state"]
        offset += len(page["labels"])
        if not page["labels"] or offset >= page["total"]:
            return out


def score(call, lists_dir, evidence_dir, tags):
    reviews = {}
    for r in call("/reviews"):  # newest first: keep each tag's latest list review
        if r.get("source") == "supplied_list":
            reviews.setdefault(r["tag"], r)
    rows = []
    for tag in tags:
        listed = read_list(lists_dir, tag)
        got = verdicts_for(call, tag)
        rid = (reviews.get(tag) or {}).get("id")
        controls = call(f"/reviews/{rid}").get("receipt", {}).get("controls") if rid else None
        methods = [c for c in listed[0] if c not in ("picture_id", "cos_to_nearest_train")]
        for m in methods:
            mine = [x for x in listed if x[m] == "1"]
            states = [got.get(int(x["picture_id"])) for x in mine]
            pos = [x for x, s in zip(mine, states) if s == "POS"]
            rows.append(
                {
                    "tag": tag,
                    "method": m,
                    "listed": len(mine),
                    "confirmed": len(pos),
                    "rejected": states.count("NEG"),
                    "no_verdict": sum(s is None for s in states),
                    "confirmed_near_twins": sum(
                        float(x["cos_to_nearest_train"]) >= TWIN for x in pos
                    ),
                    "controls": json.dumps(controls) if controls else "",
                }
            )
    write_csv(os.path.join(evidence_dir, f"confirms_{'_'.join(tags).replace(' ', '_')}.csv"), rows)
    for r in rows:
        print(r, file=sys.stderr)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("create", "score"))
    ap.add_argument("lists_dir")
    ap.add_argument("evidence_dir", nargs="?")
    ap.add_argument("--tags", default=DEFAULT_TAGS)
    ap.add_argument("--control-share", type=float, default=0.1)
    ap.add_argument("--url", default="https://127.0.0.1:9537")
    a = ap.parse_args()
    call = api(a.url, os.environ["PIXLSTASH_TOKEN"].strip())
    tags = a.tags.split(",")
    if a.cmd == "create":
        create(call, a.lists_dir, tags, a.control_share)
    else:
        score(call, a.lists_dir, a.evidence_dir, tags)


if __name__ == "__main__":
    main()
