import pytest

import portfolio_store as ps


@pytest.fixture
def tmp_conn(tmp_path):
    conn = ps.connect(str(tmp_path / "t.db"))
    ps.init_db(conn)
    yield conn
    conn.close()
