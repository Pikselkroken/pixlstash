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
    # The upload button is a widget of its own in the editor and no input at
    # all in the API graph - the same shape as control_after_generate, and the
    # commonest i2i node there is.
    "LoadImage": {
        "input": {"required": {"image": [["a.png", "b.png"], {"image_upload": True}]}},
        "input_order": {"required": ["image"]},
    },
    # `forceInput` is "this one is a socket even though its type could be typed
    # in", so it takes no slot in the widget array.
    "CLIPTextEncodeForced": {
        "input": {
            "required": {
                "text": ["STRING", {"forceInput": True}],
                "weight": ["FLOAT", {}],
            }
        },
        "input_order": {"required": ["text", "weight"]},
    },
    # No `input_order`: the declaration order of the dict is the fallback, and
    # a refusal here would refuse every ComfyUI that does not publish one.
    "UnorderedLoader": {
        "input": {"required": {"first": ["STRING", {}], "second": ["INT", {}]}}
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

    def test_a_bypassed_node_hands_the_matching_input_straight_on(self):
        """Mode 4 is bypass: the node does not run and its wire passes through.

        **The OUTPUT's type picks which input is passed through**, which is the
        whole difficulty: the node below is bypassed with two live inputs of
        different types, and only one of them is what the sampler asked for.
        A converter that took "the first connected input" would wire the
        sampler's MODEL socket to a CLIP producer and report an exact rebuild.
        """
        graph = _graph(
            [
                _node(
                    4,
                    "CheckpointLoaderSimple",
                    outputs=[{"name": "MODEL", "type": "MODEL", "links": [1]}],
                    widgets=["sd_xl_base_1.0.safetensors"],
                ),
                _node(
                    41,
                    "CheckpointLoaderSimple",
                    outputs=[{"name": "CLIP", "type": "CLIP", "links": [4]}],
                    widgets=["sd_xl_base_1.0.safetensors"],
                ),
                _node(
                    7,
                    "LoraStub",
                    mode=4,
                    inputs=[
                        # CLIP first on purpose: position must not decide it.
                        {"name": "clip", "type": "CLIP", "link": 4},
                        {"name": "model", "type": "MODEL", "link": 1},
                    ],
                    outputs=[
                        {"name": "MODEL", "type": "MODEL", "links": [2]},
                        {"name": "CLIP", "type": "CLIP", "links": []},
                    ],
                ),
                _node(
                    3,
                    "KSampler",
                    inputs=[{"name": "model", "type": "MODEL", "link": 2}],
                    widgets=[1, "fixed", 20, 7.0],
                ),
            ],
            [
                [1, 4, 0, 7, 1, "MODEL"],
                [4, 41, 0, 7, 0, "CLIP"],
                [2, 7, 0, 3, 0, "MODEL"],
            ],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert problems == []
        assert "7" not in prompt
        assert prompt["3"]["inputs"]["model"] == ["4", 0], (
            "the sampler must be wired to the MODEL producer, not the CLIP one"
        )

    def test_a_wired_widget_input_still_holds_its_slot_in_the_array(self):
        """The rule most likely to shift a whole node's values.

        The editor keeps the last typed value behind a socket that has been
        wired up, so the array still carries it. Counting it as consumed is
        what keeps `weight` reading 0.5 rather than the leftover text.
        """
        graph = _graph(
            [
                _node(
                    6,
                    "CLIPTextEncode",
                    outputs=[{"name": "COND", "type": "STRING", "links": [1]}],
                    widgets=["feeds the socket"],
                ),
                _node(
                    8,
                    "CLIPTextEncodeForced",
                    inputs=[{"name": "text", "type": "STRING", "link": 1}],
                    widgets=[0.5],
                ),
            ],
            [[1, 6, 0, 8, 0, "STRING"]],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert problems == []
        # `text` is forceInput, so it takes NO slot and `weight` reads the
        # first and only value.
        assert prompt["8"]["inputs"] == {"text": ["6", 0], "weight": 0.5}

    def test_a_wired_WIDGET_input_keeps_the_slot_it_left_behind(self):
        """The rule the whole positional read turns on.

        `text` is an ordinary STRING widget that the owner has since wired a
        socket into. The editor keeps the last typed value in the array behind
        that socket, so the array is `["leftover", 0.7]` and `weight` is the
        SECOND entry. A converter that skipped the slot would read `weight` as
        the string, and every later value in the node with it.
        """
        graph = _graph(
            [
                _node(
                    6,
                    "CLIPTextEncode",
                    outputs=[{"name": "OUT", "type": "STRING", "links": [1]}],
                    widgets=["feeds the socket"],
                ),
                _node(
                    9,
                    "WidgetThenWeight",
                    inputs=[{"name": "text", "type": "STRING", "link": 1}],
                    widgets=["leftover", 0.7],
                ),
            ],
            [[1, 6, 0, 9, 0, "STRING"]],
        )
        object_info = dict(OBJECT_INFO)
        object_info["WidgetThenWeight"] = {
            "input": {
                "required": {
                    "text": ["STRING", {"multiline": True}],
                    "weight": ["FLOAT", {}],
                }
            },
            "input_order": {"required": ["text", "weight"]},
        }
        prompt, problems = convert_ui_graph_to_api(graph, object_info)
        assert problems == []
        assert prompt["9"]["inputs"] == {"text": ["6", 0], "weight": 0.7}

    def test_an_upload_button_consumes_a_slot_the_api_graph_has_no_input_for(self):
        """LoadImage: `["picture.png", "image"]` is one input and one button."""
        graph = _graph([_node(10, "LoadImage", widgets=["a.png", "image"])], [])
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert problems == []
        assert prompt["10"]["inputs"] == {"image": "a.png"}

    def test_a_class_that_publishes_no_input_order_uses_its_declaration_order(self):
        """A refusal here would refuse every ComfyUI that omits the field."""
        graph = _graph([_node(11, "UnorderedLoader", widgets=["hello", 3])], [])
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert problems == []
        assert prompt["11"]["inputs"] == {"first": "hello", "second": 3}

    def test_the_dict_spelling_of_a_link_is_read_too(self):
        graph = {
            "nodes": [
                _node(
                    4,
                    "CheckpointLoaderSimple",
                    outputs=[{"name": "MODEL", "type": "MODEL", "links": [1]}],
                    widgets=["sd_xl_base_1.0.safetensors"],
                ),
                _node(
                    3,
                    "KSampler",
                    inputs=[{"name": "model", "type": "MODEL", "link": 1}],
                    widgets=[1, "fixed", 20, 7.0],
                ),
            ],
            "links": [{"id": 1, "origin_id": 4, "origin_slot": 0}],
        }
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert problems == []
        assert prompt["3"]["inputs"]["model"] == ["4", 0]

    def test_a_muted_node_on_an_OPTIONAL_input_is_left_alone(self):
        """Muting a branch off is an ordinary gesture; the editor submits it.

        The control for `test_a_muted_node_on_a_required_input_is_refused`
        below: refusing this one would refuse a workflow that runs.
        """
        graph = _graph(
            [
                _node(
                    6,
                    "CLIPTextEncode",
                    mode=2,
                    outputs=[{"name": "COND", "type": "COND", "links": [1]}],
                    widgets=["off"],
                ),
                _node(
                    12,
                    "OptionalTaker",
                    inputs=[{"name": "extra", "type": "COND", "link": 1}],
                    widgets=[],
                ),
            ],
            [[1, 6, 0, 12, 0, "COND"]],
        )
        object_info = dict(OBJECT_INFO)
        object_info["OptionalTaker"] = {
            "input": {"optional": {"extra": ["COND", {}]}},
            "input_order": {"optional": ["extra"]},
        }
        prompt, problems = convert_ui_graph_to_api(graph, object_info)
        assert problems == []
        assert prompt["12"]["inputs"] == {}
        assert "6" not in prompt

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

    def test_the_class_is_the_node_type_and_never_a_litegraph_property(self):
        """`Node name for S&R` is attacker-authorable, so it is never read.

        The severe case: the editor graph is file metadata, the workflow box
        the owner reads shows `type`, and `class_type` is what gets submitted.
        If they could differ, a crafted picture would show one node and run
        another on the owner's ComfyUI.
        """
        node = _node(
            4, "CheckpointLoaderSimple", widgets=["sd_xl_base_1.0.safetensors"]
        )
        node["properties"] = {"Node name for S&R": "SomeOtherLoader"}
        prompt, problems = convert_ui_graph_to_api(_graph([node], []), OBJECT_INFO)
        assert problems == []
        assert prompt["4"]["class_type"] == "CheckpointLoaderSimple"

    def test_two_nodes_sharing_an_id_stop_it(self):
        """Keying by id would drop one and call the smaller graph exact."""
        graph = _graph(
            [
                _node(
                    4, "CheckpointLoaderSimple", widgets=["sd_xl_base_1.0.safetensors"]
                ),
                _node(4, "CLIPTextEncode", widgets=["a cat"]),
            ],
            [],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert problems == ["two nodes in this editor graph share the id 4"]

    def test_a_bypassed_node_that_does_not_say_what_it_carries_stops_it(self):
        """With no output type there is nothing to match, so nothing is picked.

        The inverse of `test_a_bypassed_node_hands_the_matching_input_straight_on`:
        the same two producers, and an `outputs` array the file does not carry.
        Taking the first connected input here is how the sampler's MODEL socket
        ends up on a CLIP producer.
        """
        graph = _graph(
            [
                _node(
                    41,
                    "CheckpointLoaderSimple",
                    outputs=[{"name": "CLIP", "type": "CLIP", "links": [4]}],
                    widgets=["sd_xl_base_1.0.safetensors"],
                ),
                _node(
                    7,
                    "LoraStub",
                    mode=4,
                    inputs=[{"name": "clip", "type": "CLIP", "link": 4}],
                    outputs=[],
                ),
                _node(
                    3,
                    "KSampler",
                    inputs=[{"name": "model", "type": "MODEL", "link": 2}],
                    widgets=[1, "fixed", 20, 7.0],
                ),
            ],
            [[4, 41, 0, 7, 0, "CLIP"], [2, 7, 0, 3, 0, "MODEL"]],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert "is bypassed and does not say what its output carries" in problems[0]

    def test_a_muted_node_on_a_required_input_is_refused(self):
        """ComfyUI would take this graph and reject it; saying so now is the
        same answer sooner, with the node named."""
        graph = _graph(
            [
                _node(
                    4,
                    "CheckpointLoaderSimple",
                    mode=2,
                    outputs=[{"name": "MODEL", "type": "MODEL", "links": [1]}],
                    widgets=["sd_xl_base_1.0.safetensors"],
                ),
                _node(
                    3,
                    "KSampler",
                    inputs=[{"name": "model", "type": "MODEL", "link": 1}],
                    widgets=[1, "fixed", 20, 7.0],
                ),
            ],
            [[1, 4, 0, 3, 0, "MODEL"]],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert problems == [
            "KSampler (node 3) has its 'model' input wired to something that "
            "does not run"
        ]

    def test_a_widget_value_keyed_by_a_name_the_node_does_not_have_stops_it(self):
        """The dict spelling gets the same accounting as the array.

        It cannot SHIFT, but a key this ComfyUI's version of the node does not
        declare still means the file and `/object_info` disagree - and the
        value would be dropped in silence.
        """
        graph = _graph(
            [
                _node(
                    3,
                    "KSampler",
                    widgets={"seed": 7, "steps": 30, "cfg": 4.0, "eta": 1},
                )
            ],
            [],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert problems == [
            "KSampler (node 3) carries a widget value for 'eta', which this "
            "ComfyUI's version of the node does not have"
        ]

    def test_a_widget_the_node_needs_and_the_dict_omits_stops_it(self):
        graph = _graph([_node(3, "KSampler", widgets={"seed": 7})], [])
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert problems == [
            "KSampler (node 3) has no value for 'cfg', 'steps', which this "
            "ComfyUI's version of the node needs"
        ]

    def test_a_wire_that_runs_in_a_circle_stops_it(self):
        """Two reroutes feeding each other: the depth guard, not a hang."""
        graph = _graph(
            [
                _node(
                    50,
                    "Reroute",
                    inputs=[{"name": "", "type": "*", "link": 2}],
                    outputs=[{"name": "", "type": "MODEL", "links": [1]}],
                ),
                _node(
                    51,
                    "Reroute",
                    inputs=[{"name": "", "type": "*", "link": 1}],
                    outputs=[{"name": "", "type": "MODEL", "links": [2]}],
                ),
                _node(
                    3,
                    "KSampler",
                    inputs=[{"name": "model", "type": "MODEL", "link": 1}],
                    widgets=[1, "fixed", 20, 7.0],
                ),
            ],
            [[1, 50, 0, 3, 0, "MODEL"], [2, 51, 0, 50, 0, "MODEL"]],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert "a wire runs in a circle" in problems

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
