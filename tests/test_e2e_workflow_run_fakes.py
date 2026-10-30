"""The e2e fakes of a workflow run, held against what the server really sends.

``frontend/e2e/specs/workflow-run.spec.js`` stands in for ComfyUI by answering
the run's routes with ``page.route``, from
``frontend/e2e/fixtures/workflowRunFakes.json``. A fake that drifts from the
server keeps that spec green while the app breaks, so each fake is checked here
against the model or the producer that makes the real answer: a field renamed
on either side turns this red.
"""

import json
from pathlib import Path

import pytest

import pixlstash.services.comfyui_service as comfyui_service
from pixlstash.hub.db import HubDatabase
from pixlstash.resources.arbiter import Tenant
from pixlstash.routes.comfyui import ComfyUIPixlstashNodeResponse
from pixlstash.routes.resources import LeaseAnswer
from pixlstash.routes.workflows import RunOpening, RunPreflight, RunResult, RunsCleared
from pixlstash.services import run_log
from tests.test_resource_arbiter import CARD, make

FAKES = json.loads(
    (
        Path(__file__).resolve().parents[1]
        / "frontend"
        / "e2e"
        / "fixtures"
        / "workflowRunFakes.json"
    ).read_text(encoding="utf-8")
)


def _assert_same_shape(fake, real, where: str) -> None:
    """*fake* has exactly *real*'s keys, each holding the same JSON type.

    A null on either side matches anything: the field is there, and whether it
    is filled is the case a fake chooses. A list is held by its first entry.
    """
    if fake is None or real is None:
        return
    numbers = (int, float)
    same_type = type(fake) is type(real) or (
        type(fake) in numbers and type(real) in numbers
    )
    assert same_type, (
        f"{where}: the fake is {type(fake).__name__}, the server sends {type(real).__name__}"
    )
    if isinstance(fake, dict):
        assert set(fake) == set(real), (
            f"{where}: only the fake has {sorted(set(fake) - set(real))}, "
            f"only the server has {sorted(set(real) - set(fake))}"
        )
        for key, value in fake.items():
            _assert_same_shape(value, real[key], f"{where}.{key}")
    elif isinstance(fake, list) and fake and real:
        _assert_same_shape(fake[0], real[0], f"{where}[0]")


@pytest.mark.parametrize(
    "name, model",
    [
        ("preflight", RunPreflight),
        ("refused", RunPreflight),
        ("run", RunResult),
        ("opening", RunOpening),
        ("cleared", RunsCleared),
        ("leaseAnswer", LeaseAnswer),
        ("pixlstashNode", ComfyUIPixlstashNodeResponse),
    ],
)
def test_a_fake_answer_is_one_its_response_model_sends(name, model):
    """A key the model does not know is dropped by the round trip, and a value
    of the wrong type is refused by the strict read."""
    fake = FAKES[name]
    sent = model.model_validate(fake, strict=True).model_dump(
        mode="json", exclude_unset=True
    )
    assert sent == fake


def test_the_live_run_fake_is_what_the_poll_lists(monkeypatch):
    fake = FAKES["liveRun"]
    monkeypatch.setattr(
        comfyui_service,
        "_live_runs",
        {fake["prompt_id"]: {**fake, "library_uuid": "e2e-library"}},
    )
    assert comfyui_service.live_runs("e2e-library") == [fake]


def test_the_finished_fake_is_what_the_run_log_lists(tmp_path):
    fake = FAKES["finished"]
    hub = HubDatabase(str(tmp_path / "hub.db"))
    try:
        run_log.queued(
            hub, fake["id"], fake["id"], fake["workflow_id"], fake["label"], "lib"
        )
        run_log.ended(
            hub,
            {
                "run_id": fake["id"],
                "status": "completed",
                "picture_ids": fake["picture_ids"],
            },
        )
        [real] = run_log.finished(hub, "lib", existing=set)
    finally:
        hub.close()
    _assert_same_shape(fake, real, "finished")


def test_the_resources_fake_is_what_the_arbiter_snapshots():
    """A ComfyUI run waiting for the GPU, as the Tasks tab's Waiting row reads
    it, with the two actions it offers there."""
    fake = FAKES["resources"]
    arb, _ledger, _usage = make(used_mb=12000, thread=False)
    lease = arb.request(
        Tenant.COMFYUI,
        fake["leases"][0]["label"],
        device=CARD,
        need_mb=fake["leases"][0]["need_mb"],
        run_id=fake["leases"][0]["run_id"],
        workflow_id=fake["leases"][0]["workflow_id"],
    )
    try:
        real = arb.snapshot()
    finally:
        lease.release()
    _assert_same_shape(fake, real, "resources")
    assert (
        real["leases"][0]["waiting_reason"]["actions"]
        == fake["leases"][0]["waiting_reason"]["actions"]
    )
