"""Human-readable and machine-readable reports."""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from dwh_compare.models import RunReport, TableResult

console = Console()


def print_report(report: RunReport) -> None:
    summary = report.summary
    period_s = (
        f"{report.period.start} .. {report.period.end}" if report.period else "full table"
    )
    console.print()
    console.rule("[bold]DWH Compare Report")
    console.print(f"Period: [cyan]{period_s}[/cyan]")
    if report.finished_at:
        console.print(
            f"Finished: {report.finished_at.isoformat()}  "
            f"(started {report.started_at.isoformat()})"
        )
    console.print(
        f"Tables: {summary['tables']}  "
        f"[green]ok={summary['ok']}[/green]  "
        f"[yellow]differ={summary['differ']}[/yellow]  "
        f"[red]error={summary['error']}[/red]  "
        f"skipped={summary['skipped']}"
    )
    console.print()

    overview = Table(show_header=True, header_style="bold")
    overview.add_column("Table")
    overview.add_column("Status")
    overview.add_column("Src", justify="right")
    overview.add_column("Tgt", justify="right")
    overview.add_column("Match", justify="right")
    overview.add_column("Only src", justify="right")
    overview.add_column("Only tgt", justify="right")
    overview.add_column("≠ cols", justify="right")

    for r in report.results:
        style = {
            "ok": "green",
            "differ": "yellow",
            "error": "red",
            "skipped": "dim",
        }.get(r.status, "")
        overview.add_row(
            r.table,
            f"[{style}]{r.status}[/{style}]",
            str(r.source_count),
            str(r.target_count),
            str(r.matched),
            str(r.missing_in_target),
            str(r.missing_in_source),
            str(r.values_differ),
        )
    console.print(overview)

    for r in report.results:
        if r.status == "error":
            console.print(f"\n[red]ERROR[/red] {r.table}: {r.error}")
        elif r.status == "skipped":
            console.print(f"\n[dim]SKIP[/dim] {r.table}: {r.error}")
        elif r.has_diffs and r.samples:
            _print_samples(r)


def _print_samples(result: TableResult) -> None:
    console.print(f"\n[yellow]Diffs[/yellow] {result.table}:")
    for sample in result.samples:
        key_s = ", ".join(f"{k}={v!r}" for k, v in sample.key.items() if k != "md5")
        header = f"  • [{sample.status}]"
        if key_s:
            header = f"{header} {key_s}"
        console.print(header)
        for col in sample.columns:
            console.print(
                f"      {col.column}:  source={col.source!r}  target={col.target!r}"
            )


def write_json(report: RunReport, path: str | Path) -> None:
    Path(path).write_text(report.model_dump_json(indent=2), encoding="utf-8")


def write_json_summary(report: RunReport, path: str | Path) -> None:
    payload = {
        "started_at": report.started_at.isoformat(),
        "finished_at": report.finished_at.isoformat() if report.finished_at else None,
        "period": (
            {"start": report.period.start.isoformat(), "end": report.period.end.isoformat()}
            if report.period
            else None
        ),
        "ok": report.ok,
        "summary": report.summary,
        "results": [
            {
                "table": r.table,
                "status": r.status,
                "source_count": r.source_count,
                "target_count": r.target_count,
                "matched": r.matched,
                "missing_in_target": r.missing_in_target,
                "missing_in_source": r.missing_in_source,
                "values_differ": r.values_differ,
                "date_column": r.date_column,
                "error": r.error,
                "sample_count": len(r.samples),
            }
            for r in report.results
        ],
    }
    Path(path).write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
