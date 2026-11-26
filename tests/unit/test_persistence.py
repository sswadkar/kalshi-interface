"""A failed write must preserve the last usable configuration, not truncate it."""
import errno

import pytest

from kalshi_workstation.core import persistence


@pytest.mark.parametrize("failure_point", ["fsync", "replace"])
def test_failed_write_preserves_previous_file_and_removes_temporary_file(tmp_path, monkeypatch, failure_point):
    path = tmp_path / "settings.json"
    previous = b'{"enabled": false}\n'
    path.write_bytes(previous)

    def fail(*args):
        raise OSError(errno.ENOSPC, "Synthetic disk full")

    monkeypatch.setattr(persistence.os, failure_point, fail)
    with pytest.raises(persistence.ConfigurationSaveError, match="previous saved configuration is unchanged"):
        persistence.write_json_atomic(path, {"enabled": True})

    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]
