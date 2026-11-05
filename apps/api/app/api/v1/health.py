from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.api.deps import get_session

router = APIRouter(tags=["system"])


@router.get("/health")
def health(response: Response, session: Session = Depends(get_session)) -> dict[str, str]:
    """Liveness plus a database round trip. Public, so orchestrators can poll it."""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        response.status_code = 503
        return {"status": "degraded", "database": "error"}
    return {"status": "ok", "database": "ok"}
