"""Compact a DuckDB database by copying it into a fresh file."""

from .core import (
    CompactionResult,
    RestoreResult,
    compact_database,
    human_size,
    replace_with_compacted,
    restore_indexes,
)

__all__ = [
    "CompactionResult",
    "RestoreResult",
    "compact_database",
    "human_size",
    "replace_with_compacted",
    "restore_indexes",
]
