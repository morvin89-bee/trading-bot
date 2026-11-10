import pytest

import engine
import pair_meta
import portfolio_store as ps


@pytest.fixture(autouse=True)
def _bersihkan_pesan_engine():
    """Dedupe pesan engine adalah keadaan proses; tiap tes mulai kosong."""
    engine.reset_state()
    yield
    engine.reset_state()


@pytest.fixture(autouse=True)
def _metadata_pair_default(monkeypatch):
    """Metadata pair default longgar supaya tes lama tidak menyentuh jaringan.

    Tes yang menguji halt, batas minimum, atau cache nyata mengganti
    `pair_meta.get`/`pair_meta.refresh` sendiri (atau memakai `_get`/`_refresh`
    yang tidak dipalsukan di sini).
    """
    monkeypatch.setattr(pair_meta, "refresh",
                        lambda conn, *, now, fetch=None: 0)

    def palsu(conn, pair, *, now, max_age_s=pair_meta.MAX_AGE_S):
        return pair_meta.Meta(
            pair=pair, quantity_increment="1e-08", min_base_idr=0.0,
            min_traded=0.0, fee_pct=0.002, fetched_ts=now)

    monkeypatch.setattr(pair_meta, "get", palsu)


@pytest.fixture
def tmp_conn(tmp_path):
    conn = ps.connect(str(tmp_path / "t.db"))
    ps.init_db(conn)
    yield conn
    conn.close()
