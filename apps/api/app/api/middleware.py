"""What every request and response goes through: a request id, the size of what is sent, and the
headers that go back."""

from __future__ import annotations

import re
from collections.abc import Mapping
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import error_response
from app.api.headers import SECURITY_HEADERS
from app.core.logging import request_id_var

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


class RequestIdMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = ""
        for name, value in scope["headers"]:
            if name == b"x-request-id":
                incoming = value.decode("latin-1")
                break
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid4().hex
        scope["request_id"] = request_id
        token = request_id_var.set(request_id)

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        finally:
            request_id_var.reset(token)


class SecurityHeadersMiddleware:
    """Adds the headers in app.api.headers to every response that does not set them itself."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    if name not in headers:
                        headers[name] = value
            await send(message)

        await self.app(scope, receive, send_with_headers)


class BodyLimitMiddleware:
    """Refuses a request whose body is larger than the limit (413), before the application has read
    it. A body that says how long it is, is refused without being read at all; one that does not
    (sent in chunks) is cut off as soon as what has arrived passes the limit, so the server never
    holds more than the limit of anyone's body. `larger` gives a path its own, bigger limit.

    Once a body has been cut off, whatever the application then answers (FastAPI turns a body it
    could not read into a 400) is replaced by the 413."""

    def __init__(
        self, app: ASGIApp, *, limit: int, larger: Mapping[str, int] | None = None
    ) -> None:
        self.app = app
        self.limit = limit
        self.larger = dict(larger or {})

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = max(self.limit, self.larger.get(scope["path"], 0))
        for name, value in scope["headers"]:
            if name == b"content-length":
                if value.isdigit() and int(value) > limit:
                    await self._refuse(scope, receive, send)
                    return
                break

        received = 0
        cut_off = False
        answered = False

        async def counted() -> Message:
            nonlocal received, cut_off
            if cut_off:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    cut_off = True
                    return {"type": "http.disconnect"}
            return message

        async def watched(message: Message) -> None:
            nonlocal answered
            if message["type"] == "http.response.start":
                if cut_off:
                    answered = True
                    await self._refuse(scope, receive, send)
                    return
            elif answered:
                return  # the rest of an answer that was replaced
            await send(message)

        await self.app(scope, counted, watched)
        if cut_off and not answered:
            await self._refuse(scope, receive, send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = error_response(
            Request(scope), 413, "payload_too_large", "The request body is too large."
        )
        await response(scope, receive, send)
