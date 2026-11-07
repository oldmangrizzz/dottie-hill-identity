"""Five AEGIS intelligences, each with its own scoped memory client.

Assembly only: this package wires identity to the live dottie-memory service and proves the
service enforces the boundary. It does not define the memory namespace topology — agent
scoping is a MECHANISM; the topology is unsettled, the principal not having specified one.
"""
from .client import (DEFAULT_BASE_URL, MemoryClient, MemoryDenied, MemoryFailure,
                     MemoryHTTPError, MemoryUnreachable, MemoryUnscoped)
from .registry import AEGIS, AGENT_IDS, EXCLUDED, clients, identity, identity_path

__all__ = [
    "DEFAULT_BASE_URL", "MemoryClient", "MemoryDenied", "MemoryFailure",
    "MemoryHTTPError", "MemoryUnreachable", "MemoryUnscoped",
    "AEGIS", "AGENT_IDS", "EXCLUDED", "clients", "identity", "identity_path",
]
