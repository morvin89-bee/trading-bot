import pytest

import engine
import portfolio_store as ps


@pytest.fixture(autouse=True)
def _bersihkan_pesan_engine():
    """Dedupe pesan engine adalah keadaan proses; tiap tes mulai kosong."""
    engine.reset_state()
    yield
    engine.reset_state()


@pytest.fixture
def tmp_conn(tmp_path):
    conn = ps.connect(str(tmp_path / "t.db"))
    ps.init_db(conn)
    yield conn
    conn.close()
