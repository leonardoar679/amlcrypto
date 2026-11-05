"""Importing this package registers every model on Base.metadata (Alembic relies on that)."""

from app.database.models.base import Base
from app.database.models.tenancy import Organization, User

__all__ = ["Base", "Organization", "User"]
