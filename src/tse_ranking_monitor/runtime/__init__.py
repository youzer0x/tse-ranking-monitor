"""Unattended-routine contracts and low-overhead observability."""

from .contract import (
    CONTRACT_DOCUMENT,
    CONTRACT_LOCK,
    LOCKED_SOURCES,
    build_contract_lock,
    verify_contract_lock,
)

__all__ = [
    "CONTRACT_DOCUMENT",
    "CONTRACT_LOCK",
    "LOCKED_SOURCES",
    "build_contract_lock",
    "verify_contract_lock",
]
