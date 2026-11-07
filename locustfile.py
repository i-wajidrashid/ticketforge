"""Locust load test for the public, read-heavy API.

    make loadtest-on          # lift the per-IP edge limit (load testing only)
    make loadtest-headless    # 500 users for 90 s, prints the summary
    make loadtest-off         # restore the production edge limit

Or `make loadtest` for the Locust web UI at http://localhost:8089.

Every task is an anonymous GET, the traffic shape of people browsing before a
big on-sale. Checkout (holds and payments) is covered by the concurrency
tests in tests/test_concurrency.py rather than here: 500 simulated buyers
would sell out the demo venues in seconds and then only measure "sold out".
"""

from __future__ import annotations

import os
import random

import urllib3
from locust import HttpUser, between, task

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

HOST = os.environ.get("TF_HOST", "https://localhost")
EVENT_SLUGS: list[str] = []


class TicketForgeUser(HttpUser):
    wait_time = between(0.5, 2.0)
    host = HOST

    def on_start(self) -> None:
        self.client.verify = False  # the local stack uses a self-signed certificate
        if not EVENT_SLUGS:
            listing = self.client.get("/api/v1/events", name="/api/v1/events").json()
            EVENT_SLUGS.extend(event["slug"] for event in listing.get("items", []))

    @task(5)
    def browse_events(self) -> None:
        self.client.get("/api/v1/events", name="/api/v1/events")

    @task(3)
    def view_event(self) -> None:
        if EVENT_SLUGS:
            self.client.get(
                f"/api/v1/events/{random.choice(EVENT_SLUGS)}", name="/api/v1/events/[slug]"
            )

    @task(2)
    def view_seat_map(self) -> None:
        if EVENT_SLUGS:
            self.client.get(
                f"/api/v1/events/{random.choice(EVENT_SLUGS)}/seats",
                name="/api/v1/events/[slug]/seats",
            )

    @task(1)
    def search(self) -> None:
        self.client.get("/api/v1/search?q=live&city=Karachi", name="/api/v1/search")
