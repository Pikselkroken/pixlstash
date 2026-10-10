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

from copy import deepcopy

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

    def test_a_socket_the_file_never_wired_is_simply_absent(self, simple_editor_graph):
        """Drawn and unwired, and `negative` has no socket at all.

        **`clip` here is a REQUIRED input, and that is deliberate.** An input
        the file never wired is rebuilt faithfully by leaving it out - the file
        really does say nothing feeds it - so refusing would refuse an exact
        rebuild. That is the opposite of a required input wired to a MUTED
        node, which the file says IS fed and the rebuild cannot reproduce; that
        one is refused by name. ComfyUI rejects this graph with a per-node
        error naming the socket, which is a better message than any this could
        invent. An earlier name for this test said "optional", which the
        fixture never was.
        """
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

    def test_a_bypass_splices_the_input_on_the_slot_being_read(self):
        """ComfyUI's rule, so the rebuild and the topology key pick one wire.

        Two MODEL inputs and the sampler reads the SECOND output: ComfyUI
        passes the second input through. "The first connected input of the
        right type" wires the other model and reports an exact rebuild (#1440).
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
                    42,
                    "CheckpointLoaderSimple",
                    outputs=[{"name": "MODEL", "type": "MODEL", "links": [5]}],
                    widgets=["sd_xl_base_1.0.safetensors"],
                ),
                _node(
                    7,
                    "LoraStub",
                    mode=4,
                    inputs=[
                        {"name": "model_a", "type": "MODEL", "link": 1},
                        {"name": "model_b", "type": "MODEL", "link": 5},
                    ],
                    outputs=[
                        {"name": "MODEL_A", "type": "MODEL", "links": []},
                        {"name": "MODEL_B", "type": "MODEL", "links": [2]},
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
                [1, 4, 0, 7, 0, "MODEL"],
                [5, 42, 0, 7, 1, "MODEL"],
                [2, 7, 1, 3, 0, "MODEL"],
            ],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert problems == []
        assert prompt["3"]["inputs"]["model"] == ["42", 0]

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

    def test_an_optional_widget_takes_its_slot_after_every_required_one(self):
        """Required first, optional second - the order `/object_info` gives.

        The fixtures' only optional input was a link, so nothing exercised an
        optional WIDGET holding a position; getting that order wrong shifts a
        node exactly as a miscounted extra widget does.
        """
        object_info = dict(OBJECT_INFO)
        object_info["WithAnOptionalWidget"] = {
            "input": {
                "required": {"name": ["STRING", {}]},
                "optional": {"scale": ["FLOAT", {}]},
            },
            "input_order": {"required": ["name"], "optional": ["scale"]},
        }
        graph = _graph(
            [_node(12, "WithAnOptionalWidget", widgets=["a name", 0.75])], []
        )
        prompt, problems = convert_ui_graph_to_api(graph, object_info)
        assert problems == []
        assert prompt["12"]["inputs"] == {"name": "a name", "scale": 0.75}

    def test_a_bypassed_node_whose_only_wire_is_the_wrong_type_stops_it(self):
        """The guard `_chase_through`'s docstring exists to justify.

        One connected input, of a type the asked-for output does not carry.
        "First connected input" would splice it and report an exact rebuild.
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
                    outputs=[{"name": "MODEL", "type": "MODEL", "links": [2]}],
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
        assert problems == [
            "KSampler (node 3) has its 'model' input wired to something that "
            "does not run"
        ]

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

    def test_a_getnode_fetches_what_its_setnode_was_given(self):
        """KJNodes `SetNode`/`GetNode` are a wire drawn in two halves.

        A `GetNode` has no input of its own, so the ordinary chase finds
        nothing and the sampler below would be left with no model at all. The
        editor pairs them by the name in their widget, and so does this.
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
                    20,
                    "SetNode",
                    inputs=[{"name": "MODEL", "type": "MODEL", "link": 1}],
                    outputs=[],
                    widgets=["the_model"],
                ),
                _node(
                    21,
                    "GetNode",
                    inputs=[],
                    outputs=[{"name": "MODEL", "type": "MODEL", "links": [2]}],
                    widgets=["the_model"],
                ),
                _node(
                    3,
                    "KSampler",
                    inputs=[{"name": "model", "type": "MODEL", "link": 2}],
                    widgets=[1, "fixed", 20, 7.0],
                ),
            ],
            [[1, 4, 0, 20, 0, "MODEL"], [2, 21, 0, 3, 0, "MODEL"]],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert problems == []
        # Neither half is a node ComfyUI would run.
        assert set(prompt) == {"4", "3"}
        assert prompt["3"]["inputs"]["model"] == ["4", 0]

    def test_a_getnode_that_pairs_with_nothing_stops_it(self):
        """Not "drop the edge": that is a graph with no model, run anyway."""
        graph = _graph(
            [
                _node(
                    21,
                    "GetNode",
                    outputs=[{"name": "MODEL", "type": "MODEL", "links": [2]}],
                    widgets=["never_set"],
                ),
                _node(
                    3,
                    "KSampler",
                    inputs=[{"name": "model", "type": "MODEL", "link": 2}],
                    widgets=[1, "fixed", 20, 7.0],
                ),
            ],
            [[2, 21, 0, 3, 0, "MODEL"]],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert (
            "fetches 'never_set', which this editor graph files under 0"
            in (problems[0])
        )

    def test_a_setnode_with_nothing_wired_into_it_stops_it(self):
        """The name pairs, and there is still nothing behind it.

        Separate from the unpaired case: here the `SetNode` exists and is
        unique, so a converter that stopped at "one match" would go on to
        resolve an empty input list to nothing and drop the edge in silence.
        """
        graph = _graph(
            [
                _node(20, "SetNode", inputs=[], outputs=[], widgets=["the_model"]),
                _node(
                    21,
                    "GetNode",
                    outputs=[{"name": "MODEL", "type": "MODEL", "links": [2]}],
                    widgets=["the_model"],
                ),
                _node(
                    3,
                    "KSampler",
                    inputs=[{"name": "model", "type": "MODEL", "link": 2}],
                    widgets=[1, "fixed", 20, 7.0],
                ),
            ],
            [[2, 21, 0, 3, 0, "MODEL"]],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert (
            "the node filing 'the_model' has 0 wires into it, so there is no "
            "one thing to fetch" in problems
        )

    def test_two_setnodes_under_one_name_stop_it(self):
        """First-wins would pick one of two wires and report an exact rebuild."""
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
                    outputs=[{"name": "MODEL", "type": "MODEL", "links": [3]}],
                    widgets=["sd_xl_base_1.0.safetensors"],
                ),
                _node(
                    20,
                    "SetNode",
                    inputs=[{"name": "MODEL", "type": "MODEL", "link": 1}],
                    widgets=["the_model"],
                ),
                _node(
                    22,
                    "SetNode",
                    inputs=[{"name": "MODEL", "type": "MODEL", "link": 3}],
                    widgets=["the_model"],
                ),
                _node(
                    21,
                    "GetNode",
                    outputs=[{"name": "MODEL", "type": "MODEL", "links": [2]}],
                    widgets=["the_model"],
                ),
                _node(
                    3,
                    "KSampler",
                    inputs=[{"name": "model", "type": "MODEL", "link": 2}],
                    widgets=[1, "fixed", 20, 7.0],
                ),
            ],
            [
                [1, 4, 0, 20, 0, "MODEL"],
                [3, 41, 0, 22, 0, "MODEL"],
                [2, 21, 0, 3, 0, "MODEL"],
            ],
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert "files under 2 matching nodes" in problems[0]

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

    def test_a_value_of_the_wrong_type_stops_it_where_it_lands(self):
        """The check the arithmetic one cannot make.

        A pack that DROPS one widget and ADDS another leaves the count
        unchanged, so counting alone converts clean and every value after the
        change lands on the wrong input. Here the string reaches `steps`, which
        takes an INT, and that is caught at the slot rather than at the total.
        """
        graph = _graph(
            [_node(3, "KSampler", widgets=[1, "fixed", "extra", 20, 7.5])], []
        )
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert problems == [
            "KSampler (node 3) has 'extra' where its 'steps' input takes a "
            "INT, so its widget values do not line up with the node this "
            "ComfyUI has"
        ]

    def test_an_unexplained_widget_value_stops_it(self):
        """One value too many means every later value in the node is suspect.

        Every value here is of a type its input could hold, so the total is
        the only thing left to notice - which is why both checks exist.
        """
        graph = _graph([_node(3, "KSampler", widgets=[1, "fixed", 20, 7.5, 99])], [])
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert prompt is None
        assert problems == [
            "KSampler (node 3) carries 5 widget values, and its inputs account for 4"
        ]

    def test_a_same_type_swap_is_the_residual_this_cannot_catch(self):
        """Stated as a test so the limit is written down, not implied.

        `steps` and a hypothetical second INT trading places keeps the count
        and every type, and a positional array carries no names to tell them
        apart. Nothing here claims otherwise.
        """
        graph = _graph([_node(3, "KSampler", widgets=[1, "fixed", 7, 20.0])], [])
        prompt, problems = convert_ui_graph_to_api(graph, OBJECT_INFO)
        assert problems == []
        # Read as declared, which is all the file allows.
        assert prompt["3"]["inputs"]["steps"] == 7
        assert prompt["3"]["inputs"]["cfg"] == 20.0

    def test_it_refuses_without_object_info(self):
        """ComfyUI unreachable is a refusal, not a licence to guess the order."""
        prompt, problems = convert_ui_graph_to_api(_graph([], []), None)
        assert prompt is None
        assert problems == ["PixlStash could not ask ComfyUI which nodes it has"]

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


def _node_id_a_list(graph, info):
    graph["nodes"][0]["id"] = [4]


def _inputs_a_number(graph, info):
    graph["nodes"][2]["inputs"] = 1


def _node_spec_a_string(graph, info):
    info["KSampler"] = "not a spec"


@pytest.mark.parametrize(
    "corrupt", [_node_id_a_list, _inputs_a_number, _node_spec_a_string]
)
def test_a_malformed_graph_is_refused_not_raised(simple_editor_graph, corrupt):
    """An imported file or a picture's chunk can hold any shape; Run must not 500."""
    info = {cls: dict(spec) for cls, spec in OBJECT_INFO.items()}
    corrupt(simple_editor_graph, info)
    assert convert_ui_graph_to_api(simple_editor_graph, info) == (
        None,
        ["this editor graph is malformed and cannot be read"],
    )


# ---------------------------------------------------------------------------
# Subgraphs: expanded into the graph, then converted as usual
# ---------------------------------------------------------------------------

SUBGRAPH = "8a6b5c4d-1e2f-4a3b-9c8d-7e6f5a4b3c2d"
INNER = "1b2c3d4e-5f6a-4b7c-8d9e-0f1a2b3c4d5e"

SUBGRAPH_OBJECT_INFO = {
    **OBJECT_INFO,
    "Scale": {
        "input": {"required": {"image": ["IMAGE", {}], "factor": ["FLOAT", {}]}},
        "input_order": {"required": ["image", "factor"]},
    },
    "FloatSource": {"input": {"required": {}}, "input_order": {"required": []}},
}


def _definition(definition_id=SUBGRAPH, *, inner_nodes=None, inner_links=None):
    """A Scale wrapped in a subgraph, in the shape ComfyUI writes one.

    Inside a definition links are objects and the two boundaries are nodes
    -10 (its inputs) and -20 (its outputs), matched to the instance BY NAME.
    """
    return {
        "id": definition_id,
        "inputs": [
            {"name": "image", "type": "IMAGE"},
            {"name": "factor", "type": "FLOAT"},
        ],
        "outputs": [{"name": "IMAGE", "type": "IMAGE"}],
        "nodes": inner_nodes
        or [
            _node(
                5,
                "Scale",
                inputs=[
                    {"name": "image", "type": "IMAGE", "link": 1},
                    {
                        "name": "factor",
                        "type": "FLOAT",
                        "link": 2,
                        "widget": {"name": "factor"},
                    },
                ],
                outputs=[{"name": "IMAGE", "type": "IMAGE", "links": [3]}],
                widgets=[1.5],
            )
        ],
        "links": inner_links
        or [
            {
                "id": 1,
                "origin_id": -10,
                "origin_slot": 0,
                "target_id": 5,
                "target_slot": 0,
                "type": "IMAGE",
            },
            {
                "id": 2,
                "origin_id": -10,
                "origin_slot": 1,
                "target_id": 5,
                "target_slot": 1,
                "type": "FLOAT",
            },
            {
                "id": 3,
                "origin_id": 5,
                "origin_slot": 0,
                "target_id": -20,
                "target_slot": 0,
                "type": "IMAGE",
            },
        ],
    }


def _subgraph_workflow(
    *, instance_values=None, factor_link=None, definitions=None, mode=0
):
    """LoadImage -> [subgraph: Scale] -> SaveImage."""
    nodes = [
        _node(
            1,
            "LoadImage",
            outputs=[{"name": "IMAGE", "type": "IMAGE", "links": [10]}],
            widgets=["a.png", "image"],
        ),
        {
            "id": 2,
            "type": SUBGRAPH,
            "mode": mode,
            # The instance lists its inputs in its own order, not the
            # definition's: matched by name, never by position.
            "inputs": [
                {
                    "name": "factor",
                    "type": "FLOAT",
                    "link": factor_link,
                    "widget": {"name": "factor"},
                },
                {"name": "image", "type": "IMAGE", "link": 10},
            ],
            "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [11]}],
            "widgets_values": [] if instance_values is None else instance_values,
        },
        _node(
            3,
            "SaveImage",
            inputs=[{"name": "images", "type": "IMAGE", "link": 11}],
            widgets=["out"],
        ),
    ]
    links = [[10, 1, 0, 2, 1, "IMAGE"], [11, 2, 0, 3, 0, "IMAGE"]]
    if factor_link is not None:
        nodes.append(
            _node(
                4,
                "FloatSource",
                outputs=[{"name": "FLOAT", "type": "FLOAT", "links": [factor_link]}],
            )
        )
        links.append([factor_link, 4, 0, 2, 0, "FLOAT"])
    graph = _graph(nodes, links)
    graph["definitions"] = {"subgraphs": definitions or [_definition()]}
    return graph


class TestSubgraphs:
    def test_an_instance_is_expanded_and_its_wires_cross_the_boundary(self):
        prompt, problems = convert_ui_graph_to_api(
            _subgraph_workflow(), SUBGRAPH_OBJECT_INFO
        )
        assert problems == []
        # Keyed the way ComfyUI's own export keys an inner node.
        assert prompt["2:5"]["class_type"] == "Scale"
        assert prompt["2:5"]["inputs"]["image"] == ["1", 0]
        assert prompt["3"]["inputs"]["images"] == ["2:5", 0]
        assert SUBGRAPH not in {node["class_type"] for node in prompt.values()}

    def test_with_no_value_of_its_own_the_inner_node_keeps_its_value(self):
        """A promoted widget's value is kept on the inner node."""
        prompt, problems = convert_ui_graph_to_api(
            _subgraph_workflow(), SUBGRAPH_OBJECT_INFO
        )
        assert problems == []
        assert prompt["2:5"]["inputs"]["factor"] == 1.5

    def test_the_instances_own_value_replaces_the_definitions(self):
        """One value per definition input that feeds a widget, in slot order."""
        prompt, problems = convert_ui_graph_to_api(
            _subgraph_workflow(instance_values=[3.0]), SUBGRAPH_OBJECT_INFO
        )
        assert problems == []
        assert prompt["2:5"]["inputs"]["factor"] == 3.0

    def test_a_wire_into_the_instance_wins_over_its_value(self):
        prompt, problems = convert_ui_graph_to_api(
            _subgraph_workflow(instance_values=[3.0], factor_link=12),
            SUBGRAPH_OBJECT_INFO,
        )
        assert problems == []
        assert prompt["2:5"]["inputs"]["factor"] == ["4", 0]

    def test_the_instances_value_fills_a_widget_its_inner_file_lacks(self):
        """An inner node saved before its widget existed takes the instance's value."""
        graph = _subgraph_workflow(instance_values=[3.0])
        graph["definitions"]["subgraphs"][0]["nodes"][0]["widgets_values"] = []
        prompt, problems = convert_ui_graph_to_api(graph, SUBGRAPH_OBJECT_INFO)
        assert problems == []
        assert prompt["2:5"]["inputs"]["factor"] == 3.0

    def test_the_instances_value_is_matched_by_the_input_name(self):
        """A widget named apart from its input still takes the instance's value."""
        graph = _subgraph_workflow(instance_values=[3.0])
        inner = graph["definitions"]["subgraphs"][0]["nodes"][0]
        inner["inputs"][1]["widget"] = {"name": "factor_widget"}
        prompt, problems = convert_ui_graph_to_api(graph, SUBGRAPH_OBJECT_INFO)
        assert problems == []
        assert prompt["2:5"]["inputs"]["factor"] == 3.0

    def test_an_instance_value_for_an_unnamed_input_is_refused(self):
        """Not dropped: the inner node's own value is not what was set."""
        graph = _subgraph_workflow(instance_values=[3.0])
        inner = graph["definitions"]["subgraphs"][0]["nodes"][0]["inputs"][1]
        # Still a widget input, so the instance's value reaches it; no name.
        del inner["name"]
        inner["widget"] = {"type": "FLOAT"}
        prompt, problems = convert_ui_graph_to_api(graph, SUBGRAPH_OBJECT_INFO)
        assert prompt is None
        assert any("unnamed input fed by a subgraph" in p for p in problems)

    def test_a_class_named_like_a_uuid_but_not_one_is_a_node(self):
        """Only the 8-4-4-4-12 hex shape is read as a missing subgraph."""
        lookalike = "abcdefgh-ijkl-mnop-qrst-uvwxyz012345"
        info = {
            **SUBGRAPH_OBJECT_INFO,
            lookalike: SUBGRAPH_OBJECT_INFO["SaveImage"],
        }
        graph = _subgraph_workflow()
        graph["nodes"][2]["type"] = lookalike
        prompt, problems = convert_ui_graph_to_api(graph, info)
        assert problems == []
        assert prompt["3"]["class_type"] == lookalike

    def test_a_slot_the_definition_does_not_declare_takes_no_unnamed_wire(self):
        """No name to match by is no match, not the first nameless input."""
        graph = _subgraph_workflow(factor_link=12)
        graph["definitions"]["subgraphs"][0]["inputs"] = [
            {"name": "image", "type": "IMAGE"}
        ]
        del graph["nodes"][1]["inputs"][0]["name"]
        prompt, problems = convert_ui_graph_to_api(graph, SUBGRAPH_OBJECT_INFO)
        assert problems == []
        assert prompt["2:5"]["inputs"]["factor"] == 1.5

    def test_a_subgraph_inside_a_subgraph_is_expanded_too(self):
        inner = _definition(INNER)
        outer = _definition(
            SUBGRAPH,
            inner_nodes=[
                {
                    "id": 7,
                    "type": INNER,
                    "mode": 0,
                    "inputs": [
                        {"name": "image", "type": "IMAGE", "link": 1},
                        {
                            "name": "factor",
                            "type": "FLOAT",
                            "link": 2,
                            "widget": {"name": "factor"},
                        },
                    ],
                    "outputs": [{"name": "IMAGE", "type": "IMAGE", "links": [3]}],
                    "widgets_values": [],
                }
            ],
            inner_links=[
                {
                    "id": 1,
                    "origin_id": -10,
                    "origin_slot": 0,
                    "target_id": 7,
                    "target_slot": 0,
                    "type": "IMAGE",
                },
                {
                    "id": 2,
                    "origin_id": -10,
                    "origin_slot": 1,
                    "target_id": 7,
                    "target_slot": 1,
                    "type": "FLOAT",
                },
                {
                    "id": 3,
                    "origin_id": 7,
                    "origin_slot": 0,
                    "target_id": -20,
                    "target_slot": 0,
                    "type": "IMAGE",
                },
            ],
        )
        prompt, problems = convert_ui_graph_to_api(
            _subgraph_workflow(instance_values=[2.5], definitions=[outer, inner]),
            SUBGRAPH_OBJECT_INFO,
        )
        assert problems == []
        assert prompt["2:7:5"]["inputs"]["image"] == ["1", 0]
        # The outermost instance's value reaches the innermost widget.
        assert prompt["2:7:5"]["inputs"]["factor"] == 2.5
        assert prompt["3"]["inputs"]["images"] == ["2:7:5", 0]

    def test_a_bypassed_instance_is_spliced_out_not_expanded(self):
        prompt, problems = convert_ui_graph_to_api(
            _subgraph_workflow(mode=4), SUBGRAPH_OBJECT_INFO
        )
        assert problems == []
        assert "2:5" not in prompt
        assert prompt["3"]["inputs"]["images"] == ["1", 0]

    def test_a_missing_definition_is_refused_by_name(self):
        """Refused for what it is, not as "no node class '<uuid>'"."""
        graph = _subgraph_workflow()
        graph["definitions"] = {"subgraphs": [_definition(INNER)]}
        prompt, problems = convert_ui_graph_to_api(graph, SUBGRAPH_OBJECT_INFO)
        assert prompt is None
        assert any(
            "subgraph whose definition is not in the file" in p for p in problems
        )

    def test_values_that_cannot_be_matched_to_inputs_are_refused(self):
        prompt, problems = convert_ui_graph_to_api(
            _subgraph_workflow(instance_values=[3.0, 4.0]), SUBGRAPH_OBJECT_INFO
        )
        assert prompt is None
        assert any("cannot be matched up" in p for p in problems)


# ---------------------------------------------------------------------------
# V3 dynamic combos: the chosen option's own inputs follow the combo
# ---------------------------------------------------------------------------

DYNAMIC_OBJECT_INFO = {
    **OBJECT_INFO,
    # The shape ComfyUI 0.38's TextGenerate declares, cut down.
    "TextGen": {
        "input": {
            "required": {
                "max_length": ["INT", {}],
                "sampling_mode": [
                    "COMFY_DYNAMICCOMBO_V3",
                    {
                        "options": [
                            {
                                "key": "on",
                                "inputs": {
                                    "required": {
                                        "temperature": ["FLOAT", {}],
                                        "top_k": ["INT", {}],
                                    },
                                    "optional": {"presence_penalty": ["FLOAT", {}]},
                                },
                            },
                            {"key": "off", "inputs": {"required": {}}},
                        ]
                    },
                ],
            },
            "optional": {"use_default_template": ["BOOLEAN", {}]},
        },
        "input_order": {
            "required": ["max_length", "sampling_mode"],
            "optional": ["use_default_template"],
        },
    },
}


def _textgen(values):
    return _graph([_node(1, "TextGen", widgets=values)], [])


class TestDynamicCombos:
    def test_the_chosen_options_inputs_take_the_values_after_it(self):
        prompt, problems = convert_ui_graph_to_api(
            _textgen([256, "on", 0.7, 64, 0.0, True]), DYNAMIC_OBJECT_INFO
        )
        assert problems == []
        # Named as ComfyUI's server reads them: `<combo>.<input>`.
        assert prompt["1"]["inputs"] == {
            "max_length": 256,
            "sampling_mode": "on",
            "sampling_mode.temperature": 0.7,
            "sampling_mode.top_k": 64,
            "sampling_mode.presence_penalty": 0.0,
            "use_default_template": True,
        }

    def test_an_option_with_no_inputs_takes_no_values(self):
        prompt, problems = convert_ui_graph_to_api(
            _textgen([256, "off", False]), DYNAMIC_OBJECT_INFO
        )
        assert problems == []
        assert prompt["1"]["inputs"] == {
            "max_length": 256,
            "sampling_mode": "off",
            "use_default_template": False,
        }

    def test_an_option_this_comfyui_does_not_offer_is_refused_by_name(self):
        prompt, problems = convert_ui_graph_to_api(
            _textgen([256, "turbo", 1.0, True]), DYNAMIC_OBJECT_INFO
        )
        assert prompt is None
        assert any("'turbo' for its 'sampling_mode'" in p for p in problems)

    def test_a_combo_with_no_value_expands_no_option(self):
        """A null choice must not pick an option that has no key."""
        info = deepcopy(DYNAMIC_OBJECT_INFO)
        info["TextGen"]["input"]["required"]["sampling_mode"][1]["options"].insert(
            0, {"inputs": {"required": {"bogus": ["INT", {}]}}}
        )
        prompt, problems = convert_ui_graph_to_api(
            _textgen([256, None, 5, False]), info
        )
        assert prompt is None
        assert any("no value for its 'sampling_mode' choice" in p for p in problems)

    def test_values_keyed_by_name_use_the_prefixed_names(self):
        graph = _textgen(
            {
                "max_length": 256,
                "sampling_mode": "on",
                "sampling_mode.temperature": 0.5,
                "sampling_mode.top_k": 10,
                "sampling_mode.presence_penalty": 0.1,
                "use_default_template": True,
            }
        )
        prompt, problems = convert_ui_graph_to_api(graph, DYNAMIC_OBJECT_INFO)
        assert problems == []
        assert prompt["1"]["inputs"]["sampling_mode.temperature"] == 0.5


# ---------------------------------------------------------------------------
# Every wire is sent, as the editor sends it, whatever the class declares
# ---------------------------------------------------------------------------

WIRED_OBJECT_INFO = {
    **OBJECT_INFO,
    # ComfyUI's Math Expression as `/object_info` declares it, names cut down:
    # a V3 autogrow input, whose sockets are `values.a`, `values.b`, ...
    "ComfyMathExpression": {
        "input": {
            "required": {
                "expression": ["STRING", {"default": "a + b", "multiline": True}],
                "values": [
                    "COMFY_AUTOGROW_V3",
                    {
                        "template": {
                            "input": {"required": {"value": ["FLOAT,INT,BOOLEAN", {}]}},
                            "names": ["a", "b", "c"],
                            "min": 1,
                        }
                    },
                ],
            }
        },
    },
    "PrimitiveFloat": {"input": {"required": {"value": ["FLOAT", {}]}}},
}


def _seconds(mode=0):
    return _node(
        2,
        "PrimitiveFloat",
        mode=mode,
        outputs=[{"name": "FLOAT", "type": "FLOAT", "links": [1]}],
        widgets=[5],
    )


def _expression():
    """A Math Expression with its `a` wired to the primitive, as saved."""
    return _node(
        1,
        "ComfyMathExpression",
        inputs=[
            {"name": "values.a", "type": "FLOAT,INT,BOOLEAN", "link": 1},
            {"name": "values.b", "type": "FLOAT,INT,BOOLEAN", "link": None},
            {"name": "expression", "type": "STRING", "link": None},
        ],
        widgets=["a * 24"],
    )


class TestEveryWireIsSent:
    def test_a_socket_the_class_grows_is_sent_under_its_own_name(self):
        """A video's length is `seconds * fps` in a Math Expression; dropping
        the wire left ComfyUI refusing the run over a missing `a`."""
        graph = _graph([_expression(), _seconds()], [[1, 2, 0, 1, 0, "FLOAT"]])
        prompt, problems = convert_ui_graph_to_api(graph, WIRED_OBJECT_INFO)
        assert problems == []
        assert prompt["1"]["inputs"] == {"expression": "a * 24", "values.a": ["2", 0]}

    def test_a_wire_into_a_socket_the_class_never_declared_is_sent(self):
        # The editor queues `inputs[socket.name]` for every wire and consults
        # no declaration, so neither does this.
        graph = _graph(
            [
                _node(
                    1,
                    "PrimitiveFloat",
                    inputs=[{"name": "from_a_newer_pack", "type": "FLOAT", "link": 1}],
                    widgets=[3],
                ),
                _seconds(),
            ],
            [[1, 2, 0, 1, 0, "FLOAT"]],
        )
        prompt, problems = convert_ui_graph_to_api(graph, WIRED_OBJECT_INFO)
        assert problems == []
        assert prompt["1"]["inputs"] == {"value": 3, "from_a_newer_pack": ["2", 0]}

    def test_such_a_wire_from_a_muted_node_is_absent_as_the_editor_leaves_it(self):
        graph = _graph([_expression(), _seconds(mode=2)], [[1, 2, 0, 1, 0, "FLOAT"]])
        prompt, problems = convert_ui_graph_to_api(graph, WIRED_OBJECT_INFO)
        assert problems == []
        assert prompt["1"]["inputs"] == {"expression": "a * 24"}


# ---------------------------------------------------------------------------
# A widget the node gained after the file was saved gets its default
# ---------------------------------------------------------------------------

GROWN_OBJECT_INFO = {
    **OBJECT_INFO,
    "ShiftModel": {
        "input": {
            "required": {"model": ["MODEL", {}], "shift": ["FLOAT", {}]},
            # Added in a later ComfyUI: older files carry one value, not two.
            "optional": {
                "sampling": [["flow", "img_to_img_velocity"], {"default": "flow"}],
                "mode": [["a", "b"], {}],
            },
        },
        "input_order": {
            "required": ["model", "shift"],
            "optional": ["sampling", "mode"],
        },
    },
    "NoDefault": {
        "input": {"required": {"a": ["FLOAT", {}], "b": ["FLOAT", {}]}},
        "input_order": {"required": ["a", "b"]},
    },
}


class TestWidgetsAddedSinceTheFileWasSaved:
    def test_missing_trailing_widgets_take_their_defaults_as_comfyui_loads_them(self):
        graph = _graph([_node(1, "ShiftModel", widgets=[3.0])], [])
        prompt, problems = convert_ui_graph_to_api(graph, GROWN_OBJECT_INFO)
        assert problems == []
        # The declared default, else a combo's first choice.
        assert prompt["1"]["inputs"] == {
            "shift": 3.0,
            "sampling": "flow",
            "mode": "a",
        }

    def test_a_missing_widget_with_no_default_is_refused_by_name(self):
        graph = _graph([_node(1, "NoDefault", widgets=[1.0])], [])
        prompt, problems = convert_ui_graph_to_api(graph, GROWN_OBJECT_INFO)
        assert prompt is None
        assert any("no value for 'b'" in p for p in problems)

    def test_more_values_than_widgets_is_still_refused(self):
        """A removed or moved widget: which value is whose cannot be told."""
        graph = _graph([_node(1, "NoDefault", widgets=[1.0, 2.0, 3.0])], [])
        prompt, problems = convert_ui_graph_to_api(graph, GROWN_OBJECT_INFO)
        assert prompt is None
        assert any("carries 3 widget values" in p for p in problems)
