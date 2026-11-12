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


def test_the_public_address_of_the_site_reaches_the_web_build():
    # the Atom feed's links and ids are made from NEXT_PUBLIC_SITE_URL, which the framework reads
    # when the app is built: without a build argument an operator could not set it
    dockerfile = (WEB / "Dockerfile").read_text(encoding="utf-8")
    build, final = dockerfile.split("AS build", 1)[1].split("FROM node:22-slim", 1)

    assert "ARG NEXT_PUBLIC_SITE_URL=" in build
    assert "ENV NEXT_PUBLIC_SITE_URL=$NEXT_PUBLIC_SITE_URL" in build
    assert build.index("ARG NEXT_PUBLIC_SITE_URL") < build.index("RUN npm run build")
    assert "NEXT_PUBLIC_SITE_URL" not in final, (
        "it is inlined when the app is built, not read again"
    )
    assert "NEXT_PUBLIC_SITE_URL: ${NEXT_PUBLIC_SITE_URL:-}" in service_block("web")


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
    # the web host takes a form at most, except the one request that saves a whole incident: a
    # location of its own, with a limit a little above what the web app allows it, and nowhere else
    assert "client_max_body_size 128k;" in code
    assert re.search(
        r"location ~ \^/api/v1/editor/\(incidents\|wallets\)/\[0-9\]\+/content\$ \{"
        r"\s*client_max_body_size 640k;\s*limit_req zone=aml_web",
        code,
    )
    assert code.count("client_max_body_size 640k;") == 1
    rules = (WEB / "src" / "lib" / "proxyRules.js").read_text(encoding="utf-8")
    app_cap = re.search(r"MAX_CONTENT_BYTES = (\d+) \* 1024;", rules)
    assert app_cap and int(app_cap.group(1)) < 640, (
        "the proxy must take at least what the app allows"
    )
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


def test_a_fully_loaded_incident_still_fits_what_the_web_app_lets_an_editor_save():
    """An editor saves a whole incident in one request, through the web app, which refuses a body
    over its own limit (src/lib/proxyRules.js). Fill every part of an incident the way a heavy one
    is filled: the most documents, amounts, addresses, movements and timeline entries the platform
    takes, each with a long but ordinary text. It must stay well under that limit, so that raising
    any of the platform's own limits is a decision about this one too, and not a surprise to an
    editor."""

    import json

    from app.incidents.model import MAX_ADDRESSES, MAX_AMOUNTS, MAX_EVIDENCE, MAX_FLOWS, MAX_UPDATES

    where = "0x" + "a" * 40
    hash_ = "0x" + "a" * 64
    body = {
        "title": "T" * 200,
        "summary": "S" * 5000,
        "kind": "hack",
        "occurred_on": "2026-01-01",
        "attribution_note": "A" * 2000,
        "internal_note": "N" * 2000,
        "evidence": [
            {
                "ref": f"e{n}",
                "publisher": "P" * 40,
                "title": "T" * 100,
                "url": "https://example.org/" + "u" * 80,
                "kind": "analytics",
                "retrieved_on": "2026-01-02",
                "published_on": "2026-01-01",
                "excerpt": "E" * 300,
            }
            for n in range(MAX_EVIDENCE)
        ],
        "amounts": [
            {
                "evidence_ref": "e1",
                "amount_usd_low": 6430000000,
                "amount_usd_high": 6500000000,
                "as_of": "2026-01-02",
                "label": "L" * 60,
            }
            for _ in range(MAX_AMOUNTS)
        ],
        "addresses": [
            {
                "chain": "ethereum",
                "address": where,
                "role": "ATTACKER_FUNDS_RECEIVER",
                "attribution_status": "REPORTED_BY_ANALYST",
                "confidence": 80,
                "evidence_ref": "e1",
                "note": "N" * 300,
                "last_verified_at": "2026-01-02T10:00:00Z",
            }
            for _ in range(MAX_ADDRESSES)
        ],
        "flows": [
            {
                "from_chain": "ethereum",
                "from_address": where,
                "to_chain": "ethereum",
                "to_address": where,
                "evidence_ref": "e1",
                "tx_hash": hash_,
                "amount_text": "A" * 80,
                "occurred_on": "2026-01-02",
                "note": "N" * 300,
            }
            for _ in range(MAX_FLOWS)
        ],
        "updates": [
            {
                "occurred_at": "2026-01-02T10:00:00Z",
                "kind": "event",
                "body": "B" * 500,
                "evidence_ref": "e1",
                "chain": "ethereum",
                "tx_hash": hash_,
            }
            for _ in range(MAX_UPDATES)
        ],
    }
    size = len(json.dumps(body, separators=(",", ":")).encode())  # as the browser sends it

    rules = (WEB / "src" / "lib" / "proxyRules.js").read_text(encoding="utf-8")
    cap = re.search(r"MAX_CONTENT_BYTES = (\d+) \* 1024;", rules)
    assert cap, "the web app's limit for saving an incident was not found"
    limit = int(cap.group(1)) * 1024
    # larger than the 64 KB every other request gets (that is why the route has a limit of its own),
    # and not within a sixth of the limit
    assert 64 * 1024 < size < limit * 0.85, f"{size} bytes against a limit of {limit}"


def test_a_fully_loaded_wallet_profile_still_fits_what_the_web_app_lets_an_editor_save():
    """A wallet profile is saved the same way, as one request, through the same limit: an exchange
    can name hundreds of addresses. Fill it as heavily as the platform allows."""

    import json

    from app.editorial import MAX_EVIDENCE
    from app.wallets.model import MAX_ADDRESSES

    body = {
        "name": "N" * 120,
        "kind": "exchange",
        "summary": "S" * 5000,
        "website": "https://example.org/" + "w" * 80,
        "attribution_note": "A" * 2000,
        "internal_note": "I" * 2000,
        "evidence": [
            {
                "ref": f"e{n}",
                "publisher": "P" * 40,
                "title": "T" * 100,
                "url": "https://example.org/" + "u" * 80,
                "kind": "official",
                "retrieved_on": "2026-01-02",
                "published_on": "2026-01-01",
                "excerpt": "E" * 300,
            }
            for n in range(MAX_EVIDENCE)
        ],
        "addresses": [
            {
                "chain": "ethereum",
                "address": "0x" + "a" * 40,
                "label": "L" * 60,
                "attribution_status": "OFFICIALLY_CONFIRMED",
                "confidence": 95,
                "evidence_ref": "e1",
                "note": "N" * 500,
                "last_verified_at": "2026-01-02T10:00:00Z",
            }
            for _ in range(MAX_ADDRESSES)
        ],
    }
    size = len(json.dumps(body, separators=(",", ":")).encode())

    rules = (WEB / "src" / "lib" / "proxyRules.js").read_text(encoding="utf-8")
    cap = re.search(r"MAX_CONTENT_BYTES = (\d+) \* 1024;", rules)
    assert cap, "the web app's limit for saving a profile was not found"
    limit = int(cap.group(1)) * 1024
    assert 64 * 1024 < size < limit * 0.85, f"{size} bytes against a limit of {limit}"
