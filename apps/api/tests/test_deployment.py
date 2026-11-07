"""Properties of deployment/docker-compose.yml that a mistake would turn into a security hole.

CI checks that the file parses; nothing here starts a container (the development machine has no
Docker).
"""

from __future__ import annotations

import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[3] / "deployment" / "docker-compose.yml"


def service_block(name: str) -> str:
    text = COMPOSE.read_text(encoding="utf-8")
    match = re.search(rf"^  {name}:\n((?:    .*\n|\n)+)", text, flags=re.MULTILINE)
    assert match, f"no service called {name}"
    return match.group(1)


def test_watchman_publishes_no_port_because_its_api_has_no_authentication():
    block = service_block("watchman")

    assert "ports:" not in block and "expose:" not in block
    assert "network_mode: host" not in block


def test_the_backend_is_pointed_at_watchman_by_its_internal_name():
    text = COMPOSE.read_text(encoding="utf-8")

    assert "WATCHMAN_URL: ${WATCHMAN_URL-http://watchman:8084}" in text
    # And the one service that does publish a port keeps it on the loopback address.
    assert '"127.0.0.1:8000:8000"' in service_block("api")


def test_watchman_is_told_which_list_to_load():
    assert "INCLUDED_LISTS: us_ofac" in service_block("watchman")


def test_the_webhook_key_and_the_proxy_count_reach_both_services_and_are_off_by_default():
    text = COMPOSE.read_text(encoding="utf-8")
    shared = re.search(r"^x-aml-environment: &aml-environment\n((?:  .*\n)+)", text, re.MULTILINE)
    assert shared, "the environment shared by the api and the worker is gone"

    # In the block both services use, so the worker (which checks the monitors) sees them too.
    assert "ALCHEMY_WEBHOOK_SIGNING_KEY: ${ALCHEMY_WEBHOOK_SIGNING_KEY:-}" in shared.group(1)
    assert "TRUSTED_PROXY_HOPS: ${TRUSTED_PROXY_HOPS:-0}" in shared.group(1)
    assert text.count("environment: *aml-environment") == 2
