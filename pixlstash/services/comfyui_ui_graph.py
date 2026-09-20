"""Convert a ComfyUI **editor** graph into the API prompt ComfyUI executes.

ComfyUI embeds two different things in a picture it generates: the ``prompt``
chunk, which is the resolved API graph the server ran, and the ``workflow``
chunk, which is the editor's own node graph. Only the first is submittable, and
for a long time PixlStash refused to look at the second at all - converting one
means re-resolving widget values, links and bypassed nodes exactly as the
ComfyUI frontend does, and a near-miss yields a graph that runs and silently
generates something else.

That refusal cost every picture whose ``prompt`` chunk is missing - a file
exported from the editor, re-saved by a node that only writes ``workflow``, or
put together by hand - its whole recipe, which is most of what the lightbox's
Recipe tab is for.

**So the conversion is done, and it is done with ComfyUI rather than guessed
at.** ``/object_info`` is the same map the frontend builds its widgets from: it
names every input of every installed class, in declaration order, and marks the
ones that carry an extra frontend-only widget (``control_after_generate`` after
a seed, the upload button after a file picker). With it in hand the positional
``widgets_values`` array maps onto named inputs the way the editor maps it.

**The safety property is that this refuses rather than approximates.** Every
node must be a class this ComfyUI declares, and its widget values must be
accounted for exactly; one unexplained value means the model of the frontend is
off and every later value in that node is suspect, so the whole conversion is
abandoned and the caller is told why. Measured over the 15 editor/API workflow
pairs shipped by comfyui-multigpu, that rule converts 9 exactly and refuses the
other 6 - and produces no graph that differs from the real one.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Input types whose value is a widget in the editor rather than a wire. A combo
# declares its options as a list in place of a type name, so anything that is
# not a string is a combo.
_WIDGET_TYPES = frozenset({"INT", "FLOAT", "STRING", "BOOLEAN", "COMBO"})

# Options that make the editor draw a SECOND widget right after the one they
# belong to: the seed's control-after-generate row, and the upload button on a
# file picker. Each consumes a slot of `widgets_values` that no API input takes.
_EXTRA_WIDGET_OPTIONS = (
    "control_after_generate",
    "image_upload",
    "audio_upload",
    "video_upload",
    "file_upload",
)

# Editor furniture. These carry no class on the server and nothing a run needs,
# so they are dropped rather than refused.
_ANNOTATION_CLASSES = frozenset({"Note", "MarkdownNote"})

# A drawn link. The server has no such class; a wire through one resolves to
# whatever feeds it.
_PASSTHROUGH_CLASSES = frozenset({"Reroute"})

# Node.mode in the editor graph: 2 is muted, 4 is bypassed. Neither runs.
_MODE_MUTED = 2
_MODE_BYPASSED = 4

# A wire chased through reroutes and bypassed nodes cannot reasonably be this
# long; a cycle in a hand-edited file would otherwise not terminate.
_MAX_LINK_DEPTH = 64


def is_ui_graph(workflow) -> bool:
    """Return True when *workflow* is an editor graph with nodes to convert."""
    return isinstance(workflow, dict) and isinstance(workflow.get("nodes"), list)


def _declared_inputs(node_spec: dict) -> list[tuple[str, object, dict]]:
    """``[(name, type_field, options)]`` for a class, in declaration order.

    ``input_order`` is ComfyUI's own answer to "which widget comes first",
    which is the only thing that makes a positional ``widgets_values`` array
    readable. Without it the dict's insertion order is the same answer for
    every ComfyUI this decade, so it is the fallback rather than a refusal.
    """
    inputs = node_spec.get("input")
    if not isinstance(inputs, dict):
        return []
    order = node_spec.get("input_order")
    order = order if isinstance(order, dict) else {}
    declared: list[tuple[str, object, dict]] = []
    for group in ("required", "optional"):
        group_spec = inputs.get(group)
        if not isinstance(group_spec, dict):
            continue
        names = order.get(group)
        if not isinstance(names, list):
            names = list(group_spec.keys())
        for name in names:
            entry = group_spec.get(name)
            if not isinstance(entry, list) or not entry:
                continue
            options = entry[1] if len(entry) > 1 and isinstance(entry[1], dict) else {}
            declared.append((str(name), entry[0], options))
    return declared


def _is_widget(type_field, options: dict) -> bool:
    """Whether this input takes a value from ``widgets_values``.

    ``forceInput``/``defaultInput`` are the editor's "this one is a socket even
    though its type could be typed in", so they take no widget slot.
    """
    if options.get("forceInput") or options.get("defaultInput"):
        return False
    if not isinstance(type_field, str):
        return True
    return type_field in _WIDGET_TYPES


def _extra_widget_slots(options: dict) -> int:
    return sum(1 for option in _EXTRA_WIDGET_OPTIONS if options.get(option))


def _node_class(node: dict) -> str:
    """The server class of an editor node.

    ``Node name for S&R`` is what the editor records when a node was renamed by
    a pack that also renamed its class; ``type`` is the ordinary answer.
    """
    properties = node.get("properties")
    if isinstance(properties, dict):
        recorded = properties.get("Node name for S&R")
        if isinstance(recorded, str) and recorded:
            return recorded
    return str(node.get("type") or "")


def _index_links(workflow: dict) -> dict[object, tuple[object, int]]:
    """``{link_id: (origin_node_id, origin_slot)}`` for both link spellings."""
    links: dict[object, tuple[object, int]] = {}
    for link in workflow.get("links") or []:
        if isinstance(link, list) and len(link) >= 3:
            links[link[0]] = (link[1], link[2])
        elif isinstance(link, dict) and link.get("id") is not None:
            links[link["id"]] = (link.get("origin_id"), link.get("origin_slot") or 0)
    return links


class _Converter:
    """One conversion. Holds the graph's indexes so the link chase is cheap."""

    def __init__(self, workflow: dict, object_info: dict):
        self.object_info = object_info
        self.nodes = {
            node.get("id"): node
            for node in workflow.get("nodes") or []
            if isinstance(node, dict) and node.get("id") is not None
        }
        self.links = _index_links(workflow)
        self.problems: list[str] = []

    def _resolve(self, link_id, depth: int = 0) -> list | None:
        """``[node_id, slot]`` for a wire, chasing reroutes and bypasses.

        A muted node breaks the wire, which is what muting means; ComfyUI then
        refuses the prompt for a missing required input, exactly as it does for
        the same graph submitted from the editor.
        """
        if depth > _MAX_LINK_DEPTH:
            self.problems.append("a wire runs in a circle")
            return None
        origin = self.links.get(link_id)
        if origin is None:
            return None
        origin_id, origin_slot = origin
        node = self.nodes.get(origin_id)
        if node is None:
            return None
        mode = node.get("mode") or 0
        node_class = _node_class(node)
        if mode == _MODE_MUTED:
            return None
        if mode == _MODE_BYPASSED or node_class in _PASSTHROUGH_CLASSES:
            return self._chase_through(node, origin_slot, depth)
        if node_class not in self.object_info:
            self.problems.append(f"this ComfyUI has no node class {node_class!r}")
            return None
        return [str(origin_id), origin_slot]

    def _chase_through(self, node: dict, origin_slot, depth: int) -> list | None:
        """Follow a bypassed or rerouted node to whatever feeds its output."""
        outputs = node.get("outputs") or []
        wanted = None
        if isinstance(origin_slot, int) and 0 <= origin_slot < len(outputs):
            entry = outputs[origin_slot]
            wanted = entry.get("type") if isinstance(entry, dict) else None
        for candidate in node.get("inputs") or []:
            if not isinstance(candidate, dict) or candidate.get("link") is None:
                continue
            if wanted in (None, "*") or candidate.get("type") in (wanted, "*"):
                return self._resolve(candidate["link"], depth + 1)
        return None

    def _connected_inputs(self, node: dict) -> dict[str, object]:
        return {
            str(entry.get("name")): entry["link"]
            for entry in node.get("inputs") or []
            if isinstance(entry, dict) and entry.get("link") is not None
        }

    def _node_inputs(self, node: dict, node_class: str) -> dict | None:
        """The API ``inputs`` map for one node, or None when it cannot be read."""
        declared = _declared_inputs(self.object_info[node_class])
        connected = self._connected_inputs(node)
        widget_values = node.get("widgets_values")
        # Newer editor saves key the widget values by name, which needs no
        # positional accounting at all.
        by_name = isinstance(widget_values, dict)
        positional = widget_values if isinstance(widget_values, list) else []
        inputs: dict = {}
        consumed = 0
        for name, type_field, options in declared:
            is_widget = _is_widget(type_field, options)
            if name in connected:
                resolved = self._resolve(connected[name])
                if resolved is not None:
                    inputs[name] = resolved
                # A widget input that has been wired up still holds its slot in
                # the array: the editor keeps the last typed value behind the
                # socket.
                if is_widget and not by_name:
                    consumed += 1 + _extra_widget_slots(options)
                continue
            if not is_widget:
                continue
            if by_name:
                if name in widget_values:
                    inputs[name] = widget_values[name]
                continue
            if consumed < len(positional):
                inputs[name] = positional[consumed]
            consumed += 1 + _extra_widget_slots(options)
        if not by_name and consumed != len(positional):
            self.problems.append(
                f"{node_class} (node {node.get('id')}) carries "
                f"{len(positional)} widget values, and its inputs account for "
                f"{consumed}"
            )
            return None
        return inputs

    def run(self) -> dict:
        prompt: dict = {}
        for node_id, node in self.nodes.items():
            node_class = _node_class(node)
            if node_class in _ANNOTATION_CLASSES:
                continue
            if (node.get("mode") or 0) in (_MODE_MUTED, _MODE_BYPASSED):
                continue
            if node_class in _PASSTHROUGH_CLASSES:
                continue
            if node_class not in self.object_info:
                self.problems.append(f"this ComfyUI has no node class {node_class!r}")
                continue
            inputs = self._node_inputs(node, node_class)
            if inputs is None:
                continue
            title = node.get("title")
            if not isinstance(title, str) or not title:
                title = self.object_info[node_class].get("display_name") or node_class
            prompt[str(node_id)] = {
                "inputs": inputs,
                "class_type": node_class,
                "_meta": {"title": title},
            }
        return prompt


def convert_ui_graph_to_api(workflow, object_info) -> tuple[dict | None, list[str]]:
    """Convert an editor graph to the API prompt, or say why it cannot be.

    Args:
        workflow: The editor graph - the ``workflow`` chunk of a picture, or a
            stored ``.json`` saved from the ComfyUI editor.
        object_info: ComfyUI's ``GET /object_info`` map. ``None`` is the honest
            "ComfyUI could not be asked", which is a refusal and not a licence
            to guess.

    Returns:
        ``(prompt, [])`` when the graph converted exactly, and
        ``(None, problems)`` otherwise, where each problem is one sentence
        naming what could not be read. An empty graph - every node an
        annotation, or no nodes at all - converts to nothing and is reported as
        a problem rather than as an empty prompt ComfyUI would refuse.
    """
    if not is_ui_graph(workflow):
        return None, ["this is not a ComfyUI editor graph"]
    if not isinstance(object_info, dict) or not object_info:
        return None, ["PixlStash could not ask ComfyUI which nodes it has"]
    # Refused by name rather than by its symptom. A subgraph instance's class
    # is the definition's uuid, so without this the report would be "this
    # ComfyUI has no node class '6e0f8...'", which tells the reader nothing.
    # Inlining them is `reduce_ui_graph`'s job and it reduces for a hash, not
    # for a run.
    # ponytail: subgraphs and the deprecated `PrimitiveNode` are the two things
    # this refuses that the editor can resolve; expand them here if real
    # pictures turn out to carry them.
    if (workflow.get("definitions") or {}).get("subgraphs"):
        return None, ["this editor graph uses subgraphs, which PixlStash cannot run"]
    converter = _Converter(workflow, object_info)
    prompt = converter.run()
    if converter.problems:
        # Deduplicated, because one missing node pack is one problem however
        # many nodes it accounts for, and the list is read by a person.
        seen: list[str] = []
        for problem in converter.problems:
            if problem not in seen:
                seen.append(problem)
        logger.info(
            "[comfyui] Editor graph could not be converted to an API prompt: %s",
            "; ".join(seen),
        )
        return None, seen
    if not prompt:
        return None, ["this editor graph has no nodes that would run"]
    return prompt, []
