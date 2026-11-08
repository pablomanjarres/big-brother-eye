from __future__ import annotations

import pytest

from bbeye.storage import Storage


@pytest.fixture
def storage(tmp_path):
    db = Storage(tmp_path / "test.sqlite3")
    yield db
    db.close()
