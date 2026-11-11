"""Importing this package registers every model on Base.metadata (Alembic relies on that)."""

from app.database.models.access import ApiKey, AuditLog
from app.database.models.acquisition import DataCoverage, FetchRun, RawResponse
from app.database.models.base import Base
from app.database.models.cases import Case, CaseEvent, CaseLink
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
from app.database.models.incidents import (
    Incident,
    IncidentAddress,
    IncidentAmount,
    IncidentEvidence,
    IncidentFlow,
    IncidentUpdate,
)
from app.database.models.intel import AddressLabel, Entity, LabelImport, LabelSource
from app.database.models.jobs import FetchJob, JobStatus
from app.database.models.monitoring import Alert, MonitoredAddress
from app.database.models.risk import RiskAssessment, RiskFinding, RiskRule
from app.database.models.tenancy import Organization, User
from app.database.models.usage import ApiUsage

__all__ = [
    "AddressLabel",
    "AddressTransaction",
    "Alert",
    "ApiKey",
    "ApiUsage",
    "AuditLog",
    "Base",
    "Case",
    "CaseEvent",
    "CaseLink",
    "Chain",
    "ChainAsset",
    "DataCoverage",
    "Entity",
    "FetchJob",
    "FetchRun",
    "Incident",
    "IncidentAddress",
    "IncidentAmount",
    "IncidentEvidence",
    "IncidentFlow",
    "IncidentUpdate",
    "JobStatus",
    "LabelImport",
    "LabelSource",
    "MonitoredAddress",
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
