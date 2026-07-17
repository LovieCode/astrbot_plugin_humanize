"""AstrBot adapters for the embedded OpenViking memory core."""

from .adapter import MemoryUpsertResult, OpenVikingMemoryAdapter, SessionCommitResult
from .management import OpenVikingManagementAdapter
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
    "OpenVikingManagementAdapter",
    "OpenVikingProviderBridge",
    "OpenVikingRecallAdapter",
    "OpenVikingRecallResult",
    "ProviderRerankResult",
    "MemoryUpsertResult",
    "SessionCommitResult",
    "WorkspaceManifest",
    "WorkspacePathError",
    "WorkspaceVersionError",
]
