"""Preview core rule v2 on a hub before data step 8 rewrites it.

Opens the hub READ-ONLY (a ``mode=ro`` URI: nothing is written, not even a
WAL checkpoint) and, for every topology cached under core rule v1, derives
its v2 core from the stored documents exactly as data step 8 will
(``hub/workflow_group_convert.rederive_cores``). Prints how many automatic
workflows there are before and after, every group of v1 workflows that would
combine, which node classes the prune removed, which graphs the sampler guard
kept whole, and asserts the rule is many-to-one: no v1 workflow splits.

The output names the owner's workflows and models. It is for the owner; quote
only its counts anywhere public.

Usage::

    python scripts/workflow_core_dry_run.py [--hub PATH]

``--hub`` defaults to the hub this machine's server opens.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from types import SimpleNamespace

from pixlstash.hub.db import default_hub_path
from pixlstash.hub.workflow_group_convert import (
    _CORE_RULE_V1,
    _Reader,
    _core_strip_v1,
    card_document,
)
from pixlstash.services.workflow_hash import graph_key
from pixlstash.services.workflow_identity import _core_v2, special_groups


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hub", default=default_hub_path())
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.hub}?mode=ro", uri=True)
    hub = _Reader(conn)

    rows = hub.fetchall(
        "SELECT topology_hash, core_hash, workflow_type FROM workflow_topology_core "
        "WHERE core_version = ? ORDER BY topology_hash",
        (_CORE_RULE_V1,),
    )
    variants: dict[str, list[str]] = {}
    for row in hub.fetchall(
        "SELECT topology_hash, structural_hash FROM workflow_variant "
        "ORDER BY structural_hash"
    ):
        variants.setdefault(row["topology_hash"], []).append(row["structural_hash"])

    print(f"Hub: {args.hub}")
    print(f"Topologies on {_CORE_RULE_V1}: {len(rows)}")
    new_of_old: dict[str, set[str]] = {}
    topologies_of_new: dict[str, list[str]] = {}
    pruned_total: Counter = Counter()
    refused, unreadable, drifted = [], [], []
    type_of: dict[str, str] = {}
    old_of: dict[str, str] = {}
    specials_of: dict[str, tuple] = {}
    for row in rows:
        topology = row["topology_hash"]
        card = SimpleNamespace(
            workflow_key=topology,
            topology_hash=topology,
            variants=variants.get(topology, []),
        )
        found = card_document(hub, card)
        if found is None:
            unreadable.append(topology)
            continue
        v1 = _core_strip_v1(found[1])
        if graph_key(v1) != row["core_hash"]:
            drifted.append(topology)
        core, pruned, was_refused = _core_v2(v1)
        pruned_total.update(pruned)
        if was_refused:
            refused.append(topology)
        new = graph_key(core)
        new_of_old.setdefault(row["core_hash"], set()).add(new)
        topologies_of_new.setdefault(new, []).append(topology)
        type_of[topology] = row["workflow_type"] or "untyped"
        old_of[topology] = row["core_hash"]
        specials_of[topology] = special_groups(found[1])

    olds_of_new: dict[str, set[str]] = {}
    for old, news in new_of_old.items():
        for new in news:
            olds_of_new.setdefault(new, set()).add(old)
    split = {old: news for old, news in new_of_old.items() if len(news) > 1}
    combined = {new: olds for new, olds in olds_of_new.items() if len(olds) > 1}

    print(f"Automatic workflows before: {len(new_of_old)}")
    print(f"Automatic workflows after:  {len(olds_of_new)}")
    print(f"Groups that combine: {len(combined)}")
    print(
        "Largest combine: "
        f"{max((len(olds) for olds in combined.values()), default=0)} workflows"
    )
    print(f"Sampler guard kept whole: {len(refused)} topologies")
    print(f"No readable document (stay on v1): {len(unreadable)} topologies")
    print(f"Stored v1 hash not re-derived: {len(drifted)} topologies")
    print("\nPruned classes (node count over all topologies):")
    for cls, n in pruned_total.most_common():
        print(f"  {n:5d}  {cls}")

    names = _names(hub)
    print("\nWorkflows that combine:")
    for new, olds in sorted(combined.items(), key=lambda kv: -len(kv[1])):
        print(f"\n  auto:{new[:12]}… <- {len(olds)} workflows")
        for old in sorted(olds):
            tops = [t for t in topologies_of_new[new] if old_of[t] == old]
            count = sum(len(variants.get(t, [])) for t in tops)
            kinds = sorted(
                {
                    f"{type_of[t]}{'+' + '+'.join(specials_of[t]) if specials_of[t] else ''}"
                    for t in tops
                }
            )
            print(
                f"    auto:{old[:12]}…  {names.get(old) or '(unnamed)'}  "
                f"[{', '.join(kinds)}; {len(tops)} topologies, {count} variants]"
            )
    if refused:
        print("\nTopologies the sampler guard kept whole:")
        for topology in refused:
            print(f"  {topology}")

    print("\nMany-to-one: ", end="")
    if split:
        print(f"VIOLATED by {len(split)} v1 workflows: {json.dumps(sorted(split))}")
        return 1
    print("holds (no v1 workflow splits).")
    return 0


def _names(hub) -> dict[str, str]:
    """``{v1 core: a readable name}``: the owner's, else the cards', else a model."""
    names: dict[str, str] = {}
    for row in hub.fetchall(
        "SELECT workflow_id, name FROM workflow_group_attr WHERE name IS NOT NULL"
    ):
        if row["workflow_id"].startswith("auto:"):
            names[row["workflow_id"][len("auto:") :]] = row["name"]
    for row in hub.fetchall(
        "SELECT c.core_hash AS core, a.name AS name, "
        "MIN(ra.normalized_filename) AS model "
        "FROM workflow_topology_core c "
        "JOIN workflow_variant v ON v.topology_hash = c.topology_hash "
        "LEFT JOIN workflow_attr a ON a.workflow_key = v.workflow_key "
        "LEFT JOIN workflow_recipe_asset ra ON ra.structural_hash = v.structural_hash "
        "AND ra.normalized_filename NOT LIKE '%lora%' "
        "GROUP BY c.core_hash, a.name"
    ):
        names.setdefault(row["core"], row["name"] or f"model {row['model']}")
    return names


if __name__ == "__main__":
    sys.exit(main())
