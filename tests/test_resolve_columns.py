from dwh_compare.db import pick_date_column, resolve_key_and_payload
from dwh_compare.models import AppConfig, TableEntry


def test_ignored_pk_dropped_from_keys():
    entry = TableEntry(schema="s", table="t")
    keys, payload = resolve_key_and_payload(
        ["rid", "order_nk", "amount", "created_at"],
        entry,
        global_ignore=["rid", "created_at"],
        key_columns=["rid"],
    )
    assert keys == []  # rid ignored ⇒ no match key
    assert payload == ["order_nk", "amount"]


def test_natural_key_kept():
    entry = TableEntry(schema="s", table="t")
    keys, payload = resolve_key_and_payload(
        ["rid", "order_nk", "amount"],
        entry,
        global_ignore=["rid"],
        key_columns=["order_nk"],
    )
    assert keys == ["order_nk"]
    assert payload == ["amount"]



def test_pick_date_auto():
    entry = TableEntry(schema="s", table="t")
    assert pick_date_column(entry, ["id", "created_at", "amount"]) == "created_at"


def test_single_server_config():
    cfg = AppConfig.model_validate(
        {
            "server": {"host": "db", "user": "u", "password": "p", "port": 5432},
            "source_database": "prod",
            "target_database": "test",
        }
    )
    assert cfg.source_db.database == "prod"
