"""Compare by natural key + MD5; ignored PK (rid/id) ⇒ MD5-only mode."""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Sequence

from psycopg2.extensions import connection as PgConnection

from dwh_compare.db import (
    fetch_example_by_md5,
    fetch_payload_by_keys,
    list_column_types,
    list_primary_key,
    load_key_md5_map,
    load_md5_list,
    pick_date_column,
    resolve_key_and_payload,
    stringify,
)
from dwh_compare.models import (
    AppConfig,
    ColumnDiff,
    Period,
    RowDiff,
    RunReport,
    TableEntry,
    TableResult,
)

logger = logging.getLogger(__name__)


def compare_table(
    source_conn: PgConnection,
    target_conn: PgConnection,
    entry: TableEntry,
    config: AppConfig,
    period: Period | None,
) -> TableResult:
    try:
        src_types = list_column_types(source_conn, entry)
        tgt_types = list_column_types(target_conn, entry)
        src_cols = list(src_types.keys())
        tgt_cols = list(tgt_types.keys())
        if not src_cols:
            return TableResult(
                table=entry.display_name,
                status="error",
                error=f"Table not found on source: {entry.full_name}",
            )
        if not tgt_cols:
            return TableResult(
                table=entry.display_name,
                status="error",
                error=f"Table not found on target: {entry.full_name}",
            )

        common = [c for c in src_cols if c.lower() in {x.lower() for x in tgt_cols}]
        # Prefer source type names for hashing (same logical types assumed)
        col_types = {c: src_types[c] for c in common}

        if entry.keys:
            raw_keys = entry.keys
        else:
            raw_keys = list_primary_key(source_conn, entry)

        key_cols, payload = resolve_key_and_payload(
            common, entry, config.ignore_columns, raw_keys or []
        )
        date_col = pick_date_column(entry, common)

        if period is not None and not date_col:
            return TableResult(
                table=entry.display_name,
                status="skipped",
                error="No date column (set date_column or use -p all)",
                compared_columns=payload,
            )

        # PK fully in ignore (e.g. rid) → compare multisets of MD5 only
        if not key_cols:
            logger.info(
                "Comparing %s (MD5-only, PK ignored; %d payload cols, date=%s)",
                entry.full_name,
                len(payload),
                date_col,
            )
            return compare_md5_only(
                source_conn,
                target_conn,
                entry,
                payload,
                period,
                date_col,
                config,
                col_types,
            )

        logger.info(
            "Comparing %s (keys=%s, %d payload cols → MD5, date=%s)",
            entry.full_name,
            ",".join(key_cols),
            len(payload),
            date_col,
        )

        source_map = load_key_md5_map(
            source_conn,
            entry,
            key_cols,
            payload,
            period,
            date_col,
            config.fetch_size,
            col_types,
        )
        target_map = load_key_md5_map(
            target_conn,
            entry,
            key_cols,
            payload,
            period,
            date_col,
            config.fetch_size,
            col_types,
        )

        result, differ_keys = diff_md5_maps(
            source_map,
            target_map,
            key_cols,
            payload,
            entry.display_name,
            date_col,
            config.sample_limit,
        )

        if differ_keys:
            result.samples.extend(
                field_diffs_for_keys(
                    source_conn,
                    target_conn,
                    entry,
                    key_cols,
                    payload,
                    differ_keys,
                )
            )

        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed comparing %s", entry.full_name)
        return TableResult(
            table=entry.display_name,
            status="error",
            error=str(exc),
        )


def compare_md5_only(
    source_conn: PgConnection,
    target_conn: PgConnection,
    entry: TableEntry,
    payload: Sequence[str],
    period: Period | None,
    date_col: str | None,
    config: AppConfig,
    column_types: dict[str, str] | None = None,
) -> TableResult:
    """No usable key (PK ignored) — compare multisets of row MD5."""
    src = Counter(
        load_md5_list(
            source_conn,
            entry,
            payload,
            period,
            date_col,
            config.fetch_size,
            column_types,
        )
    )
    tgt = Counter(
        load_md5_list(
            target_conn,
            entry,
            payload,
            period,
            date_col,
            config.fetch_size,
            column_types,
        )
    )

    only_src = src - tgt
    only_tgt = tgt - src
    matched = sum((src & tgt).values())

    limit = config.sample_limit if config.sample_limit is not None else 10**9
    samples: list[RowDiff] = []

    # Lazy: resolve a few orphan hashes → real field values (not raw md5 in report)
    for md5, cnt in only_src.items():
        if len(samples) >= limit:
            break
        row = fetch_example_by_md5(
            source_conn, entry, payload, period, date_col, md5
        )
        samples.append(
            RowDiff(
                status="missing_in_target",
                key={"×": cnt} if cnt > 1 else {},
                columns=[
                    ColumnDiff(column=c, source=(row or {}).get(c), target=None)
                    for c in payload
                ],
            )
        )
    for md5, cnt in only_tgt.items():
        if len(samples) >= limit:
            break
        row = fetch_example_by_md5(
            target_conn, entry, payload, period, date_col, md5
        )
        samples.append(
            RowDiff(
                status="missing_in_source",
                key={"×": cnt} if cnt > 1 else {},
                columns=[
                    ColumnDiff(column=c, source=None, target=(row or {}).get(c))
                    for c in payload
                ],
            )
        )

    miss_t = sum(only_src.values())
    miss_s = sum(only_tgt.values())
    has_diff = bool(miss_t or miss_s)
    return TableResult(
        table=entry.display_name,
        status="differ" if has_diff else "ok",
        source_count=sum(src.values()),
        target_count=sum(tgt.values()),
        matched=matched,
        missing_in_target=miss_t,
        missing_in_source=miss_s,
        values_differ=0,
        samples=samples,
        compared_columns=list(payload),
        date_column=date_col,
    )


def _key_dict(key: tuple, key_cols: Sequence[str]) -> dict:
    return {key_cols[i]: stringify(key[i]) for i in range(len(key_cols))}


def diff_md5_maps(
    source_map: dict[tuple, str],
    target_map: dict[tuple, str],
    key_cols: Sequence[str],
    payload_cols: Sequence[str],
    table_name: str,
    date_column: str | None,
    sample_limit: int | None,
) -> tuple[TableResult, list[tuple]]:
    limit = sample_limit if sample_limit is not None else 10**9

    src_keys = set(source_map)
    tgt_keys = set(target_map)
    common = src_keys & tgt_keys

    miss_tgt = sorted(src_keys - tgt_keys)
    miss_src = sorted(tgt_keys - src_keys)

    matched = 0
    differ_all: list[tuple] = []
    for key in common:
        if source_map[key] == target_map[key]:
            matched += 1
        else:
            differ_all.append(key)

    samples: list[RowDiff] = []
    for key in miss_tgt[:limit]:
        samples.append(
            RowDiff(status="missing_in_target", key=_key_dict(key, key_cols))
        )
    remain = max(0, limit - len(samples))
    for key in miss_src[:remain]:
        samples.append(
            RowDiff(status="missing_in_source", key=_key_dict(key, key_cols))
        )
    remain = max(0, limit - len(samples))
    differ_sample = differ_all[:remain]

    has_diff = bool(miss_tgt or miss_src or differ_all)
    result = TableResult(
        table=table_name,
        status="differ" if has_diff else "ok",
        source_count=len(source_map),
        target_count=len(target_map),
        matched=matched,
        missing_in_target=len(miss_tgt),
        missing_in_source=len(miss_src),
        values_differ=len(differ_all),
        samples=samples,
        compared_columns=list(payload_cols),
        date_column=date_column,
    )
    return result, differ_sample


def field_diffs_for_keys(
    source_conn: PgConnection,
    target_conn: PgConnection,
    entry: TableEntry,
    key_cols: Sequence[str],
    payload_cols: Sequence[str],
    keys: Sequence[tuple],
) -> list[RowDiff]:
    if not keys:
        return []

    src_rows = fetch_payload_by_keys(
        source_conn, entry, key_cols, payload_cols, keys
    )
    tgt_rows = fetch_payload_by_keys(
        target_conn, entry, key_cols, payload_cols, keys
    )

    samples: list[RowDiff] = []
    for key in keys:
        src = src_rows.get(key, {})
        tgt = tgt_rows.get(key, {})
        cols = [
            ColumnDiff(column=c, source=src.get(c), target=tgt.get(c))
            for c in payload_cols
            if src.get(c) != tgt.get(c)
        ]
        samples.append(
            RowDiff(
                status="values_differ",
                key=_key_dict(key, key_cols),
                columns=cols,
            )
        )
    return samples


def run_comparison(
    source_conn: PgConnection,
    target_conn: PgConnection,
    tables: Sequence[TableEntry],
    config: AppConfig,
    period: Period | None,
) -> RunReport:
    report = RunReport(started_at=datetime.now(timezone.utc), period=period)
    for entry in tables:
        report.results.append(
            compare_table(source_conn, target_conn, entry, config, period)
        )
    report.finished_at = datetime.now(timezone.utc)
    return report
