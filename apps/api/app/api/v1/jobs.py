"""The state of a job the caller's organization queued."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import Principal, get_session, screening_read
from app.api.errors import APIError
from app.api.limits import IdPath
from app.database.models import FetchJob

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/{job_id}")
def get_job(
    job_id: IdPath,
    principal: Principal = Depends(screening_read),
    session: Session = Depends(get_session),
) -> dict[str, Any]:
    """Where a job stands (`QUEUED`, `RUNNING`, `SUCCEEDED`, `FAILED`) and, when it has finished,
    its result or why it failed. Only the organization that queued a job can see it."""
    job = session.get(FetchJob, job_id)
    if job is None or job.organization_id != principal.organization_id:
        raise APIError(404, "job_not_found", "No such job.")
    return {
        "success": True,
        "job_id": job.id,
        "type": job.job_type,
        "status": job.status,
        "attempts": job.attempts,
        "max_attempts": job.max_attempts,
        "queued_at": job.created_at.isoformat(),
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        "result": job.result,
        "error": job.error,
    }
