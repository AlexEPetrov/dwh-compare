from dwh_compare.comparator import diff_md5_maps
from dwh_compare.models import ColumnDiff


def test_maps_ok():
    m = {("1",): "h1", ("2",): "h2"}
    result, differ = diff_md5_maps(
        m, m, ["id"], ["amount"], "t", None, 10
    )
    assert result.status == "ok"
    assert result.matched == 2
    assert differ == []


def test_maps_differ_lazy_keys():
    src = {("1",): "aaa", ("2",): "bbb"}
    tgt = {("1",): "aXX", ("3",): "ccc"}
    result, differ = diff_md5_maps(
        src, tgt, ["id"], ["amount"], "t", None, 10
    )
    assert result.status == "differ"
    assert result.values_differ == 1
    assert result.missing_in_target == 1
    assert result.missing_in_source == 1
    assert differ == [("1",)]  # only MD5 mismatch keys for field fetch
    # missing samples have no columns
    assert all(not s.columns for s in result.samples)


def test_column_filter():
    cols = [
        ColumnDiff(column="amount", source="10", target="11"),
        ColumnDiff(column="status", source="ok", target="ok"),
    ]
    assert [c.column for c in cols if c.source != c.target] == ["amount"]
