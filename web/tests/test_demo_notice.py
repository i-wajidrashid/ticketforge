"""A demo deployment says so on every page."""

from __future__ import annotations

import pytest
from django.test import override_settings

pytestmark = pytest.mark.django_db


@pytest.mark.parametrize("path", ["/", "/accounts/login/", "/accounts/signup/"])
def test_demo_pages_carry_the_notice_and_stay_out_of_search(client, path):
    with override_settings(DEMO_MODE=True):
        html = client.get(path).content.decode()

    assert 'class="demo-banner"' in html
    assert "Payments are simulated and no real tickets are sold." in html
    assert '<meta name="robots" content="noindex, nofollow">' in html


def test_a_real_deployment_has_no_demo_notice(client):
    with override_settings(DEMO_MODE=False):
        html = client.get("/").content.decode()

    assert "demo-banner" not in html
    assert "noindex" not in html
