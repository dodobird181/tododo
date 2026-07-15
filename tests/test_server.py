"""
Route dispatch: POST returns a uuid, polling returns the applied event, and
reads fold the projection. Exercises the pure `dispatch` core (no socket).
"""

from __future__ import annotations

import time

from tododo.app import Backend
from tododo.server import dispatch


def _wait_job(backend: Backend, uuid: str, timeout: float = 3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = backend.poll(uuid)
        if job and job.status != "pending":
            return job
        time.sleep(0.01)
    raise AssertionError("job never completed")


def _post(backend, path, body):
    return dispatch(backend, "POST", path, {}, body)


def test_post_returns_uuid_and_polls(backend: Backend):
    backend.start()
    try:
        status, payload = _post(backend, "/board", {"name": "Work", "columns": ["todo", "done"]})
        assert status == 200
        uuid = payload["uuid"]
        job = _wait_job(backend, uuid)
        assert job.status == "done"
        assert job.event.op == "CreateBoard"

        _, job_payload = dispatch(backend, "GET", "/job", {"uuid": uuid}, {})
        assert job_payload["status"] == "done"
    finally:
        backend.stop()


def test_reads_fold_projection(backend: Backend):
    backend.start()
    try:
        _, created = _post(backend, "/board", {"name": "Work", "columns": ["todo"]})
        job = _wait_job(backend, created["uuid"])
        board_id = job.event.target

        status, boards = dispatch(backend, "GET", "/boards", {}, {})
        assert status == 200
        assert any(board["id"] == board_id for board in boards["boards"])

        _, one = dispatch(backend, "GET", "/board", {"id": board_id}, {})
        assert one["name"]["value"] == "Work"
    finally:
        backend.stop()


def test_missing_field_is_400(backend: Backend):
    status, payload = _post(backend, "/board", {})
    assert status == 400
    assert "error" in payload


def test_unknown_uuid_is_404(backend: Backend):
    status, payload = dispatch(backend, "GET", "/job", {"uuid": "nope"}, {})
    assert status == 404
