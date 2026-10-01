"""Preview core rule v2 on a hub before data step 8 rewrites it.

Opens the hub READ-ONLY (a ``mode=ro`` URI: nothing is written, not even a
WAL checkpoint) and, for every topology cached under core rule v1, derives
what data step 8 will (``hub/workflow_group_convert.rederive_cores``): the v2
core, and per card its base-model families, which together are its v2
workflow. Prints how many automatic workflows there are before and after,
every group of v1 workflows that combine, every v1 workflow that splits and
why (base-model family, or a graph with no sampler keeping its stages), which
node classes the prune removed and which graphs the sampler guard kept whole.
Exits non-zero if a workflow splits for any other reason.

The output names the owner's workflows and models. It is for the owner; quote
only its counts anywhere public.

Usage::

    python scripts/workflow_core_dry_run.py [--hub PATH]

``--hub`` defaults to the hub this machine's server opens.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from collections import Counter
from types import SimpleNamespace

from pixlstash.hub.db import default_hub_path
from pixlstash.hub.workflow_cards import (
    STRIP_LORAS_FOR_STACKS,
    UNRESOLVED_FAMILY,
    WORKFLOW_KEY_VERSION,
    auto_workflow_id,
    variant_families,
)
from pixlstash.hub.workflow_group_convert import (
    _CORE_RULE_V1,
    _Reader,
    _core_strip_v1,
    card_document,
    stranded_workflow_ids,
)
from pixlstash.services.workflow_hash import graph_key
from pixlstash.services.workflow_inbox import workflow_user_dir
from pixlstash.services.workflow_identity import (
    _core_pass,
    _reduce,
    core_hash,
    has_sampler,
    special_groups,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--hub", default=default_hub_path())
    args = parser.parse_args()
    conn = sqlite3.connect(f"file:{args.hub}?mode=ro", uri=True)
    hub = _Reader(conn)

    v1_rows = {
        row["topology_hash"]: row
        for row in hub.fetchall(
            "SELECT topology_hash, core_hash, workflow_type FROM workflow_topology_core "
            "WHERE core_version = ?",
            (_CORE_RULE_V1,),
        )
    }
    cached = {
        row[0]
        for row in hub.fetchall("SELECT topology_hash FROM workflow_topology_core")
    }
    cards: dict[str, dict[str, list[str]]] = {}  # topology -> {card: variants}
    for row in hub.fetchall(
        "SELECT topology_hash, workflow_key, structural_hash FROM workflow_variant "
        "ORDER BY structural_hash"
    ):
        cards.setdefault(row["topology_hash"], {}).setdefault(
            row["workflow_key"], []
        ).append(row["structural_hash"])

    # As step 8 at the upgrade: every v1 row, and every card topology with no
    # cache row at all (steps 5 and 6 filed it on the v1 core of its document).
    uncached = sorted(set(cards) - cached)
    print(f"Hub: {args.hub}")
    print(f"Topologies on {_CORE_RULE_V1}: {len(v1_rows)}")
    print(f"Card topologies with no cache row: {len(uncached)}")
    heirs: dict[str, Counter] = {}  # old id -> {new id: variants}
    olds_of: dict[str, set[str]] = {}
    core_of_new: dict[str, str] = {}
    families_of_new: dict[str, str] = {}
    sampled_cores: dict[str, set[str]] = {}  # old id -> v2 cores of sampled graphs
    unsampled: list[str] = []
    pruned_total: Counter = Counter()
    refused, unreadable = [], []
    kinds: dict[str, set[str]] = {}
    shelf: list = []
    unresolved = 0
    for topology in sorted(set(v1_rows) | set(uncached)):
        row = v1_rows.get(topology)
        documents = {}
        for key, variants in cards.get(topology, {}).items():
            card = SimpleNamespace(
                workflow_key=key, topology_hash=topology, variants=variants
            )
            documents[key] = (card, card_document(hub, card))
        readable = [found for _, found in documents.values() if found]
        if not readable:
            unreadable.append(topology)
            continue
        document = readable[0][1]
        old = "auto:" + (
            row["core_hash"] if row else graph_key(_core_strip_v1(document))
        )
        _, pruned, was_refused = _core_pass(document, STRIP_LORAS_FOR_STACKS)
        pruned_total.update(pruned)
        if was_refused:
            refused.append(topology)
        new_core = core_hash(document, strip_loras=STRIP_LORAS_FOR_STACKS)
        if has_sampler(_reduce(document)):
            sampled_cores.setdefault(old, set()).add(new_core)
        else:
            unsampled.append(topology)
        kind = ((row["workflow_type"] if row else None) or "untyped") + "".join(
            f"+{g}" for g in special_groups(document)
        )
        for card, found in documents.values():
            if found is None:
                continue  # step 8 leaves such a card pending
            structural_hash, card_doc = found
            families = variant_families(hub, structural_hash, card_doc, shelf)
            unresolved += UNRESOLVED_FAMILY in families.split(",")
            new = auto_workflow_id(new_core, families)
            core_of_new[new], families_of_new[new] = new_core, families
            heirs.setdefault(old, Counter())[new] += len(card.variants)
            olds_of.setdefault(new, set()).add(old)
            kinds.setdefault(new, set()).add(kind)

    combined = {new: olds for new, olds in olds_of.items() if len(olds) > 1}
    split = {old: news for old, news in heirs.items() if len(news) > 1}
    by_family = {
        old for old, news in split.items() if len({core_of_new[n] for n in news}) == 1
    }
    violations = {old for old, cores in sampled_cores.items() if len(cores) > 1}
    # Data step 7 retires a file-only card's `auto:<topology>` for the manual
    # workflow it makes of the file, when the file is in the user folder.
    keyed = {
        row[0]
        for row in hub.fetchall(
            "SELECT DISTINCT topology_hash FROM workflow_variant WHERE key_version = ?",
            (WORKFLOW_KEY_VERSION,),
        )
    }
    folder = workflow_user_dir()
    adopted = {
        f"auto:{row['topology_hash']}"
        for row in hub.fetchall(
            "SELECT workflow_name, topology_hash FROM workflow_file"
        )
        if row["topology_hash"] not in keyed
        and os.path.isfile(os.path.join(folder, row["workflow_name"]))
    }
    stranded = stranded_workflow_ids(hub, set(olds_of), set(heirs) | adopted)

    print(f"Automatic workflows before: {len(heirs)}")
    print(f"Automatic workflows after:  {len(olds_of)}")
    print(f"Groups that combine: {len(combined)}")
    print(
        "Largest combine: "
        f"{max((len(olds) for olds in combined.values()), default=0)} workflows"
    )
    print(f"v1 workflows that split: {len(split)}")
    print(f"  by base-model family only: {len(by_family)}")
    print(
        f"  by a graph with no sampler (and maybe family): {len(split) - len(by_family)}"
    )
    print(f"Topologies with no sampler (stages kept as core): {len(unsampled)}")
    print(f"Sampler guard kept whole: {len(refused)} topologies")
    print(f"No readable document (stay on v1): {len(unreadable)} topologies")
    print(f"Cards sharing the '{UNRESOLVED_FAMILY}' family: {unresolved}")
    print(
        "Stranded workflow ids (named, neither live nor retired by steps 7 "
        f"and 8): {len(stranded)}"
    )
    print("\nPruned classes (node count over all topologies):")
    for cls, n in pruned_total.most_common():
        print(f"  {n:5d}  {cls}")

    names = _names(hub)
    print("\nWorkflows that combine:")
    for new, olds in sorted(combined.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(
            f"\n  {new[:17]}… [{families_of_new[new] or 'no base model'}; "
            f"{', '.join(sorted(kinds[new]))}] <- {len(olds)} workflows"
        )
        for old in sorted(olds):
            print(
                f"    {old[:17]}…  {names.get(old) or '(unnamed)'}  "
                f"[{heirs[old][new]} variants]"
            )
    print("\nv1 workflows that split:")
    for old, news in sorted(split.items()):
        why = "family" if old in by_family else "no sampler"
        print(f"\n  {old[:17]}…  {names.get(old) or '(unnamed)'}  ({why})")
        for new in sorted(news):
            print(
                f"    -> {new[:17]}… [{families_of_new[new] or 'no base model'}; "
                f"{news[new]} variants]"
            )
    print("\nEvery v1 workflow and its successors:")
    for old, news in sorted(heirs.items()):
        print(f"  {old[:17]}… -> {', '.join(n[:17] + '…' for n in sorted(news))}")
    if stranded:
        print("\nStranded workflow ids:")
        for workflow_id in stranded:
            print(f"  {workflow_id}")
    if refused:
        print("\nTopologies the sampler guard kept whole:")
        for topology in refused:
            print(f"  {topology}")

    print("\nSplits only by family or a graph with no sampler: ", end="")
    if violations:
        print(f"VIOLATED by {len(violations)} v1 workflows: {sorted(violations)}")
        return 1
    print("holds.")
    return 0


def _names(hub) -> dict[str, str]:
    """``{v1 id: a readable name}``: the owner's, else the cards', else its base model."""
    names: dict[str, str] = {}
    for row in hub.fetchall(
        "SELECT workflow_id, name FROM workflow_group_attr WHERE name IS NOT NULL"
    ):
        names[row["workflow_id"]] = row["name"]
    for row in hub.fetchall(
        "SELECT c.core_hash AS core, a.name AS name, "
        "MIN(ra.normalized_filename) AS model "
        "FROM workflow_topology_core c "
        "JOIN workflow_variant v ON v.topology_hash = c.topology_hash "
        "LEFT JOIN workflow_attr a ON a.workflow_key = v.workflow_key "
        "LEFT JOIN workflow_recipe_asset ra ON ra.structural_hash = v.structural_hash "
        "AND ra.widget_name IN ('ckpt_name', 'unet_name', 'diffusion_model', "
        "'model_path', 'checkpoint_id') "
        "GROUP BY c.core_hash, a.name"
    ):
        names.setdefault(f"auto:{row['core']}", row["name"] or f"model {row['model']}")
    return names


if __name__ == "__main__":
    sys.exit(main())
