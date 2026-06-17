"""Find suspect labels for one tag via near-neighbor disagreement (model-independent).

This is the cold-start label-error signal for the dataset-refinement loop: it reuses
the CLIP image embeddings already stored in the vault (no tagger model needed) to find
images whose tag label disagrees with their visually-nearest neighbours. Because it does
not depend on the (noisy) tagger, it is trustworthy on day one and breaks the circularity
of "use the model to clean the data it was trained on".

Two directions are surfaced, so review is fast:
  * ADD    – image is NOT tagged, but its near neighbours mostly ARE  → likely a missing tag
             (the false-negative / rare-class recall hole).
  * REMOVE – image IS tagged, but its near neighbours mostly are NOT  → likely a wrong tag.

Each suspect also carries its single most-similar opposite-labelled neighbour ("nearest
twin"), so the reviewer can eyeball the concrete near-duplicate that triggered the flag.

Read-only: opens the vault in SQLite read-only mode and never writes to it. Output is a
ranked CSV of (image, tag) suspects.

Usage (from the repo root, using the project venv which has numpy):
    .venv/bin/python scripts/near_neighbor_label_disagreement.py --tag "malformed hand"

    .venv/bin/python scripts/near_neighbor_label_disagreement.py \
        --db vault.db --tag "malformed hand" --k 12 --out tmp/malformed_hand_suspects.csv
"""

import argparse
import csv
import datetime
import json
import os
import sqlite3
import sys
import time

import numpy as np

EMBEDDING_DIM = 512  # CLIP ViT-B-32 image_embedding blobs are 512 float32 = 2048 bytes.
EMBEDDING_BYTES = EMBEDDING_DIM * 4


def resolve_db(path: str | None) -> str:
    """Resolve the vault path, following the common ./vault.db symlink."""
    if path:
        return os.path.abspath(path)
    for candidate in ("vault.db", os.path.expanduser("~/Projects/pixlstash/vault.db")):
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
    sys.exit("ERROR: no --db given and no vault.db found in cwd.")


def resolve_project_id(conn, project: str | None, project_id: int | None) -> int | None:
    """Resolve a --project name or --project-id to a project id (None = whole vault)."""
    if project_id is not None:
        return project_id
    if project is None:
        return None
    row = conn.execute(
        "SELECT id FROM project WHERE name = ? COLLATE NOCASE", (project,)
    ).fetchone()
    if row is None:
        names = [r[0] for r in conn.execute("SELECT name FROM project ORDER BY id")]
        sys.exit(f"ERROR: no project named {project!r}. Known projects: {names}")
    return int(row[0])


def load_tag_merges(override_path: str | None):
    """Return ``(child -> parent merge map, source_label)``.

    Source of truth is PixlStash's editable ``DEFAULT_TAG_MERGES`` constant (so it can
    be loosened as the model improves). Pass ``--tag-remap`` to override with a JSON
    file — e.g. pixltagger's ``pixlstash.json``, whose ``tag_remap`` may also contain
    ``null`` ("drop") entries, which are ignored here since they don't merge into a parent.
    """
    if override_path:
        try:
            with open(override_path) as f:
                data = json.load(f)
        except Exception as e:
            sys.exit(f"ERROR reading --tag-remap {override_path}: {e}")
        raw = data.get("tag_remap", data) if isinstance(data, dict) else {}
        return {k: v for k, v in raw.items() if isinstance(v, str)}, override_path
    try:
        # Import PixlStash's constant. scripts/ lives at <repo>/scripts, so the repo
        # root (which contains the `pixlstash` package) is two levels up.
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from pixlstash.db_models.tag import DEFAULT_TAG_MERGES

        return dict(DEFAULT_TAG_MERGES), "pixlstash DEFAULT_TAG_MERGES"
    except Exception:
        return {}, None


def load_vault(
    db_path: str,
    tag: str,
    equiv_tags: set[str],
    project: str | None,
    project_id: int | None,
):
    """Load ids, paths, normalized embeddings, and two label masks for non-deleted pictures.

    Returns ``has_literal`` (the picture carries ``tag`` itself) and ``has_concept`` (it
    carries ``tag`` *or* any merge-equivalent child tag). ``has_concept`` drives neighbour
    voting and "missing" eligibility; ``has_literal`` drives "remove" eligibility (you can
    only remove a tag a picture actually has).

    Scoped to a single project when given, so the scan covers exactly the images used
    to train the model and not the rest of the vault. Opens read-only so a running
    PixlStash instance is never disturbed.
    """
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        pid = resolve_project_id(conn, project, project_id)
        where = "image_embedding IS NOT NULL AND deleted = 0"
        params: tuple = ()
        if pid is not None:
            where += " AND project_id = ?"
            params = (pid,)

        ids: list[int] = []
        paths: list[str] = []
        blobs: list[bytes] = []
        cur = conn.execute(
            f"SELECT id, file_path, image_embedding FROM picture WHERE {where}", params
        )
        for pic_id, file_path, blob in cur:
            if blob is None or len(blob) != EMBEDDING_BYTES:
                continue
            ids.append(pic_id)
            paths.append(file_path or "")
            blobs.append(blob)

        literal = {
            row[0]
            for row in conn.execute(
                "SELECT picture_id FROM tag WHERE tag = ?", (tag,)
            )
        }
        placeholders = ",".join("?" * len(equiv_tags))
        concept = {
            row[0]
            for row in conn.execute(
                f"SELECT picture_id FROM tag WHERE tag IN ({placeholders})",
                tuple(sorted(equiv_tags)),
            )
        }
    finally:
        conn.close()

    if not ids:
        sys.exit("ERROR: no embedded, non-deleted pictures found in the vault.")

    emb = np.frombuffer(b"".join(blobs), dtype=np.float32).reshape(len(ids), EMBEDDING_DIM)
    # Defensive renormalise: stored vectors are already unit-norm, but a missed
    # normalisation would silently corrupt cosine == dot.
    norms = np.linalg.norm(emb, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    emb = (emb / norms).astype(np.float32)

    id_arr = np.array(ids, dtype=np.int64)
    has_literal = np.array([pid in literal for pid in ids], dtype=bool)
    has_concept = np.array([pid in concept for pid in ids], dtype=bool)
    return id_arr, paths, emb, has_literal, has_concept


def knn_disagreement(
    emb: np.ndarray,
    has_tag: np.ndarray,
    k: int,
    block: int = 1024,
):
    """For every image, compute similarity-weighted positive fraction among its k
    nearest neighbours, plus the most-similar opposite-labelled neighbour.

    Returns arrays aligned to the input rows:
      pos_frac          – sim-weighted fraction of neighbours carrying the tag
      twin_idx          – row index of nearest opposite-labelled neighbour (-1 if none)
      twin_sim          – cosine similarity of that twin (0.0 if none)
    """
    n = emb.shape[0]
    pos_frac = np.zeros(n, dtype=np.float32)
    twin_idx = np.full(n, -1, dtype=np.int64)
    twin_sim = np.zeros(n, dtype=np.float32)
    y = has_tag.astype(np.float32)

    for start in range(0, n, block):
        end = min(start + block, n)
        sims = emb[start:end] @ emb.T  # (rows, n) cosine similarities
        # Exclude self-matches before ranking.
        for local, glob in enumerate(range(start, end)):
            sims[local, glob] = -np.inf

        # Top-k neighbours per row.
        top = np.argpartition(-sims, kth=k - 1, axis=1)[:, :k]  # (rows, k), unsorted
        for local in range(end - start):
            glob = start + local
            nbr = top[local]
            w = sims[local, nbr]
            w = np.clip(w, 0.0, None)  # ignore negative-sim neighbours in the vote
            wsum = w.sum()
            yn = y[nbr]
            pos_frac[glob] = float((w * yn).sum() / wsum) if wsum > 0 else 0.0

            # Nearest opposite-labelled neighbour ("twin").
            opp = nbr[has_tag[nbr] != has_tag[glob]]
            if opp.size:
                opp_sims = sims[local, opp]
                best = opp.size and int(opp[np.argmax(opp_sims)])
                twin_idx[glob] = best
                twin_sim[glob] = float(sims[local, best])

    return pos_frac, twin_idx, twin_sim


def write_suggestions(
    db_path: str, rows: list[dict], source: str, replace_tag: str | None = None
) -> int:
    """Upsert suspect rows into the tag_suggestion queue.

    Conflicts on (picture_id, tag, source) update the score/reason for rows still
    PENDING, but never resurrect a suggestion the user already ACCEPTED or DISMISSED.

    When ``replace_tag`` is given, all *PENDING* rows for that tag + source are deleted
    first, so a fresh scan fully rebuilds the tag's queue (purging stale suggestions, e.g.
    ones generated before merge-awareness). Already-reviewed rows are still preserved.

    Requires the tag_suggestion table (migration 0057). Returns rows written/updated.
    """
    conn = sqlite3.connect(db_path)
    try:
        has = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tag_suggestion'"
        ).fetchone()
        if not has:
            sys.exit("ERROR: tag_suggestion table missing — run migration 0057 first "
                     "(alembic upgrade head, or start PixlStash once).")
        if replace_tag is not None:
            conn.execute(
                "DELETE FROM tag_suggestion "
                "WHERE status='PENDING' AND source=? AND tag=?",
                (source, replace_tag),
            )
        now = datetime.datetime.utcnow().isoformat()
        payload = []
        for r in rows:
            # Always phrased in terms of "have the tag" (pos_neighbour_frac), so it
            # reads correctly for both directions: high -> add, low -> remove.
            reason = (
                f"near-twin {r['twin_picture_id']} (sim {r['twin_sim']:.3f}) disagrees; "
                f"{r['pos_neighbour_frac']:.0%} of nearest neighbours have the tag"
            )
            payload.append((
                r["picture_id"], r["tag"], r["direction"], source, r["score"], reason,
                r["twin_picture_id"] or None, r["twin_sim"], now,
            ))
        cur = conn.executemany(
            """
            INSERT INTO tag_suggestion
                (picture_id, tag, direction, source, score, reason,
                 twin_picture_id, twin_sim, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?)
            ON CONFLICT(picture_id, tag, source) DO UPDATE SET
                direction=excluded.direction,
                score=excluded.score,
                reason=excluded.reason,
                twin_picture_id=excluded.twin_picture_id,
                twin_sim=excluded.twin_sim,
                created_at=excluded.created_at
            WHERE tag_suggestion.status='PENDING'
            """,
            payload,
        )
        conn.commit()
        return len(payload)
    finally:
        conn.close()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Path to vault.db (default: ./vault.db).")
    ap.add_argument("--tag", required=True, help="Target tag to scan, e.g. 'malformed hand'.")
    ap.add_argument("--project", default="PixlTagger",
                    help="Scope to this PixlStash project name (default 'PixlTagger', the model's training data). Use '' for the whole vault.")
    ap.add_argument("--project-id", type=int, default=None,
                    help="Scope by numeric project id instead of name (overrides --project).")
    ap.add_argument("--k", type=int, default=12, help="Neighbours per image (default 12).")
    ap.add_argument("--add-threshold", type=float, default=0.55,
                    help="Untagged image flagged ADD when sim-weighted positive neighbour fraction >= this (default 0.55).")
    ap.add_argument("--remove-threshold", type=float, default=0.45,
                    help="Tagged image flagged REMOVE when positive neighbour fraction <= this (default 0.45).")
    ap.add_argument("--min-twin-sim", type=float, default=0.85,
                    help="Only keep suspects whose nearest opposite-labelled neighbour is at least this similar (default 0.85). Focuses on genuine near-twins.")
    ap.add_argument("--limit", type=int, default=0, help="Cap rows written (0 = all).")
    ap.add_argument("--out", default=None, help="Output CSV path (default tmp/<tag>_nn_suspects.csv).")
    ap.add_argument("--write-db", action="store_true",
                    help="Upsert suspects into the tag_suggestion review queue (preserves already-reviewed rows).")
    ap.add_argument("--source", default="near_neighbor",
                    help="Value stored in tag_suggestion.source when --write-db (default 'near_neighbor').")
    ap.add_argument("--tag-remap", default=None,
                    help="Override the tag-merge map with a JSON file (else PixlStash's "
                         "DEFAULT_TAG_MERGES constant is used). Accepts pixltagger's "
                         "pixlstash.json (reads its 'tag_remap') or a bare child->parent dict.")
    ap.add_argument("--replace", action="store_true",
                    help="With --write-db, first delete all PENDING suggestions for this "
                         "tag+source so the scan fully rebuilds the queue (purges stale "
                         "rows). Reviewed (accepted/dismissed) rows are kept.")
    args = ap.parse_args()

    merges, merge_src = load_tag_merges(args.tag_remap)
    # Tags that count as the target for voting + "missing" eligibility: the target
    # itself plus every child tag that merges into it.
    equiv = {args.tag} | {child for child, parent in merges.items() if parent == args.tag}

    db_path = resolve_db(args.db)
    project = args.project or None
    print(f"Vault:  {db_path}")
    print(f"Scope:  {('project ' + repr(project)) if (project or args.project_id) else 'whole vault'}"
          + (f" (id {args.project_id})" if args.project_id is not None else ""))
    print(f"Tag:    {args.tag!r}")
    merged_in = sorted(equiv - {args.tag})
    if merged_in:
        print(f"Merges: counting {merged_in} as {args.tag!r} too (source: {merge_src}).")
    elif merge_src:
        print(f"Merges: source {merge_src}; nothing merges into {args.tag!r}.")
    else:
        print("Merges: none loaded — using literal tags only.")

    t0 = time.time()
    ids, paths, emb, has_literal, has_concept = load_vault(
        db_path, args.tag, equiv, project, args.project_id
    )
    n = len(ids)
    n_literal = int(has_literal.sum())
    n_concept = int(has_concept.sum())
    extra = f" (+{n_concept - n_literal} via merges)" if n_concept != n_literal else ""
    print(f"Loaded  {n} embedded pictures, {n_literal} tagged {args.tag!r}, "
          f"{n_concept} incl. merges{extra} — base rate {n_concept / n:.1%} in {time.time() - t0:.1f}s")

    t1 = time.time()
    # Neighbour voting + twin selection use the merged concept.
    pos_frac, twin_idx, twin_sim = knn_disagreement(emb, has_concept, args.k)
    print(f"kNN ({args.k}) disagreement computed in {time.time() - t1:.1f}s")

    rows = []
    for i in range(n):
        # ADD eligibility uses the merged concept (don't suggest a tag the picture
        # already has via a merge); REMOVE eligibility uses the literal tag.
        if not has_concept[i] and pos_frac[i] >= args.add_threshold:
            direction, score = "add", float(pos_frac[i])
        elif has_literal[i] and pos_frac[i] <= args.remove_threshold:
            direction, score = "remove", float(1.0 - pos_frac[i])
        else:
            continue
        if twin_sim[i] < args.min_twin_sim:
            continue
        ti = int(twin_idx[i])
        rows.append({
            "picture_id": int(ids[i]),
            "tag": args.tag,
            "direction": direction,
            "score": round(score, 4),
            "pos_neighbour_frac": round(float(pos_frac[i]), 4),
            "twin_sim": round(float(twin_sim[i]), 4),
            "twin_picture_id": int(ids[ti]) if ti >= 0 else "",
            "file_path": paths[i],
            "twin_file_path": paths[ti] if ti >= 0 else "",
        })

    # Highest-disagreement, most-near-identical first.
    rows.sort(key=lambda r: (r["score"], r["twin_sim"]), reverse=True)
    if args.limit:
        rows = rows[: args.limit]

    n_add = sum(1 for r in rows if r["direction"] == "add")
    n_remove = sum(1 for r in rows if r["direction"] == "remove")
    print(f"Suspects: {len(rows)}  ({n_add} ADD / missing-tag, {n_remove} REMOVE / wrong-tag)")

    out_path = args.out or os.path.join("tmp", f"{args.tag.replace(' ', '_')}_nn_suspects.csv")
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    fields = ["picture_id", "tag", "direction", "score", "pos_neighbour_frac",
              "twin_sim", "twin_picture_id", "file_path", "twin_file_path"]
    with open(out_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    print(f"Wrote   {out_path}")

    if args.write_db:
        n_written = write_suggestions(
            db_path, rows, args.source, replace_tag=args.tag if args.replace else None
        )
        print(f"Queued  {n_written} rows into tag_suggestion (source={args.source!r}, "
              f"already-reviewed rows preserved)")

    print("\nTop 15 suspects:")
    print(f"{'dir':6} {'score':>6} {'twinSim':>7} {'pic':>7} {'twin':>7}  file")
    for r in rows[:15]:
        print(f"{r['direction']:6} {r['score']:6.3f} {r['twin_sim']:7.3f} "
              f"{r['picture_id']:>7} {str(r['twin_picture_id']):>7}  {os.path.basename(r['file_path'])}")


if __name__ == "__main__":
    main()
