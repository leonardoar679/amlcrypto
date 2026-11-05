from __future__ import annotations

from fastapi import APIRouter, Request
from sqlalchemy.exc import SQLAlchemyError

from app import __version__
from app.database import migrations

router = APIRouter(tags=["system"])


@router.get("/version")
def version(request: Request) -> dict[str, str | bool | None]:
    """Application version and database schema revision (null when the database is unreachable)."""
    head = migrations.head_revision()
    try:
        current = migrations.current_revision(request.app.state.engine)
    except SQLAlchemyError:
        current = None
    return {
        "version": __version__,
        "schema_revision": current,
        "schema_head": head,
        "schema_up_to_date": current is not None and current == head,
    }
