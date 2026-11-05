from __future__ import annotations

import io
import logging

import pytest

from app.core.logging import JsonFormatter
from app.core.redaction import MASK, MIN_SECRET_LENGTH, SecretRegistry, redact_headers, redact_url

KEY = "k3y-Abc123XyZ789"


@pytest.fixture
def registry() -> SecretRegistry:
    registry = SecretRegistry()
    registry.add(KEY)
    return registry


def test_registered_secrets_are_masked_wherever_they_appear(registry):
    text = f"GET https://eth-mainnet.g.alchemy.com/v2/{KEY} failed; also {KEY} in prose"
    scrubbed = registry.scrub(text)
    assert KEY not in scrubbed
    assert scrubbed.count(MASK) == 2


def test_the_longest_secret_wins_when_one_contains_another():
    registry = SecretRegistry()
    registry.add("secret-value-1")
    registry.add("secret-value-1-extended")
    assert registry.scrub("token secret-value-1-extended end") == f"token {MASK} end"


def test_values_too_short_to_be_safe_are_not_registered():
    registry = SecretRegistry()
    registry.add("x" * (MIN_SECRET_LENGTH - 1))
    registry.add(None)
    registry.add("")
    assert registry.scrub("xxxxxx and more text") == "xxxxxx and more text"


@pytest.mark.parametrize(
    "text",
    [
        "https://api.etherscan.io/v2/api?chainid=1&apikey=ABCDEF123456&module=proxy",
        "https://mainnet.helius-rpc.com/?api-key=ABCDEF123456",
        "request failed: api_key=ABCDEF123456",
        "token=ABCDEF123456 rejected",
        "Authorization: Bearer ABCDEF123456",
        "TRON-PRO-API-KEY: ABCDEF123456",
        '{"authorization": "Bearer ABCDEF123456"}',
        "x-api-key=ABCDEF123456",
    ],
)
def test_credential_shaped_text_is_masked_even_for_unregistered_keys(text):
    scrubbed = SecretRegistry().scrub(text)
    assert "ABCDEF123456" not in scrubbed
    assert MASK in scrubbed


def test_ordinary_text_is_left_alone():
    text = "keyboard monkey turkey: the key point is that tokens expire"
    assert SecretRegistry().scrub(text) == text


def test_redact_url_masks_secret_params_and_path_secrets(registry):
    url = f"https://host.example/v2/{KEY}/api?chainid=1&apikey=SOMETHING&limit=10"
    safe = redact_url(url, registry)
    assert KEY not in safe and "SOMETHING" not in safe
    assert "chainid=1" in safe and "limit=10" in safe
    assert f"apikey={MASK}" in safe


def test_redact_headers_masks_credentials_only():
    headers = [
        ("Authorization", "Bearer abc"),
        ("TRON-PRO-API-KEY", "k"),
        ("Accept", "application/json"),
    ]
    assert redact_headers(headers) == {
        "Authorization": MASK,
        "TRON-PRO-API-KEY": MASK,
        "Accept": "application/json",
    }


def test_json_log_lines_never_contain_a_registered_secret():
    from app.core.redaction import secrets

    secrets.add(KEY)
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test.redaction")
    logger.handlers = [handler]
    logger.propagate = False
    logger.setLevel(logging.INFO)

    logger.info(
        "calling https://host.example/v2/%s", KEY, extra={"url": f"https://x/?apikey={KEY}"}
    )
    try:
        raise RuntimeError(f"upstream rejected key {KEY}")
    except RuntimeError:
        logger.exception("provider call failed")

    output = stream.getvalue()
    assert KEY not in output
    assert MASK in output
    assert output.count("\n") == 2  # still one JSON document per line
