"""AstrBot adapters for the embedded OpenViking memory core."""

from .adapter import MemoryUpsertResult, OpenVikingMemoryAdapter, SessionCommitResult
from .migration import (
    MigrationBatchResult,
    MigrationItemResult,
    OpenVikingMigrationAdapter,
)
from .provider import OpenVikingProviderBridge, ProviderRerankResult
from .recall import OpenVikingRecallAdapter, OpenVikingRecallResult
from .workspace import (
    OpenVikingWorkspace,
    WorkspaceManifest,
    WorkspacePathError,
    WorkspaceVersionError,
)

__all__ = [
    "OpenVikingWorkspace",
    "OpenVikingMemoryAdapter",
    "OpenVikingMigrationAdapter",
    "OpenVikingProviderBridge",
    "OpenVikingRecallAdapter",
    "OpenVikingRecallResult",
    "ProviderRerankResult",
    "MemoryUpsertResult",
    "MigrationBatchResult",
    "MigrationItemResult",
    "SessionCommitResult",
    "WorkspaceManifest",
    "WorkspacePathError",
    "WorkspaceVersionError",
]
