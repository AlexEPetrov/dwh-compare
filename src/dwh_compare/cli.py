"""CLI entrypoint."""

from __future__ import annotations

import logging
import sys

import click

from dwh_compare.comparator import run_comparison
from dwh_compare.config import load_config, load_registry
from dwh_compare.db import connect
from dwh_compare.models import Period
from dwh_compare.report import print_report, write_json, write_json_summary


def _setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


@click.group()
@click.version_option(version="0.1.0", prog_name="dwh-compare")
def main() -> None:
    """Compare data between two PostgreSQL DWH instances."""


@main.command("run")
@click.option(
    "--config",
    "-c",
    "config_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--registry",
    "-r",
    "registry_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option(
    "--period",
    "-p",
    default="yesterday",
    show_default=True,
    help="yesterday | today | YYYY-MM-DD | YYYY-MM-DD:YYYY-MM-DD | all",
)
@click.option("--table", "-t", "tables", multiple=True)
@click.option("--schema", "-s", "schemas", multiple=True)
@click.option("--json-out", type=click.Path(dir_okay=False), default=None)
@click.option("--json-summary", type=click.Path(dir_okay=False), default=None)
@click.option("--verbose", "-v", is_flag=True)
@click.option("--fail-on-diff/--no-fail-on-diff", default=True, show_default=True)
def run_cmd(
    config_path: str,
    registry_path: str,
    period: str,
    tables: tuple[str, ...],
    schemas: tuple[str, ...],
    json_out: str | None,
    json_summary: str | None,
    verbose: bool,
    fail_on_diff: bool,
) -> None:
    """Compare registered tables (MD5 of all columns except ignore list)."""
    _setup_logging(verbose)
    config = load_config(config_path)
    registry = load_registry(registry_path)

    selected = registry.enabled_tables(
        only=list(tables) or None,
        schemas=list(schemas) or None,
    )
    if not selected:
        click.echo("No enabled tables matched the selection.", err=True)
        sys.exit(2)

    period_obj: Period | None
    if period.strip().lower() == "all":
        period_obj = None
    else:
        period_obj = Period.parse(period)

    with (
        connect(config.source_db) as source_conn,
        connect(config.target_db) as target_conn,
    ):
        report = run_comparison(
            source_conn, target_conn, selected, config, period_obj
        )

    print_report(report)
    if json_out:
        write_json(report, json_out)
        click.echo(f"JSON report: {json_out}")
    if json_summary:
        write_json_summary(report, json_summary)
        click.echo(f"JSON summary: {json_summary}")

    if fail_on_diff and not report.ok:
        sys.exit(1)


@main.command("list-tables")
@click.option(
    "--registry",
    "-r",
    "registry_path",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
)
@click.option("--all", "show_all", is_flag=True)
def list_tables_cmd(registry_path: str, show_all: bool) -> None:
    """Show tables from the registry."""
    registry = load_registry(registry_path)
    for t in registry.tables:
        if not show_all and not t.enabled:
            continue
        flag = "on " if t.enabled else "off"
        date_col = t.date_column or "(auto)"
        click.echo(f"[{flag}] {t.full_name:40} date={date_col}")


if __name__ == "__main__":
    main()
