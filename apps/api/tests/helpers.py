"""Test helpers shared by several modules."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

import httpx2

T = TypeVar("T")


class FakeClock:
    """A clock whose sleep() advances time instantly, so timing logic needs no real waiting."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        await asyncio.sleep(0)  # still yield to the event loop, like a real sleep would

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FullJitter:
    """Makes the random half of a backoff its maximum: delays are exactly base * 2 ** n."""

    def uniform(self, low: float, high: float) -> float:
        return high


@dataclass
class Reply:
    """One scripted HTTP answer for Server."""

    status: int = 200
    json: Any = None
    text: str | None = None
    headers: dict[str, str] = field(default_factory=dict)

    def build(self) -> httpx2.Response:
        if self.text is not None:
            return httpx2.Response(self.status, text=self.text, headers=self.headers)
        return httpx2.Response(self.status, json=self.json, headers=self.headers)


class Server:
    """A scripted fake provider for httpx2.MockTransport.

    Replays its script in order and repeats the last entry forever. An exception in the script is
    raised from the transport, like a network failure.
    """

    def __init__(self, *script: Reply | Exception) -> None:
        self.script = list(script) or [Reply(json={"ok": True})]
        self.requests: list[httpx2.Request] = []

    async def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item.build()


def run(coro: Coroutine[Any, Any, T], *, timeout: float = 20.0) -> T:
    """Run a coroutine to completion from a synchronous test.

    The limit turns a hang (a broken timeout, a lost wake-up) into a failure instead of a stuck
    test run.
    """

    async def bounded() -> T:
        return await asyncio.wait_for(coro, timeout)

    return asyncio.run(bounded())


async def wait_until(
    condition: Callable[[], bool], *, timeout: float = 5.0, interval: float = 0.01
) -> None:
    """Poll `condition` (in real time) until it holds; fail the test if it never does."""
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("the condition was not reached in time")
        await asyncio.sleep(interval)


FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> Any:
    """A JSON file from tests/fixtures (for example "evm/ethereum_native")."""
    return json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
