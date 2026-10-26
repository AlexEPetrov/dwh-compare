"""PostgreSQL helpers — key+MD5 map (no ORDER BY); lazy row fetch for diffs."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator, Sequence

import psycopg2
import psycopg2.extras
import psycopg2.sql as sql
from psycopg2.extensions import connection as PgConnection

from dwh_compare.models import (
    DEFAULT_DATE_CANDIDATES,
    DatabaseConfig,
    Period,
    TableEntry,
)

_NULL_SENTINEL = sql.SQL("'\\x00'")
_SEP = sql.SQL("'\\x1f'")


@contextmanager
def connect(cfg: DatabaseConfig) -> Iterator[PgConnection]:
    conn = psycopg2.connect(cfg.dsn())
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 0")
        yield conn
    finally:
        conn.close()


def table_ident(entry: TableEntry) -> sql.Composed:
    return sql.SQL("{}.{}").format(
        sql.Identifier(entry.schema_name),
        sql.Identifier(entry.table),
    )


def list_columns(conn: PgConnection, entry: TableEntry) -> list[str]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = %s AND table_name = %s
            ORDER BY ordinal_position
            """,
            (entry.schema_name, entry.table),
        )
        return [r["column_name"] for r in cur.fetchall()]


def list_primary_key(conn: PgConnection, entry: TableEntry) -> list[str]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """
            SELECT a.attname AS column_name
            FROM pg_index i
            JOIN pg_attribute a
              ON a.attrelid = i.indrelid AND a.attnum = ANY (i.indkey)
            JOIN pg_class c ON c.oid = i.indrelid
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE i.indisprimary
              AND n.nspname = %s
              AND c.relname = %s
            ORDER BY array_position(i.indkey, a.attnum)
            """,
            (entry.schema_name, entry.table),
        )
        return [r["column_name"] for r in cur.fetchall()]


def resolve_key_and_payload(
    all_columns: Sequence[str],
    entry: TableEntry,
    global_ignore: list[str],
    key_columns: Sequence[str],
) -> tuple[list[str], list[str]]:
    """
    Keys used for pairing — PK/registry keys EXCEPT those in ignore
    (surrogate rid/id in ignore ⇒ not used for matching).

    Payload — all non-ignored columns (for MD5 / field diffs).
    If no keys left, caller should use MD5-only multiset compare.
    """
    ignore = entry.effective_ignore(global_ignore)
    cols_lower = {c.lower(): c for c in all_columns}

    for k in key_columns:
        if k.lower() not in cols_lower:
            raise ValueError(f"key column '{k}' not found in {entry.full_name}")

    # Drop technical/surrogate keys that user put into ignore
    key_cols = [
        cols_lower[k.lower()]
        for k in key_columns
        if k.lower() not in ignore
    ]
    key_lower = {k.lower() for k in key_cols}
    payload = [
        c
        for c in all_columns
        if c.lower() not in ignore and c.lower() not in key_lower
    ]
    return key_cols, payload


def load_md5_list(
    conn: PgConnection,
    entry: TableEntry,
    payload_columns: Sequence[str],
    period: Period | None,
    date_column: str | None,
    fetch_size: int,
) -> list[str]:
    """Stream row MD5s (no key) — for when PK is fully ignored."""
    where_sql, params = build_where(entry, period, date_column)
    query = sql.SQL("SELECT {} AS row_md5 FROM {}{}").format(
        _md5_expr(payload_columns),
        table_ident(entry),
        where_sql,
    )
    result: list[str] = []
    cur = conn.cursor(
        name=f"cmp_{entry.schema_name}_{entry.table}",
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    try:
        cur.itersize = fetch_size
        cur.execute(query, params)
        while True:
            rows = cur.fetchmany(fetch_size)
            if not rows:
                break
            for row in rows:
                result.append(row["row_md5"])
    finally:
        cur.close()
    return result



def pick_date_column(entry: TableEntry, columns: Sequence[str]) -> str | None:
    if entry.date_column:
        return entry.date_column
    lower = {c.lower(): c for c in columns}
    for name in DEFAULT_DATE_CANDIDATES:
        if name in lower:
            return lower[name]
    return None


def build_where(
    entry: TableEntry,
    period: Period | None,
    date_column: str | None,
) -> tuple[sql.Composed, list[Any]]:
    parts: list[sql.Composed] = []
    params: list[Any] = []

    if entry.where:
        parts.append(sql.SQL("({})").format(sql.SQL(entry.where)))

    if period is not None:
        if not date_column:
            raise ValueError(
                f"Period filter requested but no date column for {entry.full_name}"
            )
        start_dt, end_dt = period.as_datetimes()
        parts.append(
            sql.SQL("{} >= %s AND {} < %s").format(
                sql.Identifier(date_column),
                sql.Identifier(date_column),
            )
        )
        params.extend([start_dt, end_dt])

    if not parts:
        return sql.SQL(""), []

    return sql.SQL(" WHERE ") + sql.SQL(" AND ").join(parts), params


def _md5_expr(payload_columns: Sequence[str]) -> sql.Composed:
    if not payload_columns:
        return sql.SQL("md5('')")

    pieces: list[sql.Composable] = []
    for i, col in enumerate(payload_columns):
        if i:
            pieces.append(_SEP)
        pieces.append(
            sql.SQL("COALESCE({}::text, {})").format(
                sql.Identifier(col),
                _NULL_SENTINEL,
            )
        )
    return sql.SQL("md5({})").format(sql.SQL(" || ").join(pieces))


def stringify(value: Any) -> Any:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    type_name = type(value).__name__
    if type_name == "Decimal":
        return format(value, "f")
    if isinstance(value, (bytes, memoryview)):
        return bytes(value).hex()
    return value


def load_key_md5_map(
    conn: PgConnection,
    entry: TableEntry,
    key_columns: Sequence[str],
    payload_columns: Sequence[str],
    period: Period | None,
    date_column: str | None,
    fetch_size: int,
) -> dict[tuple[Any, ...], str]:
    """
    Stream key + md5 into a dict. No ORDER BY (avoids heavy sort on PG).

    Only PK columns + one hash leave the server — not full row payloads.
    """
    where_sql, params = build_where(entry, period, date_column)
    key_sql = sql.SQL(", ").join(sql.Identifier(c) for c in key_columns)
    query = sql.SQL("SELECT {}, {} AS row_md5 FROM {}{}").format(
        key_sql,
        _md5_expr(payload_columns),
        table_ident(entry),
        where_sql,
    )

    result: dict[tuple[Any, ...], str] = {}
    cur = conn.cursor(
        name=f"cmp_{entry.schema_name}_{entry.table}",
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    try:
        cur.itersize = fetch_size
        cur.execute(query, params)
        while True:
            rows = cur.fetchmany(fetch_size)
            if not rows:
                break
            for row in rows:
                key = tuple(row[c] for c in key_columns)
                result[key] = row["row_md5"]
    finally:
        cur.close()
    return result


def fetch_payload_by_keys(
    conn: PgConnection,
    entry: TableEntry,
    key_columns: Sequence[str],
    payload_columns: Sequence[str],
    keys: Sequence[tuple[Any, ...]],
) -> dict[tuple[Any, ...], dict[str, Any]]:
    """Lazy: load payload only for a few PK samples (MD5 mismatches)."""
    if not keys:
        return {}

    select_cols = list(dict.fromkeys([*key_columns, *payload_columns]))
    col_sql = sql.SQL(", ").join(sql.Identifier(c) for c in select_cols)

    tuple_placeholders = []
    params: list[Any] = []
    for key in keys:
        tuple_placeholders.append(
            sql.SQL("({})").format(
                sql.SQL(", ").join(sql.Placeholder() for _ in key_columns)
            )
        )
        params.extend(key)

    query = sql.SQL("SELECT {} FROM {} WHERE ({}) IN ({})").format(
        col_sql,
        table_ident(entry),
        sql.SQL(", ").join(sql.Identifier(c) for c in key_columns),
        sql.SQL(", ").join(tuple_placeholders),
    )

    result: dict[tuple[Any, ...], dict[str, Any]] = {}
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, params)
        for row in cur.fetchall():
            key = tuple(row[c] for c in key_columns)
            result[key] = {c: stringify(row.get(c)) for c in payload_columns}
    return result


def fetch_example_by_md5(
    conn: PgConnection,
    entry: TableEntry,
    payload_columns: Sequence[str],
    period: Period | None,
    date_column: str | None,
    md5_value: str,
) -> dict[str, Any] | None:
    """One example row for a given payload MD5 (MD5-only mode samples)."""
    if not payload_columns:
        return None

    where_sql, params = build_where(entry, period, date_column)
    col_sql = sql.SQL(", ").join(sql.Identifier(c) for c in payload_columns)
    md5_expr = _md5_expr(payload_columns)

    # Filter in outer query so we don't scan more than needed beyond period
    inner = sql.SQL("SELECT {}, {} AS row_md5 FROM {}{}").format(
        col_sql, md5_expr, table_ident(entry), where_sql
    )
    query = sql.SQL(
        "SELECT * FROM ({}) AS _q WHERE row_md5 = %s LIMIT 1"
    ).format(inner)

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(query, list(params) + [md5_value])
        row = cur.fetchone()
        if not row:
            return None
        return {c: stringify(row.get(c)) for c in payload_columns}

