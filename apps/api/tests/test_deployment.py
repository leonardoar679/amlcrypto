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


# --- the web app's container ---------------------------------------------------------------------

WEB = COMPOSE.parents[1] / "apps" / "web"
NGINX = COMPOSE.parent / "nginx"


def test_the_web_app_publishes_its_port_on_the_loopback_address_only_and_holds_no_secret():
    block = service_block("web")

    assert '"127.0.0.1:3000:3000"' in block
    assert "BACKEND_URL: http://api:8000" in block
    # it reaches the API by name inside the compose project, and has nothing of the API's: its
    # token is made by the API, its provider keys are the API's alone
    for secret in ("SECRET_KEY", "API_KEY", "SIGNING_KEY", "DATABASE_URL", "*aml-environment"):
        assert secret not in block, secret
    assert "service_healthy" in block and "api:" in block


def test_the_web_image_is_built_from_the_web_app_and_runs_as_nobody_special():
    dockerfile = (WEB / "Dockerfile").read_text(encoding="utf-8")

    assert "context: ../apps/web" in service_block("web")
    assert "USER node" in dockerfile and "NEXT_OUTPUT=standalone" in dockerfile
    assert 'CMD ["node", "server.js"]' in dockerfile
    # the second stage takes what was built and nothing else: no source, no dev tools, no .env
    final = dockerfile.split("AS build", 1)[1].split("FROM node:22-slim", 1)[1]
    assert "COPY . ." not in final and "npm " not in final and ".env" not in final
    ignored = (WEB / ".dockerignore").read_text(encoding="utf-8").split()
    assert {"node_modules", ".next", ".env"} <= set(ignored)


def test_the_example_proxy_does_what_the_api_and_the_web_app_count_on():
    config = (NGINX / "aml.conf.example").read_text(encoding="utf-8")
    headers = (NGINX / "proxy-headers.conf").read_text(encoding="utf-8")
    code = "\n".join(line.split("#", 1)[0] for line in config.splitlines())

    assert code.count("{") == code.count("}"), "unbalanced braces"
    # sign-in is rate limited harder than anything else, and only the web host has it
    assert re.search(r"limit_req_zone \$binary_remote_addr zone=aml_login:\w+ rate=\d+r/m;", code)
    assert re.search(r"location = /api/session/login \{\s*limit_req zone=aml_login", code)
    assert re.search(r"location = /api/v1/auth/login \{\s*return 404;", code)
    # what anybody may read is limited by address and cannot be written through that location
    assert re.search(r"limit_req_zone \$binary_remote_addr zone=aml_public:\w+ rate=\d+r/s;", code)
    assert re.search(
        r"location /api/v1/public/ \{\s*limit_req zone=aml_public"
        r".*?limit_except GET \{\s*deny all;",
        code,
        re.S,
    )
    # a body larger than the API accepts never reaches it, and nothing waits forever for one
    assert "client_max_body_size 1m;" in code and "client_body_timeout" in code
    assert "server_tokens off;" in code and "Strict-Transport-Security" in code
    assert "ssl_protocols TLSv1.2 TLSv1.3;" in code
    assert re.search(r"listen 80;.*return 301 https://\$host\$request_uri;", code, re.S)
    # the provider's webhook can be reached on the API host, by POST only
    assert re.search(
        r"location = /api/v1/webhooks/alchemy \{.*limit_except POST \{\s*deny all;", code, re.S
    )
    # every upstream a location names is defined, and every include is the file beside it
    defined = set(re.findall(r"upstream (\w+) \{", code))
    assert set(re.findall(r"proxy_pass http://(\w+);", code)) <= defined
    assert set(re.findall(r"include /etc/nginx/aml/([\w.-]+);", code)) == {"proxy-headers.conf"}
    # the client's address is OVERWRITTEN, not appended to: what a client sent in it is a claim
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in headers
    assert "$proxy_add_x_forwarded_for" not in headers + code
    assert "proxy_set_header X-Request-ID $request_id;" in headers
