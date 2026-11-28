"""The Studio (#2819): scene shots, the scene routes, takes and items."""

import gc
import json
import os
import tempfile
import uuid
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pixlstash.db_models import Picture
from pixlstash.db_models.studio import Scene
from pixlstash.server import Server
from pixlstash.services import comfyui_service, location_angles
from pixlstash.services.studio import (
    Item,
    Person,
    TilePicture,
    compose_shot,
    place_words,
    sheet_tile,
    sheet_tiles,
)
from tests.utils import (
    API_PREFIX,
    bearer_client,
    mint_read_token,
    upload_pictures_and_wait,
)

STREET = Item("Neon rain street", "a neon-lit street, wet asphalt", [10, 11, 12, 13])


def _scene(**fields) -> Scene:
    base = dict(
        id=1,
        name="Rain at the taxi rank",
        camera={"shot": "medium", "view": 0},
        setting={
            "weather": "heavy rain",
            "time": "night",
            "happening": "a taxi pulling up",
        },
        cast=[],
        props=[],
    )
    base.update(fields)
    return Scene(**base)


def _three_people():
    people = {
        1: Person("Mira", "a woman with short red hair", [100], ["lora-mira"]),
        2: Person("Juno", "a tall man with a beard", [200]),
        3: Person("Theo", "a young man with glasses", [300]),
    }
    outfits = {
        21: Item("Mustard raincoat", "a mustard-yellow raincoat", [41, 42, 43, 44]),
        22: Item("Cobalt raincoat", "a cobalt-blue raincoat", [51, 52, 53, 54]),
        23: Item("Teal suit", "a teal wool suit", [61, 62, 63, 64]),
    }
    cast = [
        {
            "character_id": 1,
            "outfit_set_id": 21,
            "doing": "hailing the taxi",
            "pose": "turned ¾ to the left",
            "brings": "refs",
            "box": [0.05, 0.2, 0.2, 0.7],
        },
        {
            "character_id": 2,
            "outfit_set_id": 22,
            "doing": "laughing",
            "pose": "",
            "brings": "lora",
            "box": [0.4, 0.2, 0.2, 0.7],
        },
        {
            "character_id": 3,
            "outfit_set_id": 23,
            "doing": "waiting",
            "pose": "",
            "brings": "text",
            "box": [0.75, 0.5, 0.1, 0.2],
        },
    ]
    return people, outfits, cast


def test_a_three_person_shot_names_everyone_in_their_outfit_with_the_prop_held():
    people, outfits, cast = _three_people()
    umbrella = {31: Item("Red umbrella", "a red stick umbrella", [71, 72, 73, 74])}
    scene = _scene(
        cast=cast,
        props=[{"set_id": 31, "state": "open", "held_by": 2, "box": None}],
    )
    shot = compose_shot(
        scene, location=STREET, people=people, outfits=outfits, props=umbrella
    )
    prompt = shot["prompt"]
    # The location's Wide view is picture 1; Mira's reference and her ¾ angle
    # follow; Juno brings his LoRA (none attached), Theo his words.
    assert shot["references"][:3] == [10, 100, 42]
    assert shot["references"] == [10, 100, 42, 51, 61, 71]
    assert "a neon-lit street, wet asphalt (picture 1)" in prompt
    assert "3 people" in prompt
    assert (
        "On the left in the foreground, Mira, a woman with short red hair (the person in picture 2)"
        in prompt
    )
    assert "wearing a mustard-yellow raincoat (picture 3), hailing the taxi" in prompt
    assert "Pose: turned ¾ to the left." in prompt
    assert (
        "In the centre in the foreground, Juno" in prompt
        and "wearing a cobalt-blue raincoat (picture 4)" in prompt
    )
    assert "On the right in the background, Theo" in prompt
    assert "A red stick umbrella (picture 6), open, held by Juno." in prompt
    assert "Heavy rain, night." in prompt
    assert shot["loras"] == []


def test_a_lora_cast_member_brings_their_lora_and_not_their_reference():
    people, outfits, cast = _three_people()
    cast[0]["brings"] = "lora"
    shot = compose_shot(
        _scene(cast=cast[:1]), location=STREET, people=people, outfits=outfits, props={}
    )
    assert shot["loras"] == ["lora-mira"]
    assert 100 not in shot["references"]


def test_the_reverse_view_takes_the_location_s_reverse_and_the_outfits_backs():
    people, outfits, cast = _three_people()
    scene = _scene(cast=cast, camera={"shot": "wide", "view": 1})
    shot = compose_shot(
        scene, location=STREET, people=people, outfits=outfits, props={}
    )
    assert shot["references"][0] == 11
    assert {44, 54, 64} <= set(shot["references"])
    assert "seen from its reverse view" in shot["prompt"]
    assert shot["prompt"].startswith("A wide shot")


def test_only_as_many_references_as_the_workflow_takes_are_named():
    people, outfits, cast = _three_people()
    shot = compose_shot(
        _scene(cast=cast),
        location=STREET,
        people=people,
        outfits=outfits,
        props={},
        max_references=2,
    )
    assert shot["references"] == [10, 100]
    assert "(picture 3)" not in shot["prompt"]
    # The words still carry what has no picture.
    assert "a teal wool suit" in shot["prompt"]


def test_a_clip_from_a_still_has_the_still_as_its_one_reference():
    people, outfits, cast = _three_people()
    shot = compose_shot(
        _scene(cast=cast),
        location=STREET,
        people=people,
        outfits=outfits,
        props={},
        kind="clip",
        from_picture_id=999,
    )
    assert shot["references"] == [999]
    assert shot["prompt"].startswith("Bring picture 1 to life as a short video clip")
    assert "(picture 2)" not in shot["prompt"]
    assert "from start to end" in shot["prompt"]


def test_a_clip_from_the_scene_is_a_clip_with_every_reference():
    people, outfits, cast = _three_people()
    shot = compose_shot(
        _scene(cast=cast),
        location=STREET,
        people=people,
        outfits=outfits,
        props={},
        kind="clip",
    )
    assert shot["prompt"].startswith("A short video clip, a medium shot")
    assert shot["references"][0] == 10


@pytest.mark.parametrize(
    "box, words",
    [
        ([0.0, 0.0, 0.2, 0.5], "on the left"),
        ([0.4, 0.0, 0.2, 0.5], "in the centre"),
        ([0.8, 0.0, 0.2, 0.7], "on the right in the foreground"),
        ([0.4, 0.0, 0.2, 0.2], "in the centre in the background"),
        (None, ""),
    ],
)
def test_a_box_is_placed_in_words(box, words):
    assert place_words(box) == words


# ── the routes ──────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def env():
    with tempfile.TemporaryDirectory() as temp_dir:
        os.makedirs(os.path.join(temp_dir, "images"), exist_ok=True)
        config = os.path.join(temp_dir, "server-config.json")
        with open(config, "w") as f:
            f.write(json.dumps({"port": 8000}))
        server = Server(config)
        try:
            client = TestClient(server.api)
            resp = client.post(
                "/login", json={"username": "testuser", "password": "example-password"}
            )
            assert resp.status_code == 200, resp.text
            yield SimpleNamespace(client=client, server=server, temp_dir=temp_dir)
        finally:
            server.close()
            gc.collect()


def _name(prefix: str) -> str:
    return f"{prefix} {uuid.uuid4().hex[:8]}"


def _set(client, **body) -> int:
    resp = client.post(
        f"{API_PREFIX}/picture_sets", json={"name": _name("Set"), **body}
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["picture_set"]["id"]


def _person(client) -> int:
    resp = client.post(f"{API_PREFIX}/characters", json={"name": _name("Person")})
    assert resp.status_code == 200, resp.text
    return resp.json()["character"]["id"]


def _project(client) -> int:
    resp = client.post(f"{API_PREFIX}/projects", json={"name": _name("Project")})
    assert resp.status_code == 200, resp.text
    return resp.json()["id"]


def _item(client, kind, **set_body) -> int:
    set_id = _set(client, **set_body)
    resp = client.put(f"{API_PREFIX}/studio/items/{set_id}", json={"kind": kind})
    assert resp.status_code == 200, resp.text
    return set_id


def test_a_scene_is_made_cast_and_listed_in_its_project(env):
    client = env.client
    project = _project(client)
    other = _project(client)
    location = _item(client, "location", project_ids=[project])
    person = _person(client)
    outfit = _item(client, "outfit", parent_character_id=person)
    prop = _item(client, "prop", project_ids=[project])
    resp = client.post(
        f"{API_PREFIX}/studio/scenes",
        json={"name": "Taxi rank", "project_id": project, "location_set_id": location},
    )
    assert resp.status_code == 201, resp.text
    scene = resp.json()
    resp = client.patch(
        f"{API_PREFIX}/studio/scenes/{scene['id']}",
        json={
            "cast": [
                {
                    "character_id": person,
                    "outfit_set_id": outfit,
                    "doing": "waving",
                    "box": [0.1, 0.1, 0.3, 0.8],
                }
            ],
            "props": [{"set_id": prop, "state": "open", "held_by": person}],
            "setting": {"weather": "rain"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["props"][0]["held_by"] == person

    listed = client.get(
        f"{API_PREFIX}/studio/overview", params={"project_id": project}
    ).json()
    assert scene["id"] in [s["id"] for s in listed["scenes"]]
    assert location in [s["id"] for s in listed["locations"]]
    assert prop in [s["id"] for s in listed["props"]]
    elsewhere = client.get(
        f"{API_PREFIX}/studio/overview", params={"project_id": other}
    ).json()
    assert scene["id"] not in [s["id"] for s in elsewhere["scenes"]]
    assert location not in [s["id"] for s in elsewhere["locations"]]
    everywhere = client.get(f"{API_PREFIX}/studio/overview").json()
    outfits = {o["id"]: o for o in everywhere["outfits"]}
    assert outfits[outfit]["character_id"] == person
    cast = {c["id"]: c for c in everywhere["cast"]}
    assert outfit in cast[person]["outfit_ids"]
    assert cast[person]["scene_count"] >= 1
    # Each card says every project it is in, for its Project menu.
    assert {s["id"]: s["project_ids"] for s in everywhere["scenes"]}[scene["id"]] == [
        project
    ]
    locations = {s["id"]: s for s in everywhere["locations"]}
    assert locations[location]["project_ids"] == [project]
    assert outfits[outfit]["project_ids"] == []
    client.patch(
        f"{API_PREFIX}/characters/{person}", json={"project_ids": [project, other]}
    )
    cast = {
        c["id"]: c for c in client.get(f"{API_PREFIX}/studio/overview").json()["cast"]
    }
    assert cast[person]["project_ids"] == sorted([project, other])

    shot = client.post(f"{API_PREFIX}/studio/scenes/{scene['id']}/shot", json={}).json()
    assert "waving" in shot["prompt"] and "held by" in shot["prompt"]


def test_a_prop_is_held_only_by_someone_in_the_cast(env):
    client = env.client
    scene = client.post(f"{API_PREFIX}/studio/scenes", json={"name": "Alone"}).json()
    prop = _item(client, "prop")
    stranger = _person(client)
    resp = client.patch(
        f"{API_PREFIX}/studio/scenes/{scene['id']}",
        json={"props": [{"set_id": prop, "held_by": stranger}]},
    )
    assert resp.status_code == 422, resp.text


def test_leaving_the_cast_puts_down_what_was_held(env):
    client = env.client
    person = _person(client)
    prop = _item(client, "prop")
    scene = client.post(f"{API_PREFIX}/studio/scenes", json={"name": "Hand-off"}).json()
    url = f"{API_PREFIX}/studio/scenes/{scene['id']}"
    client.patch(
        url,
        json={
            "cast": [{"character_id": person}],
            "props": [{"set_id": prop, "held_by": person}],
        },
    )
    resp = client.patch(url, json={"cast": []})
    assert resp.status_code == 200, resp.text
    assert resp.json()["props"] == [
        {"set_id": prop, "state": "", "held_by": None, "box": None}
    ]


def test_a_scene_whose_person_was_deleted_can_still_be_renamed(env):
    client = env.client
    person = _person(client)
    prop = _item(client, "prop")
    scene = client.post(f"{API_PREFIX}/studio/scenes", json={"name": "Gone"}).json()
    url = f"{API_PREFIX}/studio/scenes/{scene['id']}"
    client.patch(
        url,
        json={
            "cast": [{"character_id": person}],
            "props": [{"set_id": prop, "held_by": person}],
        },
    )
    assert client.delete(f"{API_PREFIX}/characters/{person}").status_code == 200
    resp = client.patch(url, json={"name": "Renamed"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["name"] == "Renamed"
    # The shot leaves out who is gone rather than failing.
    shot = client.post(f"{url}/shot", json={}).json()
    assert "Person" not in shot["prompt"]


def test_a_name_of_spaces_alone_is_no_name(env):
    client = env.client
    assert (
        client.post(f"{API_PREFIX}/studio/scenes", json={"name": "   "}).status_code
        == 422
    )
    scene = client.post(f"{API_PREFIX}/studio/scenes", json={"name": " Kept "}).json()
    assert scene["name"] == "Kept"
    url = f"{API_PREFIX}/studio/scenes/{scene['id']}"
    assert client.patch(url, json={"name": "  "}).status_code == 422
    assert client.get(url).json()["name"] == "Kept"


def test_a_scene_naming_a_missing_set_is_refused(env):
    resp = env.client.post(
        f"{API_PREFIX}/studio/scenes",
        json={"name": "Nowhere", "location_set_id": 987654},
    )
    assert resp.status_code == 404


def test_a_finished_run_becomes_takes_and_a_lost_one_is_said(env):
    client = env.client
    path = os.path.join(env.temp_dir, "take.png")
    Image.new("RGB", (64, 64), (200, 40, 40)).save(path)
    with open(path, "rb") as f:
        status = upload_pictures_and_wait(
            client, [("file", ("take.png", f.read(), "image/png"))]
        )
    assert status["status"] == "completed", status
    picture_id = status["results"][0]["picture_id"]
    scene = client.post(f"{API_PREFIX}/studio/scenes", json={"name": "Shot"}).json()
    url = f"{API_PREFIX}/studio/scenes/{scene['id']}"
    done, lost = f"run-{uuid.uuid4().hex}", f"run-{uuid.uuid4().hex}"
    resp = client.post(
        f"{url}/runs", json={"runs": [{"key": done}, {"key": lost, "kind": "clip"}]}
    )
    assert [r["key"] for r in resp.json()["runs"]] == [done, lost]
    comfyui_service.record_run_outcome(done, "running")
    still_running = client.get(url).json()
    assert [r["key"] for r in still_running["runs"]] == [done]
    assert [e["status"] for e in still_running["ended"]] == ["lost"]

    comfyui_service.record_run_outcome(done, "completed", picture_ids=[picture_id])
    filed = client.get(url).json()
    assert filed["runs"] == []
    assert [t["picture_id"] for t in filed["takes"]] == [picture_id]
    assert filed["takes"][0]["format"].lower() == "png"
    assert filed["ended"][0]["picture_count"] == 1

    # A picture already filed is not filed again, by another run or twice.
    again = f"run-{uuid.uuid4().hex}"
    client.post(f"{url}/runs", json={"runs": [{"key": again}]})
    comfyui_service.record_run_outcome(
        again, "completed", picture_ids=[picture_id, picture_id]
    )
    refiled = client.get(url).json()
    assert [t["picture_id"] for t in refiled["takes"]] == [picture_id]
    assert refiled["ended"][0]["picture_count"] == 0

    # Two runs ending in one read with the same picture file it once.
    other = client.post(f"{API_PREFIX}/studio/scenes", json={"name": "Twice"}).json()
    other_url = f"{API_PREFIX}/studio/scenes/{other['id']}"
    first, second = f"run-{uuid.uuid4().hex}", f"run-{uuid.uuid4().hex}"
    client.post(f"{other_url}/runs", json={"runs": [{"key": first}, {"key": second}]})
    for key in (first, second):
        comfyui_service.record_run_outcome(key, "completed", picture_ids=[picture_id])
    assert [t["picture_id"] for t in client.get(other_url).json()["takes"]] == [
        picture_id
    ]

    # A clip from that still must name one of the scene's takes.
    assert (
        client.post(
            f"{url}/shot", json={"kind": "clip", "from_picture_id": 424242}
        ).status_code
        == 404
    )
    clip = client.post(
        f"{url}/shot", json={"kind": "clip", "from_picture_id": picture_id}
    ).json()
    assert clip["references"] == [picture_id]

    taken_out = client.delete(f"{url}/takes/{picture_id}").json()
    assert taken_out["takes"] == []
    still_there = env.server.vault.db.run_immediate_read_task(
        lambda session: session.get(Picture, picture_id)
    )
    assert still_there is not None


def test_a_shoot_puts_its_scene_first_in_the_overview(env):
    client = env.client
    older = client.post(f"{API_PREFIX}/studio/scenes", json={"name": "Older"}).json()
    client.post(f"{API_PREFIX}/studio/scenes", json={"name": "Newer"})
    client.post(
        f"{API_PREFIX}/studio/scenes/{older['id']}/runs",
        json={"runs": [{"key": f"run-{uuid.uuid4().hex}"}]},
    )
    scenes = client.get(f"{API_PREFIX}/studio/overview").json()["scenes"]
    assert scenes[0]["id"] == older["id"]


def test_a_favourite_is_starred_and_unstarred(env):
    client = env.client
    scene = client.post(f"{API_PREFIX}/studio/scenes", json={"name": "Starred"}).json()
    body = {"kind": "scene", "item_id": scene["id"], "favourite": True}
    assert client.put(f"{API_PREFIX}/studio/favourites", json=body).status_code == 200
    assert (
        client.get(f"{API_PREFIX}/studio/scenes/{scene['id']}").json()["favourite"]
        is True
    )
    body["favourite"] = False
    client.put(f"{API_PREFIX}/studio/favourites", json=body)
    assert (
        client.get(f"{API_PREFIX}/studio/scenes/{scene['id']}").json()["favourite"]
        is False
    )


def test_a_read_token_reaches_none_of_the_studio(env):
    client = env.client
    scene = client.post(f"{API_PREFIX}/studio/scenes", json={"name": "Private"}).json()
    # The owner's own reads work, so the refusals below are the token's.
    assert client.get(f"{API_PREFIX}/studio/overview").status_code == 200
    assert client.get(f"{API_PREFIX}/studio/scenes/{scene['id']}").status_code == 200
    reader = bearer_client(env.server, mint_read_token(client, "studio check"))
    assert reader.get(f"{API_PREFIX}/studio/overview").status_code == 403
    assert reader.get(f"{API_PREFIX}/studio/scenes/{scene['id']}").status_code == 403
    assert (
        reader.post(f"{API_PREFIX}/studio/scenes", json={"name": "x"}).status_code
        == 403
    )


# ── More angles of one location ─────────────────────────────────────────────


def _shots_video(path, shots=4, seconds=2, fps=12, size=(160, 96), blur_first=8):
    """Static shots joined by hard cuts, each a distinct pattern; the first
    frames after each cut are blurred, so the sharpest is past the settle."""
    writer = cv2.VideoWriter(path, cv2.VideoWriter_fourcc(*"mp4v"), fps, size)
    for shot in range(shots):
        # Flat shapes on a flat ground, another layout each shot, so a cut
        # changes most pixels and the settle's blur only the edges.
        base = np.full(
            (size[1], size[0], 3), (40 + 50 * shot, 90, 200 - 40 * shot), np.uint8
        )
        for k in range(3):
            x = 10 + ((shot * 37 + k * 45) % (size[0] - 40))
            cv2.rectangle(
                base, (x, 15 + k * 22), (x + 30, 30 + k * 22), (255, 255, 255), -1
            )
        for frame in range(seconds * fps):
            image = cv2.GaussianBlur(base, (7, 7), 0) if frame < blur_first else base
            writer.write(image)
    writer.release()


def test_the_shots_prompt_has_the_picture_first_and_a_cut_per_shot():
    prompt = location_angles.location_shots_prompt(
        "a pub",
        "The room has exactly one open fireplace, on the left wall",
        [
            {"preset": "closer", "words": "the fireplace"},
            {"preset": "reverse", "words": ""},
            {"preset": "nonsense", "words": "the bar"},
        ],
    )
    assert prompt.startswith("Gimbal footage inside a pub, plain daylight, no people.")
    assert "exactly one open fireplace" in prompt
    assert "A sequence of 4 static shots" in prompt
    assert "SHOT 1: exactly <Picture 1>, held still." in prompt
    assert (
        "SHOT 2: cut to a static shot from closer in, looking at the fireplace."
        in prompt
    )
    # An empty row still names its move; an unknown preset is taken as closer.
    assert "looking back towards where the first shot's camera stands" in prompt
    assert "SHOT 4: cut to a static shot from closer in, looking at the bar." in prompt
    assert location_angles.shots_seconds(3) == 8


def test_every_cut_the_video_made_is_found_and_none_refuses():
    diffs = [0.3] * 47
    for at in (11, 23, 35):
        diffs[at] = 30.0
    diffs[24] = 28.0  # one cut spread over two frames is still one cut
    assert location_angles.find_cuts(diffs) == [12, 24, 36]
    # The model merged two shots: the cuts it did make are kept.
    diffs[23] = diffs[24] = 0.3
    assert location_angles.find_cuts(diffs) == [12, 36]
    with pytest.raises(location_angles.ShotsNotFound, match="no cut"):
        location_angles.find_cuts([0.3] * 47)


def test_the_sharpest_frame_of_each_new_shot_is_past_the_settle(tmp_path):
    path = str(tmp_path / "shots.mp4")
    _shots_video(path)
    picked = location_angles.sharpest_frames(path)
    assert len(picked) == 3
    for shot, index in enumerate(picked, start=1):
        # Each pick inside its own shot, after the blurred settle.
        assert shot * 24 + 8 <= index < (shot + 1) * 24


def test_the_picked_frame_is_steady_over_its_neighbours_and_near_the_middle():
    # A still shot, every frame as sharp: the middle wins, not an edge.
    still = location_angles.pick_frame(0, 48, [100.0] * 48, [0.05] * 48)
    assert 22 <= still <= 25
    # A pan with a one-frame dip at 12: the dip's neighbours still move, so
    # the steady stretch at 30-36 wins although no frame there is sharper.
    motion = [2.0] * 48
    motion[12] = 0.0
    for i in range(28, 39):
        motion[i] = 0.1
    assert 30 <= location_angles.pick_frame(0, 48, [100.0] * 48, motion) <= 36


def test_the_things_in_a_picture_are_offered_biggest_first_with_their_side():
    regions = [
        ("wooden chair", [1700, 600, 1800, 800], None),
        ("brick fireplace", [100, 300, 700, 1000], None),
        ("a pub with tables", [0, 0, 2560, 1440], None),  # the place itself
        ("Brick  Fireplace", [1000, 300, 1200, 600], None),  # said already
        ("long oak bar", [1100, 400, 1500, 900], None),
        # The room again after "in"; a description rather than a thing.
        ("wooden bar stools in dimly lit room", [2000, 800, 2300, 1100], None),
        ("restored wooden booth with leather seats and table", [0, 0, 900, 900], None),
    ]
    assert location_angles.thing_phrases(regions, 2560, 1440) == [
        "the brick fireplace on the left",
        "the long oak bar in the middle",
        "the wooden bar stools on the right",
        "the wooden chair on the right",
    ]
    many = [(f"thing {i}", [i, 0, i + 10, 10], None) for i in range(20)]
    assert (
        len(location_angles.thing_phrases(many, 2560, 1440))
        == location_angles.THINGS_MAX
    )


def test_a_restore_that_turns_back_into_the_original_is_refused():
    rng = np.random.default_rng(3)
    frame = Image.fromarray((rng.random((72, 128, 3)) * 255).astype(np.uint8))
    original = Image.fromarray((rng.random((72, 128, 3)) * 255).astype(np.uint8))
    kept = location_angles.restore_check(frame, frame, original)
    assert kept["ok"] and kept["frame_match"] > 0.99
    flipped = location_angles.restore_check(frame, original, original)
    assert not flipped["ok"] and "original" in flipped["reason"]


def test_the_shots_routes_take_frames_out_of_a_video_and_check_them(env):
    client = env.client
    path = os.path.join(env.temp_dir, "shots.mp4")
    _shots_video(path)
    with open(path, "rb") as f:
        status = upload_pictures_and_wait(
            client, [("file", ("shots.mp4", f.read(), "video/mp4"))]
        )
    assert status["status"] == "completed", status
    video_id = status["results"][0]["picture_id"]

    prompt = client.post(
        f"{API_PREFIX}/item-sheets/shots/prompt",
        json={"description": "a pub", "shots": [{"preset": "left"}] * 3},
    ).json()
    assert prompt["seconds"] == 8 and "SHOT 4" in prompt["prompt"]

    # Four new shots asked for, three filmed: the three are kept.
    made = client.post(
        f"{API_PREFIX}/item-sheets/shots",
        json={"video_picture_id": video_id, "shots": 4},
    )
    assert made.status_code == 200, made.text
    frames = made.json()["picture_ids"]
    assert len(frames) == 3 and video_id not in frames

    checked = client.post(
        f"{API_PREFIX}/item-sheets/shots/check",
        json={
            "original_id": frames[0],
            "pairs": [
                {"frame_id": frames[1], "restored_id": frames[1]},
                {"frame_id": frames[2], "restored_id": frames[0]},
            ],
        },
    ).json()["views"]
    assert [v["ok"] for v in checked] == [True, False]
    assert (
        client.post(
            f"{API_PREFIX}/item-sheets/shots",
            json={"video_picture_id": 999999, "shots": 3},
        ).status_code
        == 404
    )
    assert (
        client.post(
            f"{API_PREFIX}/item-sheets/shots/things", json={"picture_id": 999999}
        ).status_code
        == 404
    )

    # One shot and no cut: no new view, so it is refused.
    single = os.path.join(env.temp_dir, "single.mp4")
    _shots_video(single, shots=1)
    with open(single, "rb") as f:
        status = upload_pictures_and_wait(
            client, [("file", ("single.mp4", f.read(), "video/mp4"))]
        )
    assert status["status"] == "completed", status
    refused = client.post(
        f"{API_PREFIX}/item-sheets/shots",
        json={"video_picture_id": status["results"][0]["picture_id"], "shots": 3},
    )
    assert refused.status_code == 409 and "no cut" in refused.json()["detail"]


def test_only_an_item_that_exists_is_starred(env):
    client = env.client
    for kind in ("scene", "cast", "location"):
        body = {"kind": kind, "item_id": 987654, "favourite": True}
        answer = client.put(f"{API_PREFIX}/studio/favourites", json=body)
        assert answer.status_code == 404, kind
    # A star whose item has gone can still be taken off.
    body = {"kind": "location", "item_id": 987654, "favourite": False}
    assert client.put(f"{API_PREFIX}/studio/favourites", json=body).status_code == 200


def test_a_run_sent_twice_in_one_request_is_one_run(env):
    client = env.client
    scene = client.post(f"{API_PREFIX}/studio/scenes", json={"name": "Twice"}).json()
    key = f"run-{uuid.uuid4().hex}"
    resp = client.post(
        f"{API_PREFIX}/studio/scenes/{scene['id']}/runs",
        json={"runs": [{"key": key}, {"key": key}]},
    )
    assert [r["key"] for r in resp.json()["runs"]] == [key]


# ── a person's sheet tile ───────────────────────────────────────────────────


def _tile(pid, framing=None, **fields):
    return TilePicture(picture_id=pid, framing=framing, **fields)


def test_headshots_and_body_shots_go_by_framing_with_the_thumbnail_first():
    pictures = [
        _tile(1, "close-up", rating=5),
        _tile(2, "portrait", rating=4),
        _tile(3, "full body", rating=5),
        _tile(4, "upper body", rating=3),
        _tile(5, "full body", rating=1),
        # Pinned as the thumbnail though full length: still the first headshot.
        _tile(6, "full body", rating=1),
    ]
    heads, bodies = sheet_tile(pictures, thumbnail_id=6)
    # The best body shots take the body slots; the one left over fills the
    # empty headshot rather than leave a hole.
    assert heads == [6, 1, 2, 5]
    assert bodies == [3, 4]


def test_a_drawn_sheet_s_panels_fill_the_slots_in_sheet_order():
    # Panels 10-13 are passport photos, 14-15 body photos, whatever their
    # measured framing; a better-rated close-up waits behind them.
    panels = [_tile(10 + i, "upper body", source_picture_id=99) for i in range(6)]
    older = [_tile(20 + i, "close-up", source_picture_id=98) for i in range(5)]
    heads, bodies = sheet_tile([_tile(1, "close-up", rating=5), *panels, *older])
    assert heads == [10, 11, 12, 13]
    assert bodies == [14, 15]


def test_the_newest_of_two_sheets_wins():
    old = [_tile(10 + i, source_picture_id=90) for i in range(6)]
    new = [_tile(30 + i, source_picture_id=80) for i in range(6)]
    assert sheet_tile(old + new) == ([30, 31, 32, 33], [34, 35])


def test_unknown_framings_and_one_stack_member_fill_what_is_left():
    pictures = [
        _tile(1, "close-up", stack_id=7, rating=5),
        _tile(2, "close-up", stack_id=7, rating=1),
        _tile(3, None, rating=2),
        _tile(4, "full body", rating=1),
        _tile(5, "close-up", rating=0),
    ]
    heads, bodies = sheet_tile(pictures)
    assert heads == [1, 5, 3]
    assert bodies == [4]


def test_a_stacked_sheet_panel_still_marks_its_sheet():
    panels = [_tile(10 + i, source_picture_id=99) for i in range(6)]
    panels[1].stack_id = panels[2].stack_id = 3
    heads, bodies = sheet_tile(panels)
    assert heads[0] == 10 and bodies == [14, 15]


def test_tiles_read_the_largest_face_and_survive_a_bad_box():
    person = SimpleNamespace(id=5, thumbnail_picture_id=None)
    face = [100, 100, 300, 400]
    rows = [
        # Picture 1: a big face high in a tall frame reads as a close-up; a
        # second, smaller face of the same person on it does not change that.
        (5, json.dumps(face), 1, 600, 400, None, None, None, 3, None),
        (5, json.dumps([10, 10, 20, 20]), 1, 600, 400, None, None, None, 3, None),
        # Picture 2: a small face over a tall frame reads as full body.
        (5, json.dumps([100, 10, 130, 40]), 2, 600, 2000, None, None, None, 1, None),
        # Picture 3: an unreadable box costs its framing, not the overview.
        (5, json.dumps([None, "a", 1, 2]), 3, 600, 400, None, None, None, 0, None),
    ]
    session = SimpleNamespace(exec=lambda _query: SimpleNamespace(all=lambda: rows))
    assert sheet_tiles(session, [person]) == {5: ([1, 3], [2], 3)}


def test_a_person_s_card_carries_their_slots_in_the_overview(env):
    client = env.client
    person = _person(client)
    resp = client.get(f"{API_PREFIX}/studio/overview")
    assert resp.status_code == 200, resp.text
    (row,) = [c for c in resp.json()["cast"] if c["id"] == person]
    assert row["headshot_ids"] == []
    assert row["body_ids"] == []
    assert row["picture_count"] == 0
