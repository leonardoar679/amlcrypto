"""Importing this package registers every model on Base.metadata (Alembic relies on that)."""

from app.database.models.acquisition import DataCoverage, FetchRun, RawResponse
from app.database.models.base import Base
from app.database.models.chain_data import (
    AddressTransaction,
    StoredBalanceChange,
    StoredInput,
    StoredOutput,
    StoredTransaction,
    StoredTransfer,
    StoredUnattributed,
)
from app.database.models.chains import Chain, ChainAsset
from app.database.models.intel import AddressLabel, Entity, LabelImport, LabelSource
from app.database.models.jobs import FetchJob, JobStatus
from app.database.models.risk import RiskAssessment, RiskFinding, RiskRule
from app.database.models.tenancy import Organization, User
from app.database.models.usage import ApiUsage

__all__ = [
    "AddressLabel",
    "AddressTransaction",
    "ApiUsage",
    "Base",
    "Chain",
    "ChainAsset",
    "DataCoverage",
    "Entity",
    "FetchJob",
    "FetchRun",
    "JobStatus",
    "LabelImport",
    "LabelSource",
    "Organization",
    "RawResponse",
    "RiskAssessment",
    "RiskFinding",
    "RiskRule",
    "StoredBalanceChange",
    "StoredInput",
    "StoredOutput",
    "StoredTransaction",
    "StoredTransfer",
    "StoredUnattributed",
    "User",
]
