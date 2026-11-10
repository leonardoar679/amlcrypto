"""The job that keeps a label source up to date."""

from __future__ import annotations

from typing import Any

from app.intel.importer import OFAC_SLUG, READERS, ImportFailed, refresh_source
from app.providers.base import ProviderUnavailable
from app.worker.context import JobContext, NonRetryable

REFRESH_LABEL_SOURCE = "REFRESH_LABEL_SOURCE"


async def refresh_label_source(context: JobContext) -> dict[str, Any]:
    """Download a label source and bring the stored claims up to date.

    Payload: {"source": "ofac_sdn" (the default) or another source that has a reader, "force": true
    to import even a list that is the one already imported}. The result says what changed. A
    failure to download is tried again later; a list that cannot be read is not, since the same
    file will not read any better.
    """
    payload, settings = context.payload, context.settings
    slug = payload.get("source", OFAC_SLUG)
    force = payload.get("force", False)
    if not isinstance(slug, str) or slug not in READERS:
        raise NonRetryable(f"no reader for the label source {slug!r}")
    if not isinstance(force, bool):
        raise NonRetryable("force must be true or false")
    if context.sessions is None:
        raise NonRetryable("this job needs the database")
    try:
        report = await refresh_source(
            context.sessions,
            slug,
            data_dir=settings.resolved_data_dir / "downloads",
            max_bytes=settings.intel_max_download_bytes,
            timeout=settings.intel_download_timeout_seconds,
            force=force,
        )
    except ImportFailed as error:
        if error.retryable:
            raise ProviderUnavailable(slug, str(error)) from error
        raise NonRetryable(str(error)) from error
    return report.to_dict()
