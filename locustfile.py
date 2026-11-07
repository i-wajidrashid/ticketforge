"""Locust load test for the public API.

    make loadtest      # then open http://localhost:8089 and ramp to 500 users

The fake Stripe gateway means you can also exercise holds/payments safely.
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
        self.client.verify = False
        self.client.headers.update({"Content-Type": "application/json"})
        index = random.randint(0, 99)
        response = self.client.post(
            "/accounts/api/token/",
            json={"email": f"fan{index:03d}@ticketforge.local", "password": "ticketforge123"},
        )
        if response.status_code == 200:
            self.token = response.json()["access"]
            self.client.headers.update({"Authorization": f"Bearer {self.token}"})
        else:
            self.token = ""

        if not EVENT_SLUGS:
            listing = self.client.get("/api/v1/events").json().get("items", [])
            EVENT_SLUGS.extend(event["slug"] for event in listing)

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
