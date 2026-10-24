"""The gate's HTTPS API, built from what it needs so it runs the same on Modal and in a test: the
job store, how to start a battery run, how to read the pins, and the token.

The token is the only way in: every route needs `Authorization: Bearer <token>`, compared in
constant time.

A battery run that stops reporting (its function was preempted or hit its timeout) would stay
`running` for ever; one that has not changed for `stale_after_s` is reported as failed.
"""

import hmac
import time
import uuid
from collections.abc import Callable, Mapping, MutableMapping
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException

from bh02_lab.contract import ContractError
from bh02_lab.gate_core import asked

__all__ = ["STALE_AFTER_S", "authorised", "build_api", "reported"]

STALE_AFTER_S = 5 * 60 * 60  # longer than a battery run's own time limit

type Start = Callable[[str, list[str], int, str], None]


def authorised(header: str | None, expected: str) -> bool:
    """Whether an Authorization header carries the gate token.

    >>> authorised("Bearer s3cret", "s3cret"), authorised("Bearer nope", "s3cret"), authorised(None, "s3cret")
    (True, False, False)
    """
    scheme, _, token = (header or "").partition(" ")
    return bool(expected) and scheme.lower() == "bearer" and hmac.compare_digest(token.encode(), expected.encode())


def reported(job: Mapping[str, Any], now: float, stale_after_s: float = STALE_AFTER_S) -> dict[str, Any]:
    """A battery run as the API reports it: as kept, or failed when it went quiet unfinished.

    >>> reported({"status": "running", "updated_at": 0.0}, now=10.0, stale_after_s=5.0)["status"]
    'failed'
    >>> reported({"status": "done", "updated_at": 0.0}, now=10.0, stale_after_s=5.0)["status"]
    'done'
    """
    quiet = now - float(job.get("updated_at", now))
    if job.get("status") in ("queued", "running") and quiet > stale_after_s:
        return {**job, "status": "failed", "error": f"the run stopped reporting {int(quiet)} s ago"}
    return dict(job)


def build_api(
    jobs: MutableMapping[str, Any],
    start: Start,
    pins: Callable[[str], dict[str, str]],
    token: str,
    clock: Callable[[], float] = time.time,
) -> FastAPI:
    """The API over `jobs` (battery runs by id), `start` (begins one in the background), `pins`
    (what a rollout is pinned to for a battery) and the gate `token`."""
    api = FastAPI(title="bh02-lab gate", docs_url=None, redoc_url=None, openapi_url=None)

    def checked(authorization: str | None = Header(default=None)) -> None:
        if not authorised(authorization, token):
            raise HTTPException(status_code=401, detail="a gate token is needed: Authorization: Bearer <token>")

    @api.get("/pins", dependencies=[Depends(checked)])
    def get_pins(battery: str = "core") -> dict[str, str]:
        return pins(battery)

    @api.post("/batteries", status_code=202, dependencies=[Depends(checked)])
    def start_battery(body: dict[str, Any]) -> dict[str, str]:
        try:
            ask = asked(body)
        except ContractError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        job_id = uuid.uuid4().hex[:12]
        jobs[job_id] = {"status": "queued", "updated_at": clock()}
        try:
            start(job_id, sorted(ask.genome), ask.k, ask.battery)
        except Exception as error:
            jobs[job_id] = {"status": "failed", "updated_at": clock(), "error": f"did not start: {error}"}
            raise HTTPException(status_code=502, detail=f"the battery run did not start: {error}") from error
        return {"id": job_id}

    @api.get("/batteries/{job_id}", dependencies=[Depends(checked)])
    def battery_status(job_id: str) -> dict[str, Any]:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail=f"no battery run {job_id}")
        return {"id": job_id, **reported(job, clock())}

    return api
