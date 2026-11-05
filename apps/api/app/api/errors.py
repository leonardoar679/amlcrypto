"""One error envelope for every failure: {"success": false, "error": {code, message, ...}}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_HTTP_CODES = {
    400: "bad_request",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    429: "rate_limited",
}


class APIError(Exception):
    def __init__(
        self, status_code: int, code: str, message: str, *, headers: dict[str, str] | None = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers


def _response(
    request: Request,
    status_code: int,
    code: str,
    message: str,
    *,
    details: list[dict] | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    request_id = request.scope.get("request_id")
    error: dict[str, object] = {"code": code, "message": message}
    if request_id:
        error["request_id"] = request_id
    if details:
        error["details"] = details
    response_headers = dict(headers or {})
    if request_id:
        response_headers.setdefault("X-Request-ID", request_id)
    return JSONResponse(
        {"success": False, "error": error}, status_code=status_code, headers=response_headers
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
        return _response(request, exc.status_code, exc.code, exc.message, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        # Report where and why, never the submitted value: login bodies contain passwords.
        details = [
            {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
            for err in exc.errors()
        ]
        return _response(
            request, 422, "validation_error", "The request is invalid.", details=details
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _response(request, exc.status_code, code, message, headers=exc.headers)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"request_id": request.scope.get("request_id")})
        return _response(request, 500, "internal_error", "Internal server error.")
