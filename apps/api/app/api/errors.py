"""One error envelope for every failure: {"success": false, "error": {code, message, ...}}."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DataError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.headers import SECURITY_HEADERS

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


def error_response(
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
    # Not every error passes through the middleware that adds these (the server's own, a last-resort
    # 500, is made outside it), so an error envelope always carries them.
    response_headers = {**SECURITY_HEADERS, **(headers or {})}
    if request_id:
        response_headers.setdefault("X-Request-ID", request_id)
    return JSONResponse(
        {"success": False, "error": error}, status_code=status_code, headers=response_headers
    )


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def handle_api_error(request: Request, exc: APIError) -> JSONResponse:
        return error_response(request, exc.status_code, exc.code, exc.message, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        # Report where and why, never the submitted value: login bodies contain passwords.
        details = [
            {"field": ".".join(str(part) for part in err["loc"]), "message": err["msg"]}
            for err in exc.errors()
        ]
        return error_response(
            request, 422, "validation_error", "The request is invalid.", details=details
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(request: Request, exc: StarletteHTTPException):
        code = _HTTP_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return error_response(request, exc.status_code, code, message, headers=exc.headers)

    @app.exception_handler(PoolTimeout)
    @app.exception_handler(OperationalError)
    async def handle_unavailable(request: Request, exc: Exception) -> JSONResponse:
        # No connection to be had in time, the database locked or deadlocked, or not there: for the
        # caller all of it is "not now", which is what a 503 says (a 500 says "a bug").
        logger.error(
            "the database could not serve a request",
            extra={"request_id": request.scope.get("request_id"), "error": type(exc).__name__},
        )
        return error_response(
            request,
            503,
            "service_unavailable",
            "The service is busy or cannot reach its database. Try again shortly.",
            headers={"Retry-After": "2"},
        )

    @app.exception_handler(DataError)
    async def handle_unstorable(request: Request, exc: DataError) -> JSONResponse:
        # The database refused a value as too long or out of range for its column (MySQL does;
        # SQLite accepts anything). Whatever the route's own checks missed, it is the caller's
        # value that cannot be kept, which is a 422 and not a bug to be paged for.
        logger.warning(
            "a value could not be stored",
            extra={"request_id": request.scope.get("request_id"), "error": type(exc).__name__},
        )
        return error_response(
            request, 422, "unstorable_value", "A value is too long or out of range to be kept."
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled error", extra={"request_id": request.scope.get("request_id")})
        return error_response(request, 500, "internal_error", "Internal server error.")
