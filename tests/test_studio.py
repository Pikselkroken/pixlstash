"""The Studio (#2819): scene shots, the scene routes, takes and items."""

import gc
import json
import os
import tempfile
import uuid
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from pixlstash.db_models import Picture
from pixlstash.db_models.studio import Scene
from pixlstash.server import Server
from pixlstash.services import comfyui_service
from pixlstash.services.studio import Item, Person, compose_shot, place_words
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
