from datetime import date

from dwh_compare.models import Period, Registry, TableEntry


def test_period_yesterday():
    p = Period.parse("yesterday", today=date(2026, 10, 26))
    assert p.start == date(2026, 10, 25)


def test_registry_no_keys():
    reg = Registry(
        tables=[
            TableEntry(schema="mart", table="orders", enabled=True),
            TableEntry(schema="mart", table="x", enabled=False),
        ]
    )
    assert len(reg.enabled_tables()) == 1
    assert reg.enabled_tables()[0].table == "orders"


def test_list_tables_loads():
    from dwh_compare.config import load_registry

    reg = load_registry("config/registry.example.yaml")
    assert len(reg.enabled_tables()) == 2
