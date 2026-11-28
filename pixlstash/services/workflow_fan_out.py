"""Fan one picture input of a workflow out over several pictures.

A scene sends more reference pictures than a workflow has picture inputs. The
run takes one input and clones the picture's path once per extra picture: the
loader, and every IMAGE-to-IMAGE node between it and a numbered consumer slot
(an ``ImageScaleToTotalPixels`` in front of an encoder, say). The clone of the
path's last node is wired into the next numbered slot of that consumer:

- an edge from the path to ``<prefix><n>`` on a consumer makes, for the k-th
  extra picture, an edge from that picture's clone to ``<prefix><n+k>`` on the
  same consumer, **if ComfyUI's ``object_info`` accepts that name** there: a
  declared input, or an autogrow input (``COMFY_AUTOGROW_V3``) whose template
  yields it. A dotted API key (``images.image_1``) names the autogrow input and
  the name inside it. Each edge steps from its own number.
- a node joins a path only when it has exactly one linked input, which is the
  link from upstream, and its output 0 is IMAGE. A node with more links is
  shared and never cloned; a node on no path to a numbered slot is not cloned.
  A numbered consumer ends a path and is never walked through.
- an edge whose next name is not accepted is not made for that clone. A clone
  that gets no edge at all means the input is at capacity.
- a slot already wired, or claimed by an earlier clone, is never written over.
  A picture that would have to overwrite one is not dropped: the capacity stops
  there.

Pure over the API-format graph and ``object_info``: no server, no I/O.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass

# The image output of a loader (LoadImage, PixlStashPictureLoader) and of an
# image-to-image node: IMAGE is output 0, MASK output 1. A clone provides IMAGE.
IMAGE = "IMAGE"
IMAGE_OUTPUT = 0

# The widest autogrow input ComfyUI ships (ReferenceMultiLatent, 100); a clone
# beyond it has no name to take, so this also bounds the search.
MAX_PICTURES = 100

# The consumer whose prompt names pictures ``<Picture n>`` (Krea-style tags).
MINIMAX_CONSUMER = "MiniMaxH3ImageConditioning"

_AUTOGROW = "COMFY_AUTOGROW_V3"
_NUMBERED = re.compile(r"(.*?)(\d+)")
_CANONICAL_INDEX = re.compile(r"0|[1-9]\d*")


class FanOutError(ValueError):
    """A fan-out a request asked for that the graph cannot take.

    The message is the reason a caller shows; the route answers 400 with it.
    """


@dataclass(frozen=True)
class _End:
    """A numbered input a picture's path feeds.

    ``source`` is the loader or chain node whose IMAGE feeds the input.
    ``path`` is the chain from the loader to ``source`` as ``(upstream, node)``
    pairs, outermost first; empty when the loader feeds the input directly.
    """

    source: str
    consumer: str
    base: str | None
    prefix: str
    number: int
    path: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class FannedPicture:
    """One extra picture's clones: its feed targets, one per loader cloned."""

    picture_id: int
    targets: list[dict]


def _is_link(value) -> bool:
    return isinstance(value, list) and len(value) == 2


def _links(graph: dict) -> dict[str, list[tuple[str, str, int]]]:
    """Every link, by the node it reads from: ``(consumer, key, output)``."""
    found: dict[str, list[tuple[str, str, int]]] = {}
    for consumer, node in graph.items():
        if not isinstance(node, dict):
            continue
        for key, value in (node.get("inputs") or {}).items():
            if _is_link(value):
                found.setdefault(str(value[0]), []).append((consumer, key, value[1]))
    return found


def _declared(info: dict) -> dict:
    inputs = info.get("input") or {}
    return {**(inputs.get("required") or {}), **(inputs.get("optional") or {})}


def _accepts(info: dict, base: str | None, name: str) -> bool:
    """Whether a consumer of class *info* takes *name* (inside *base*, if any)."""
    declared = _declared(info)
    if base is None:
        return name in declared
    spec = declared.get(base)
    if not (
        isinstance(spec, list)
        and len(spec) == 2
        and spec[0] == _AUTOGROW
        and isinstance(spec[1], dict)
    ):
        return False
    template = spec[1].get("template") or {}
    names = template.get("names")
    if isinstance(names, list):
        return name in names
    prefix, limit = template.get("prefix"), template.get("max")
    if not isinstance(prefix, str) or not isinstance(limit, int):
        return False
    if not name.startswith(prefix):
        return False
    index = name[len(prefix) :]
    return bool(_CANONICAL_INDEX.fullmatch(index)) and int(index) < limit


def _joins_path(graph: dict, object_info: dict, node_id: str) -> bool:
    """Whether *node_id* can sit on a picture's path: one link, IMAGE out."""
    node = graph[node_id]
    linked = [
        key for key, value in (node.get("inputs") or {}).items() if _is_link(value)
    ]
    if len(linked) != 1:
        return False
    outputs = (object_info.get(node.get("class_type")) or {}).get("output") or []
    return bool(outputs) and outputs[0] == IMAGE


def _downstream(
    graph: dict,
    links: dict[str, list[tuple[str, str, int]]],
    object_info: dict,
    source: str,
    path: tuple[tuple[str, str], ...],
    seen: frozenset[str],
    found: list[_End],
) -> list[_End]:
    """Every numbered input the picture reaching *source* feeds, in order."""
    for consumer, key, output in links.get(source, ()):
        if output != IMAGE_OUTPUT:
            continue
        base, dot, name = key.partition(".")
        if not dot:
            base, name = None, key
        match = _NUMBERED.fullmatch(name)
        if match:
            found.append(
                _End(source, consumer, base, match.group(1), int(match.group(2)), path)
            )
            continue
        if consumer in seen or not _joins_path(graph, object_info, consumer):
            continue
        _downstream(
            graph,
            links,
            object_info,
            consumer,
            (*path, (source, consumer)),
            seen | {consumer},
            found,
        )
    return found


def _wiring(
    graph: dict, loader_ids: list[str], object_info: dict | None
) -> list[dict[str, list[tuple[_End, str]]]]:
    """Per clone the input can take, each loader's wired ``(end, key)`` pairs.

    Joint over *loader_ids*, with one claimed set: two clones never write the
    same consumer key, even across loaders. The list stops before the first
    clone that gets no edge on some loader, which is the capacity.
    """
    if not object_info or not loader_ids:
        return []
    links = _links(graph)
    ends = {
        loader: _downstream(
            graph, links, object_info, loader, (), frozenset({loader}), []
        )
        for loader in loader_ids
    }
    claimed: set[tuple[str, str]] = set()
    plan: list[dict[str, list[tuple[_End, str]]]] = []
    for k in range(1, MAX_PICTURES + 1):
        this: dict[str, list[tuple[_End, str]]] = {}
        for loader in loader_ids:
            wired = []
            for end in ends[loader]:
                name = f"{end.prefix}{end.number + k}"
                key = f"{end.base}.{name}" if end.base is not None else name
                node = graph[end.consumer]
                info = object_info.get(node.get("class_type")) or {}
                if not _accepts(info, end.base, name):
                    continue
                if key in (node.get("inputs") or {}) or (end.consumer, key) in claimed:
                    continue
                claimed.add((end.consumer, key))
                wired.append((end, key))
            this[loader] = wired
        if not all(this.values()):
            break
        plan.append(this)
    return plan


def fan_out_capacity(
    graph: dict, loader_ids: list[str], object_info: dict | None
) -> int:
    """How many extra pictures the input those loaders feed can take.

    0 when nothing can fan out, or when ``object_info`` is unavailable.
    Does not change *graph*.
    """
    return len(_wiring(graph, loader_ids, object_info))


def fan_out_picture_input(
    graph: dict,
    loaders: list[tuple[str, dict]],
    picture_ids: list[int],
    object_info: dict | None,
) -> list[FannedPicture]:
    """Clone each picture's path once per extra picture, wired into the next slots.

    *loaders* is ``(node_id, feed target)`` for each loader of the input; a
    target's path ends ``[node_id, "inputs", field]``, and the clone's target is
    the same path on the loader's clone. *graph* is changed in place: the clone
    nodes are added and the consumers gain their new inputs. Nothing is changed
    when *picture_ids* is empty.

    Raises:
        FanOutError: more pictures than the input can take (the message gives
            the capacity), or a loader whose target does not end in an input
            field of its own node.
    """
    if not picture_ids or not loaders:
        return []
    loader_ids = [node_id for node_id, _target in loaders]
    targets = dict(loaders)
    for node_id, target in loaders:
        path = target.get("path") or []
        if len(path) < 3 or path[-3] != node_id or path[-2] != "inputs":
            raise FanOutError(
                f"Node {node_id} cannot be cloned: its picture target is not one "
                "of its own inputs."
            )
    plan = _wiring(graph, loader_ids, object_info)
    if len(picture_ids) > len(plan):
        raise FanOutError(
            f"This picture input takes at most {len(plan)} more "
            f"picture{'' if len(plan) == 1 else 's'}; {len(picture_ids)} were sent."
        )
    # Every clone is cut from the graph as it was, before this call wires it.
    original = copy.deepcopy(graph)
    taken = set(graph)
    fanned = []
    for k, picture_id in enumerate(picture_ids):
        clone_targets = []
        for node_id in loader_ids:
            wired = plan[k][node_id]
            mapping = {node_id: _unused_id(f"{node_id}_pic{k + 1}", taken)}
            taken.add(mapping[node_id])
            for end, _key in wired:
                for upstream, node in end.path:
                    if node not in mapping:
                        mapping[node] = _unused_id(f"{node}_pic{k + 1}", taken)
                        taken.add(mapping[node])
                        clone = copy.deepcopy(original[node])
                        # The one link a path node has is from its upstream.
                        [chain_key] = [
                            key
                            for key, value in clone["inputs"].items()
                            if _is_link(value)
                        ]
                        clone["inputs"][chain_key] = [mapping[upstream], IMAGE_OUTPUT]
                        graph[mapping[node]] = clone
            graph[mapping[node_id]] = copy.deepcopy(original[node_id])
            for end, key in wired:
                graph[end.consumer]["inputs"][key] = [
                    mapping[end.source],
                    IMAGE_OUTPUT,
                ]
            path = targets[node_id]["path"]
            clone_targets.append(
                {
                    **targets[node_id],
                    "path": [*path[:-3], mapping[node_id], "inputs", path[-1]],
                }
            )
        fanned.append(FannedPicture(picture_id, clone_targets))
    return fanned


def _unused_id(candidate: str, taken: set[str]) -> str:
    """*candidate*, or the first ``candidate.<n>`` no node already has."""
    unused, n = candidate, 0
    while unused in taken:
        n += 1
        unused = f"{candidate}.{n}"
    return unused


def picture_tag(graph: dict, loader_ids: list[str]) -> str:
    """How a prompt names the n-th picture for the nodes these loaders feed.

    A template with ``{n}``: ``<Picture {n}>`` when a node the loaders feed is
    a MiniMax image conditioner, which names its pictures that way, else
    ``picture {n}``.
    """
    loaders = set(loader_ids)
    for node in graph.values():
        if not isinstance(node, dict):
            continue
        for value in (node.get("inputs") or {}).values():
            if (
                _is_link(value)
                and str(value[0]) in loaders
                and node.get("class_type") == MINIMAX_CONSUMER
            ):
                return "<Picture {n}>"
    return "picture {n}"
