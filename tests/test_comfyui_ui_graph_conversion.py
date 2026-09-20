"""Converting ComfyUI's **editor** graph into the API prompt it executes.

The property under test throughout is not "it converts" but **"it is exact or
it refuses"**. A near-miss here is the worst outcome available: the graph runs,
ComfyUI accepts it, and it makes a different picture than the one the reader
asked to make again. So every refusal case below is a case where a plausible
guess exists and is deliberately not made.

Measured separately against the 15 editor/API workflow pairs shipped by
comfyui-multigpu: 8 converted, 8 byte-identical to the API half, 0 wrong, 7
refused. The refusals are node packs the machine did not have installed, the
deprecated ``PrimitiveNode``, and two classes whose widget arrays this does not
account for - each named in the report rather than papered over.
"""

import pytest

from pixlstash.services.comfyui_ui_graph import convert_ui_graph_to_api, is_ui_graph

# ``seed`` carries control_after_generate, so the editor draws a SECOND widget
# after it and ``widgets_values`` is one longer than the inputs that take a
# value. Getting that wrong shifts steps into cfg and cfg into sampler_name.
OBJECT_INFO = {
    "CheckpointLoaderSimple": {
        "input": {"required": {"ckpt_name": [["sd_xl_base_1.0.safetensors"], {}]}},
        "input_order": {"required": ["ckpt_name"]},
    },
    "CLIPTextEncode": {
        "input": {
            "required": {
                "text": ["STRING", {"multiline": True}],
                "clip": ["CLIP", {}],
            }
        },
        "input_order": {"required": ["text", "clip"]},
    },
    "KSampler": {
        "input": {
            "required": {
                "model": ["MODEL", {}],
                "seed": ["INT", {"control_after_generate": True}],
                "steps": ["INT", {}],
                "cfg": ["FLOAT", {}],
                "positive": ["CONDITIONING", {}],
                "negative": ["CONDITIONING", {}],
            }
        },
        "input_order": {
            "required": ["model", "seed", "steps", "cfg", "positive", "negative"]
        },
    },
    "SaveImage": {
        "input": {
            "required": {
                "images": ["IMAGE", {}],
                "filename_prefix": ["STRING", {}],
            }
        },
        "input_order": {"required": ["images", "filename_prefix"]},
    },
    "VAEDecode": {
        "input": {"required": {"samples": ["LATENT", {}], "vae": ["VAE", {}]}},
        "input_order": {"required": ["samples", "vae"]},
    },
}


def _node(node_id, node_type, *, inputs=None, outputs=None, widgets=None, mode=0):
    return {
        "id": node_id,
        "type": node_type,
        "mode": mode,
        "inputs": inputs or [],
        "outputs": outputs or [],
        "widgets_values": [] if widgets is None else widgets,
    }


def _graph(nodes, links):
    return {"nodes": nodes, "links": links, "last_node_id": 99, "last_link_id": 99}


@pytest.fixture
def simple_editor_graph():
    """Loader → encoder → sampler → SaveImage, wired as the editor writes it."""
    return _graph(
        [
            _node(
                4,
                "CheckpointLoaderSimple",
                outputs=[{"name": "MODEL", "type": "MODEL", "links": [1]}],
                widgets=["sd_xl_base_1.0.safetensors"],
            ),
            _node(
                6,
                "CLIPTextEncode",
                inputs=[{"name": "clip", "type": "CLIP", "link": None}],
                outputs=[
                    {"name": "CONDITIONING", "type": "CONDITIONING", "links": [2]}
                ],
                widgets=["a cat in a hat"],
            ),
            _node(
                3,
                "KSampler",
                inputs=[
                    {"name": "model", "type": "MODEL", "link": 1},
                    {"name": "positive", "type": "CONDITIONING", "link": 2},
                ],
                outputs=[{"name": "LATENT", "type": "LATENT", "links": [3]}],
                widgets=[424242, "randomize", 20, 7.5],
            ),
            _node(
                9,
                "SaveImage",
                inputs=[{"name": "images", "type": "IMAGE", "link": 3}],
                widgets=["ComfyUI"],
            ),
        ],
        [
            [1, 4, 0, 3, 0, "MODEL"],
            [2, 6, 0, 3, 1, "CONDITIONING"],
            [3, 3, 0, 9, 0, "IMAGE"],
        ],
    )


class TestItConvertsExactly:
    def test_widget_values_land_on_the_inputs_they_belong_to(self, simple_editor_graph):
        prompt, problems = convert_ui_graph_to_api(simple_editor_graph, OBJECT_INFO)
        assert problems == []
        assert prompt["3"]["class_type"] == "KSampler"
        # The whole point: "randomize" is the control-after-generate widget, so
        # steps is 20 and cfg is 7.5 - not "randomize" and 20.
        assert prompt["3"]["inputs"]["seed"] == 424242
        assert prompt["3"]["inputs"]["steps"] == 20
        assert prompt["3"]["inputs"]["cfg"] == 7.5
        assert prompt["6"]["inputs"]["text"] == "a cat in a hat"
        assert prompt["4"]["inputs"]["ckpt_name"] == "sd_xl_base_1.0.safetensors"
        assert prompt["9"]["inputs"]["filename_prefix"] == "ComfyUI"

    def test_links_become_node_and_slot_references(self, simple_editor_graph):
        prompt, _ = convert_ui_graph_to_api(simple_editor_graph, OBJECT_INFO)
        assert prompt["3"]["inputs"]["model"] == ["4", 0]
        assert prompt["3"]["inputs"]["positive"] == ["6", 0]
        assert prompt["9"]["inputs"]["images"] == ["3", 0]

    def test_an_unconnected_optional_socket_is_simply_absent(self, simple_editor_graph):
        """The encoder's `clip` is drawn but unwired, and `negative` has no
        socket at all. Neither may be invented as a value."""
        prompt, _ = convert_ui_graph_to_api(simple_editor_graph, OBJECT_INFO)
        assert "clip" not in prompt["6"]["inputs"]
        assert "negative" not in prompt["3"]["inputs"]

    def test_widget_values_keyed_by_name_need_no_positional_accounting(self):
        """The newer editor saves a dict, which cannot shift."""
        graph = _graph(
            [
                _node(
                    3,
                    "KSampler",
                    widgets={"seed": 7, "steps": 30, "cfg": 4.0},
                )
            ],
            [],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert problems == []
        assert prompt["3"]["inputs"] == {"seed": 7, "steps": 30, "cfg": 4.0}

    def test_a_reroute_resolves_to_whatever_feeds_it(self):
        """`Reroute` is drawn furniture with no class on the server."""
        graph = _graph(
            [
                _node(
                    4,
                    "CheckpointLoaderSimple",
                    outputs=[{"name": "MODEL", "type": "MODEL", "links": [1]}],
                    widgets=["sd_xl_base_1.0.safetensors"],
                ),
                _node(
                    5,
                    "Reroute",
                    inputs=[{"name": "", "type": "*", "link": 1}],
                    outputs=[{"name": "", "type": "MODEL", "links": [2]}],
                ),
                _node(
                    3,
                    "KSampler",
                    inputs=[{"name": "model", "type": "MODEL", "link": 2}],
                    widgets=[1, "fixed", 20, 7.0],
                ),
            ],
            [[1, 4, 0, 5, 0, "MODEL"], [2, 5, 0, 3, 0, "MODEL"]],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert problems == []
        assert "5" not in prompt, "the reroute is not a node ComfyUI would run"
        assert prompt["3"]["inputs"]["model"] == ["4", 0]

    def test_a_bypassed_node_hands_its_input_straight_on(self):
        """Mode 4 is bypass: the node does not run and its wire passes through."""
        graph = _graph(
            [
                _node(
                    4,
                    "CheckpointLoaderSimple",
                    outputs=[{"name": "MODEL", "type": "MODEL", "links": [1]}],
                    widgets=["sd_xl_base_1.0.safetensors"],
                ),
                _node(
                    7,
                    "VAEDecode",
                    mode=4,
                    inputs=[{"name": "samples", "type": "MODEL", "link": 1}],
                    outputs=[{"name": "IMAGE", "type": "MODEL", "links": [2]}],
                ),
                _node(
                    3,
                    "KSampler",
                    inputs=[{"name": "model", "type": "MODEL", "link": 2}],
                    widgets=[1, "fixed", 20, 7.0],
                ),
            ],
            [[1, 4, 0, 7, 0, "MODEL"], [2, 7, 0, 3, 0, "MODEL"]],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert problems == []
        assert "7" not in prompt
        assert prompt["3"]["inputs"]["model"] == ["4", 0]

    def test_editor_annotations_are_dropped_rather_than_refused(self):
        graph = _graph(
            [
                _node(1, "MarkdownNote", widgets=["read me"]),
                _node(
                    4,
                    "CheckpointLoaderSimple",
                    widgets=["sd_xl_base_1.0.safetensors"],
                ),
            ],
            [],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert problems == []
        assert set(prompt) == {"4"}


class TestItRefusesRatherThanGuesses:
    def test_a_class_this_comfyui_does_not_have_stops_the_whole_conversion(self):
        """Not "convert the rest": a graph missing a node is a different graph."""
        graph = _graph(
            [
                _node(1, "SomeCustomPackNode", widgets=[1]),
                _node(
                    4,
                    "CheckpointLoaderSimple",
                    widgets=["sd_xl_base_1.0.safetensors"],
                ),
            ],
            [],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert problems == ["this ComfyUI has no node class 'SomeCustomPackNode'"]

    def test_an_unexplained_widget_value_stops_it(self):
        """One value too many means every later value in the node is suspect.

        This is the case a lenient converter gets wrong silently: the sampler
        below would read steps as 20 and cfg as 7.5 while the real graph ran 7.5
        steps at some other cfg.
        """
        graph = _graph(
            [_node(3, "KSampler", widgets=[1, "fixed", "extra", 20, 7.5])], []
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert problems == [
            "KSampler (node 3) carries 5 widget values, and its inputs account for 4"
        ]

    def test_it_refuses_without_object_info(self):
        """ComfyUI unreachable is a refusal, not a licence to guess the order."""
        prompt, problems = convert_ui_graph_to_api(_graph([], []), None)
        assert prompt is None
        assert problems == ["PixlStash could not ask ComfyUI which nodes it has"]

    def test_subgraphs_are_refused_by_name(self):
        """Refused for what they are, not as "no node class '<uuid>'"."""
        graph = _graph([_node(1, "6e0f8-a-subgraph-uuid")], [])
        graph["definitions"] = {"subgraphs": [{"id": "6e0f8-a-subgraph-uuid"}]}
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert problems == [
            "this editor graph uses subgraphs, which PixlStash cannot run"
        ]

    def test_an_api_graph_is_not_an_editor_graph(self):
        prompt, problems = convert_ui_graph_to_api(
            {"3": {"class_type": "KSampler", "inputs": {}}}, OBJECT_INFO
        )
        assert prompt is None
        assert problems == ["this is not a ComfyUI editor graph"]

    def test_a_graph_of_nothing_but_annotations_converts_to_nothing(self):
        graph = _graph([_node(1, "Note", widgets=["hello"])], [])
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert problems == ["this editor graph has no nodes that would run"]


class TestIsUiGraph:
    def test_it_recognises_the_editor_serialisation(self, simple_editor_graph):
        assert is_ui_graph(simple_editor_graph) is True

    def test_it_rejects_an_api_graph_and_a_non_graph(self):
        assert is_ui_graph({"3": {"class_type": "KSampler"}}) is False
        assert is_ui_graph(None) is False
        assert is_ui_graph("{}") is False
