"""Load test: simulated lawyers asking golden-set questions, ramped in stages.

Run headless (the load-test workflow does this from GitHub, next to Azure):

    LOAD_STAGES="2:120,5:120,10:180,20:180,30:180" \\
    locust -f tests/load/locustfile.py --headless --host https://<api> --only-summary

Each simulated user is a different lawyer (``X-Dev-User: load-<n>``, sign-in off on dev) on
the four matters, so the AI gateway counts tokens per user as it would for real users.
Every request is also written to ``LOAD_REQUESTS_CSV`` for tests/load/report.py, which
computes exact percentiles per stage (Locust's own history keeps only rolling windows).
"""

from __future__ import annotations

import csv
import itertools
import json
import os
import random
import time
from pathlib import Path
from typing import Any

from locust import HttpUser, LoadTestShape, between, events, task

QUESTIONS = [
    q["question"]
    for q in json.loads(
        (Path(__file__).parents[1] / "evaluation/datasets/generation_golden_set.json").read_text(encoding="utf-8")
    )["queries"]
    if q.get("answerable", True)
]
MATTERS = "BM/2026/A-0377,BM/2026/H-0412,BM/2026/V-0198,VRB/2024/ZK-9012"
STAGES = [
    (int(users), int(seconds))
    for users, seconds in (
        s.split(":") for s in os.getenv("LOAD_STAGES", "2:120,5:120,10:180,20:180,30:180").split(",")
    )
]

_ids = itertools.count(1)
_out = open(os.getenv("LOAD_REQUESTS_CSV", "load_requests.csv"), "w", newline="", encoding="utf-8")  # noqa: SIM115
_rows = csv.writer(_out)
_rows.writerow(["time", "users", "name", "status", "ok", "latency_ms", "grounding"])
_env: Any = None


@events.init.add_listener
def _keep_environment(environment: Any, **_: Any) -> None:
    global _env
    _env = environment


class Lawyer(HttpUser):
    wait_time = between(5, 15)  # reading the answer before the next question

    def on_start(self) -> None:
        self.headers = {"X-Dev-User": f"load-{next(_ids)}", "X-Dev-Roles": "Lawyer", "X-Dev-Matters": MATTERS}

    @task
    def ask(self) -> None:
        body = {"message": random.choice(QUESTIONS), "stream": False}  # noqa: S311
        with self.client.post(
            "/api/chat", json=body, headers=self.headers, timeout=180, catch_response=True, name="/api/chat"
        ) as response:
            grounding = ""
            if response.status_code == 200:
                grounding = (response.json() or {}).get("grounding", "")
                response.success()
            else:
                response.failure(f"HTTP {response.status_code}")
            response.request_meta["context"]["grounding"] = grounding


class Stages(LoadTestShape):
    """Hold each user count for its duration, then the next; stop after the last."""

    def tick(self) -> tuple[int, float] | None:
        elapsed = self.get_run_time()
        for users, seconds in STAGES:
            if elapsed < seconds:
                return users, users  # start them all within a second or so
            elapsed -= seconds
        return None


@events.request.add_listener
def _record(
    request_type: str,
    name: str,
    response_time: float,
    response: Any,
    exception: Exception | None,
    context: dict[str, Any],
    **_: Any,
) -> None:
    users = _env.runner.user_count if _env and _env.runner else 0
    status = getattr(response, "status_code", 0) or 0
    _rows.writerow(
        [
            round(time.time(), 1),
            users,
            name,
            status,
            int(exception is None),
            round(response_time),
            context.get("grounding", ""),
        ]
    )
    _out.flush()
