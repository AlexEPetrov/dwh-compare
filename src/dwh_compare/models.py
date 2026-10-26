"""Configuration and registry models."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from pydantic import BaseModel, Field, model_validator


DEFAULT_IGNORE_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "rid",
        "source_id",
        "created_at",
        "modified_at",
        "updated_at",
        "deleted_at",
        "created_by",
        "modified_by",
        "updated_by",
        "row_hash",
        "etl_batch_id",
        "load_id",
        "_loaded_at",
        "_updated_at",
    }
)

DEFAULT_DATE_CANDIDATES: tuple[str, ...] = (
    "created_at",
    "modified_at",
    "updated_at",
    "load_dt",
    "loaded_at",
    "_loaded_at",
)


class ServerConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    user: str
    password: str = ""
    sslmode: str | None = None
    connect_timeout: int = 30


class DatabaseConfig(BaseModel):
    host: str = "localhost"
    port: int = 5432
    database: str
    user: str
    password: str = ""
    sslmode: str | None = None
    connect_timeout: int = 30

    @classmethod
    def from_server(cls, server: ServerConfig, database: str) -> DatabaseConfig:
        return cls(database=database, **server.model_dump())

    def dsn(self) -> str:
        parts = [
            f"host={self.host}",
            f"port={self.port}",
            f"dbname={self.database}",
            f"user={self.user}",
            f"connect_timeout={self.connect_timeout}",
        ]
        if self.password:
            parts.append(f"password={self.password}")
        if self.sslmode:
            parts.append(f"sslmode={self.sslmode}")
        return " ".join(parts)


class AppConfig(BaseModel):
    server: ServerConfig | None = None
    source_database: str | None = None
    target_database: str | None = None
    source: DatabaseConfig | None = None
    target: DatabaseConfig | None = None

    ignore_columns: list[str] = Field(
        default_factory=lambda: sorted(DEFAULT_IGNORE_COLUMNS),
        description="Technical columns excluded from MD5",
    )
    sample_limit: int | None = 20
    fetch_size: int = 10_000

    @model_validator(mode="after")
    def resolve_endpoints(self) -> AppConfig:
        if self.source is not None and self.target is not None:
            return self
        if self.server and self.source_database and self.target_database:
            object.__setattr__(
                self, "source", DatabaseConfig.from_server(self.server, self.source_database)
            )
            object.__setattr__(
                self, "target", DatabaseConfig.from_server(self.server, self.target_database)
            )
            return self
        raise ValueError(
            "Config needs server + source_database + target_database, "
            "or full source + target blocks"
        )

    @property
    def source_db(self) -> DatabaseConfig:
        assert self.source is not None
        return self.source

    @property
    def target_db(self) -> DatabaseConfig:
        assert self.target is not None
        return self.target


class TableEntry(BaseModel):
    """Table from registry. Fields to compare are NOT listed — all except ignore."""

    schema_name: str = Field(alias="schema")
    table: str
    enabled: bool = True
    # Optional override; by default PK is taken from Postgres
    keys: list[str] | None = None
    ignore_columns: list[str] = Field(default_factory=list)
    date_column: str | None = None
    where: str | None = None
    label: str | None = None

    model_config = {"populate_by_name": True}

    @property
    def full_name(self) -> str:
        return f"{self.schema_name}.{self.table}"

    @property
    def display_name(self) -> str:
        return self.label or self.full_name

    def effective_ignore(self, global_ignore: list[str]) -> set[str]:
        return {c.lower() for c in global_ignore} | {c.lower() for c in self.ignore_columns}


class Registry(BaseModel):
    tables: list[TableEntry] = Field(default_factory=list)

    def enabled_tables(
        self,
        only: list[str] | None = None,
        schemas: list[str] | None = None,
    ) -> list[TableEntry]:
        result = [t for t in self.tables if t.enabled]
        if schemas:
            schema_set = {s.lower() for s in schemas}
            result = [t for t in result if t.schema_name.lower() in schema_set]
        if only:
            names = {n.lower() for n in only}
            result = [
                t
                for t in result
                if t.full_name.lower() in names or t.table.lower() in names
            ]
        return result


class Period(BaseModel):
    start: date
    end: date

    @model_validator(mode="after")
    def check_order(self) -> Period:
        if self.end < self.start:
            raise ValueError("period end must be >= start")
        return self

    @classmethod
    def yesterday(cls, today: date | None = None) -> Period:
        d = (today or date.today()) - timedelta(days=1)
        return cls(start=d, end=d)

    @classmethod
    def today(cls, today: date | None = None) -> Period:
        d = today or date.today()
        return cls(start=d, end=d)

    @classmethod
    def parse(cls, value: str, today: date | None = None) -> Period:
        today = today or date.today()
        value = value.strip().lower()
        if value == "yesterday":
            return cls.yesterday(today)
        if value == "today":
            return cls.today(today)
        if ":" in value:
            a, b = value.split(":", 1)
            return cls(start=date.fromisoformat(a.strip()), end=date.fromisoformat(b.strip()))
        d = date.fromisoformat(value)
        return cls(start=d, end=d)

    def as_datetimes(self) -> tuple[datetime, datetime]:
        start_dt = datetime.combine(self.start, datetime.min.time())
        end_dt = datetime.combine(self.end + timedelta(days=1), datetime.min.time())
        return start_dt, end_dt


class ColumnDiff(BaseModel):
    column: str
    source: Any = None
    target: Any = None


class RowDiff(BaseModel):
    status: str  # missing_in_target | missing_in_source | values_differ
    key: dict[str, Any] = Field(default_factory=dict)
    # Only columns whose values differ (or all payload if row is missing on one side)
    columns: list[ColumnDiff] = Field(default_factory=list)


class TableResult(BaseModel):
    table: str
    status: str  # ok | differ | error | skipped
    source_count: int = 0
    target_count: int = 0
    matched: int = 0
    missing_in_target: int = 0
    missing_in_source: int = 0
    values_differ: int = 0
    samples: list[RowDiff] = Field(default_factory=list)
    error: str | None = None
    compared_columns: list[str] = Field(default_factory=list)
    date_column: str | None = None

    @property
    def has_diffs(self) -> bool:
        return (
            self.missing_in_target > 0
            or self.missing_in_source > 0
            or self.values_differ > 0
            or self.status == "error"
        )


class RunReport(BaseModel):
    started_at: datetime
    finished_at: datetime | None = None
    period: Period | None = None
    results: list[TableResult] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(not r.has_diffs for r in self.results)

    @property
    def summary(self) -> dict[str, int]:
        return {
            "tables": len(self.results),
            "ok": sum(1 for r in self.results if r.status == "ok"),
            "differ": sum(1 for r in self.results if r.status == "differ"),
            "error": sum(1 for r in self.results if r.status == "error"),
            "skipped": sum(1 for r in self.results if r.status == "skipped"),
        }
