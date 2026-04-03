"""Memory storage backends.

Provides an abstract interface and two implementations:
- JsonFileStorage: original file-based storage (backward compatible)
- PostgresStorage: stores memory as JSONB in Postgres
"""

import json
import logging
import threading
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class MemoryStorage(ABC):
    """Abstract base class for memory storage backends."""

    @abstractmethod
    def load(self, scope: str) -> dict[str, Any]:
        """Load memory data for the given scope.

        Args:
            scope: 'global' for global memory, or an agent name for per-agent memory.

        Returns:
            The memory data dictionary, or an empty structure if none exists.
        """

    @abstractmethod
    def save(self, scope: str, data: dict[str, Any]) -> bool:
        """Save memory data for the given scope.

        Args:
            scope: 'global' for global memory, or an agent name.
            data: The full memory data dictionary.

        Returns:
            True if successful, False otherwise.
        """

    @abstractmethod
    def setup(self) -> None:
        """One-time initialization (create tables, directories, etc)."""


def _create_empty_memory() -> dict[str, Any]:
    """Create an empty memory structure."""
    return {
        "version": "1.0",
        "lastUpdated": datetime.now(timezone.utc).isoformat(),
        "user": {
            "workContext": {"summary": "", "updatedAt": ""},
            "personalContext": {"summary": "", "updatedAt": ""},
            "topOfMind": {"summary": "", "updatedAt": ""},
        },
        "history": {
            "recentMonths": {"summary": "", "updatedAt": ""},
            "earlierContext": {"summary": "", "updatedAt": ""},
            "longTermBackground": {"summary": "", "updatedAt": ""},
        },
        "facts": [],
    }


# ---------------------------------------------------------------------------
# JSON file backend
# ---------------------------------------------------------------------------


class JsonFileStorage(MemoryStorage):
    """File-based memory storage using JSON files with atomic writes."""

    def __init__(self, base_dir: Path, storage_path: str = ""):
        self._base_dir = base_dir
        self._storage_path = storage_path

    def _resolve_path(self, scope: str) -> Path:
        if scope != "global":
            return self._base_dir / "agents" / scope / "memory.json"
        if self._storage_path:
            p = Path(self._storage_path)
            return p if p.is_absolute() else self._base_dir / p
        return self._base_dir / "memory.json"

    def setup(self) -> None:
        pass

    def load(self, scope: str) -> dict[str, Any]:
        file_path = self._resolve_path(scope)
        if not file_path.exists():
            return _create_empty_memory()
        try:
            with open(file_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Failed to load memory file %s: %s", file_path, e)
            return _create_empty_memory()

    def save(self, scope: str, data: dict[str, Any]) -> bool:
        file_path = self._resolve_path(scope)
        try:
            file_path.parent.mkdir(parents=True, exist_ok=True)
            data["lastUpdated"] = datetime.now(timezone.utc).isoformat()
            temp_path = file_path.with_suffix(".tmp")
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            temp_path.replace(file_path)
            logger.info("Memory saved to %s", file_path)
            return True
        except OSError as e:
            logger.error("Failed to save memory file: %s", e)
            return False

    def get_file_mtime(self, scope: str) -> float | None:
        """Return the file modification time, used for cache invalidation."""
        file_path = self._resolve_path(scope)
        try:
            return file_path.stat().st_mtime if file_path.exists() else None
        except OSError:
            return None


# ---------------------------------------------------------------------------
# PostgreSQL backend
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS memory_store (
    scope TEXT PRIMARY KEY,
    data JSONB NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
"""

_UPSERT_SQL = """
INSERT INTO memory_store (scope, data, updated_at)
VALUES (%s, %s, NOW())
ON CONFLICT (scope) DO UPDATE
SET data = EXCLUDED.data, updated_at = NOW();
"""

_SELECT_SQL = """
SELECT data, updated_at FROM memory_store WHERE scope = %s;
"""

_SELECT_UPDATED_AT_SQL = """
SELECT updated_at FROM memory_store WHERE scope = %s;
"""


class PostgresStorage(MemoryStorage):
    """Postgres-backed memory storage using JSONB.

    Stores the full memory JSON document in a single row per scope.
    Uses the same Postgres instance as the LangGraph checkpointer.
    """

    def __init__(self, connection_string: str):
        self._connection_string = connection_string
        self._setup_done = False
        self._lock = threading.Lock()

    def _get_connection(self):
        import psycopg

        return psycopg.connect(self._connection_string, autocommit=True)

    def setup(self) -> None:
        if self._setup_done:
            return
        with self._lock:
            if self._setup_done:
                return
            try:
                with self._get_connection() as conn:
                    conn.execute(_CREATE_TABLE_SQL)
                self._setup_done = True
                logger.info("Memory store table initialized in Postgres")
            except Exception as e:
                logger.error("Failed to create memory_store table: %s", e)
                raise

    def load(self, scope: str) -> dict[str, Any]:
        self.setup()
        try:
            with self._get_connection() as conn:
                row = conn.execute(_SELECT_SQL, (scope,)).fetchone()
                if row is None:
                    return _create_empty_memory()
                data = row[0]
                if isinstance(data, str):
                    data = json.loads(data)
                return data
        except Exception as e:
            logger.error("Failed to load memory from Postgres (scope=%s): %s", scope, e)
            return _create_empty_memory()

    def save(self, scope: str, data: dict[str, Any]) -> bool:
        self.setup()
        try:
            import psycopg.types.json

            data["lastUpdated"] = datetime.now(timezone.utc).isoformat()
            with self._get_connection() as conn:
                conn.execute(
                    _UPSERT_SQL,
                    (scope, psycopg.types.json.Jsonb(data)),
                )
            logger.info("Memory saved to Postgres (scope=%s)", scope)
            return True
        except Exception as e:
            logger.error("Failed to save memory to Postgres (scope=%s): %s", scope, e)
            return False

    def get_updated_at(self, scope: str) -> float | None:
        """Return the updated_at timestamp as epoch seconds, for cache invalidation."""
        self.setup()
        try:
            with self._get_connection() as conn:
                row = conn.execute(_SELECT_UPDATED_AT_SQL, (scope,)).fetchone()
                if row is None:
                    return None
                return row[0].timestamp()
        except Exception:
            return None


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_storage: MemoryStorage | None = None
_storage_lock = threading.Lock()


def get_memory_storage() -> MemoryStorage:
    """Get the configured memory storage backend singleton."""
    global _storage
    with _storage_lock:
        if _storage is not None:
            return _storage

        from deerflow.config.memory_config import get_memory_config

        config = get_memory_config()

        if config.type == "postgres":
            conn_str = config.connection_string
            if not conn_str:
                from deerflow.config.checkpointer_config import get_checkpointer_config

                cp_config = get_checkpointer_config()
                if cp_config and cp_config.connection_string:
                    conn_str = cp_config.connection_string
                else:
                    raise ValueError(
                        "memory.connection_string is required for postgres backend "
                        "(or configure checkpointer.connection_string to reuse it)"
                    )
            _storage = PostgresStorage(conn_str)
            _storage.setup()
        else:
            from deerflow.config.paths import get_paths

            _storage = JsonFileStorage(
                base_dir=get_paths().base_dir,
                storage_path=config.storage_path,
            )
        return _storage


def reset_memory_storage() -> None:
    """Reset the storage singleton. Useful for testing."""
    global _storage
    with _storage_lock:
        _storage = None
