"""Read-only database access for the agent runtime.

Generated tools never receive database credentials and are forbidden from
importing ``sqlite3``. The agent itself may answer read-only questions through
this module, whose safety is enforced twice:

1. The SQL must be a single SELECT / WITH / EXPLAIN / read-only PRAGMA.
2. A SQLite authorizer denies every write, DDL, ATTACH and non-allowlisted
   PRAGMA at the engine level, so a bypass in the SQL check still cannot write.

This mirrors the read-only mode exposed by the official MCP database servers.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

ALLOWED_PRAGMAS = {
    "table_info", "table_xinfo", "index_list", "index_info",
    "foreign_key_list", "database_list", "compile_options",
}

_DENIED_ACTIONS = {
    getattr(sqlite3, name) for name in (
        "SQLITE_INSERT", "SQLITE_UPDATE", "SQLITE_DELETE",
        "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE",
        "SQLITE_CREATE_TEMP_INDEX", "SQLITE_CREATE_TEMP_TABLE",
        "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW",
        "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW",
        "SQLITE_DROP_INDEX", "SQLITE_DROP_TABLE",
        "SQLITE_DROP_TEMP_INDEX", "SQLITE_DROP_TEMP_TABLE",
        "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW",
        "SQLITE_DROP_TRIGGER", "SQLITE_DROP_VIEW",
        "SQLITE_ALTER_TABLE", "SQLITE_REINDEX", "SQLITE_ANALYZE",
        "SQLITE_CREATE_VTABLE", "SQLITE_DROP_VTABLE",
        "SQLITE_ATTACH", "SQLITE_DETACH",
    ) if hasattr(sqlite3, name)
}


class ReadOnlyError(RuntimeError):
    """Raised when a caller attempts a write or an unsafe statement."""


class ReadOnlySQLite:
    """A file-backed SQLite connection that cannot mutate the database."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).resolve()
        if not self.path.is_file():
            raise FileNotFoundError(f"database not found: {self.path}")
        uri = f"file:{self.path.as_posix()}?mode=ro"
        self._conn = sqlite3.connect(uri, uri=True, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.set_authorizer(self._authorize)

    def _authorize(
        self, action: int, arg1: str | None, arg2: str | None,
        dbname: str | None, source: str | None,
    ) -> int:
        if action in _DENIED_ACTIONS:
            return sqlite3.SQLITE_DENY
        if action == getattr(sqlite3, "SQLITE_PRAGMA", -1):
            if (arg1 or "").lower() not in ALLOWED_PRAGMAS:
                return sqlite3.SQLITE_DENY
        if action == getattr(sqlite3, "SQLITE_FUNCTION", -1):
            if (arg2 or "").lower() in {"load_extension", "writefile", "readfile"}:
                return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    def query(
        self,
        sql: str,
        params: Iterable[Any] = (),
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        statement = sql.strip().rstrip(";").strip()
        if not statement:
            raise ReadOnlyError("empty statement")
        if ";" in statement:
            raise ReadOnlyError("multiple statements are not allowed")
        keyword = statement.split(None, 1)[0].lower()
        if keyword not in {"select", "with", "explain", "pragma"}:
            raise ReadOnlyError(f"only read statements are allowed, got {keyword!r}")
        if keyword == "pragma":
            name = statement[6:].strip().split("(", 1)[0].split("=", 1)[0].strip().lower()
            if name not in ALLOWED_PRAGMAS:
                raise ReadOnlyError(f"pragma {name!r} is not allowlisted")
        try:
            cursor = self._conn.execute(statement, tuple(params))
            rows = cursor.fetchmany(max(1, min(int(limit), 1000)))
        except sqlite3.DatabaseError as exc:
            raise ReadOnlyError(f"query rejected: {exc}") from exc
        return [dict(row) for row in rows]

    def tables(self) -> list[str]:
        rows = self.query(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name",
            limit=1000,
        )
        return [row["name"] for row in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ReadOnlySQLite":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class ReadOnlyPostgres:
    """Optional PostgreSQL reader; requires ``pip install psycopg[binary]``.

    The transaction is set read-only before any user SQL runs, so the server
    itself rejects writes even if the statement check is bypassed.
    """

    def __init__(self, dsn: str) -> None:
        try:
            import psycopg  # type: ignore
        except ImportError as exc:  # pragma: no cover - optional dependency
            raise RuntimeError(
                "PostgreSQL support needs 'pip install psycopg[binary]'"
            ) from exc
        self._conn = psycopg.connect(dsn)
        self._conn.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")

    def query(
        self, sql: str, params: Iterable[Any] = (), *, limit: int = 100
    ) -> list[dict[str, Any]]:
        statement = sql.strip().rstrip(";").strip()
        if not statement or ";" in statement:
            raise ReadOnlyError("exactly one statement is required")
        keyword = statement.split(None, 1)[0].lower()
        if keyword not in {"select", "with", "explain"}:
            raise ReadOnlyError(f"only read statements are allowed, got {keyword!r}")
        try:
            with self._conn.cursor() as cursor:
                cursor.execute(statement, tuple(params))
                columns = [d.name for d in cursor.description or []]
                rows = cursor.fetchmany(max(1, min(int(limit), 1000)))
        except Exception as exc:
            raise ReadOnlyError(f"query rejected: {exc}") from exc
        return [dict(zip(columns, row)) for row in rows]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "ReadOnlyPostgres":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()
