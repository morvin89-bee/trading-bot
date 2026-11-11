"""Tes metadata pair publik: cache SQLite harian dan pembulatan qty ke bawah."""
from decimal import Decimal

import pytest

import pair_meta
import rules

ROWS = [{
    "ticker_id": "btc_idr",
    "quantity_increment": 1e-08,
    "trade_min_base_currency": 10000,
    "trade_min_traded_currency": 0.00000743,
    "trade_fee_percent": 0.2,
}]


def _conn(tmp_path):
    import sqlite3
    return sqlite3.connect(str(tmp_path / "meta.db"))


def test_refresh_menyimpan_metadata_dan_hanya_sekali_sehari(tmp_path):
    conn = _conn(tmp_path)
    panggilan = []

    def fetch():
        panggilan.append(1)
        return ROWS

    dasar = 1_760_000_000.0
    assert pair_meta._refresh(conn, now=dasar, fetch=fetch) == 1
    assert len(panggilan) == 1

    m = pair_meta._get(conn, "btc_idr", now=dasar)
    assert m is not None
    assert m.quantity_increment == "1e-08"
    assert m.min_base_idr == 10000
    assert m.min_traded == 0.00000743
    # 0.2 (persen) -> 0.002 (fraksi), satuan yang sama dengan proposal.FEE_PCT
    assert m.fee_pct == pytest.approx(0.002)

    # Belum basi -> tidak ada panggilan jaringan lagi.
    pair_meta._refresh(conn, now=dasar + 3600, fetch=fetch)
    assert len(panggilan) == 1

    # Basi -> disegarkan lagi.
    pair_meta._refresh(conn, now=dasar + pair_meta.MAX_AGE_S + 1, fetch=fetch)
    assert len(panggilan) == 2


def test_get_menolak_metadata_hilang_basi_atau_dari_masa_depan(tmp_path):
    conn = _conn(tmp_path)
    assert pair_meta._get(conn, "btc_idr", now=0.0) is None

    dasar = 1_760_000_000.0
    pair_meta._refresh(conn, now=dasar, fetch=lambda: ROWS)
    assert pair_meta._get(conn, "eth_idr", now=dasar) is None
    assert pair_meta._get(
        conn, "btc_idr", now=dasar + pair_meta.MAX_AGE_S + 1) is None
    assert pair_meta._get(conn, "btc_idr", now=dasar - 1) is None


def test_refresh_gagal_dibatasi_waktu_bukan_tiap_tick(tmp_path):
    """Percobaan gagal juga memakai cap waktu: denyut 5 menit tidak boleh
    membanjiri bursa dengan percobaan setiap tick."""
    conn = _conn(tmp_path)
    panggilan = []

    def gagal():
        panggilan.append(1)
        raise RuntimeError("jaringan putus")

    dasar = 1_760_000_000.0
    with pytest.raises(RuntimeError):
        pair_meta._refresh(conn, now=dasar, fetch=gagal)

    # Dalam RETRY_S: tidak ada percobaan baru (dan tidak melempar).
    assert pair_meta._refresh(conn, now=dasar + 60, fetch=gagal) == 0
    assert len(panggilan) == 1

    # Setelah RETRY_S: boleh mencoba lagi.
    with pytest.raises(RuntimeError):
        pair_meta._refresh(
            conn, now=dasar + pair_meta.RETRY_S + 1, fetch=gagal)
    assert len(panggilan) == 2


def test_umur_tertua_none_saat_kosong_dan_mengikuti_baris_tertua(tmp_path):
    conn = _conn(tmp_path)
    assert pair_meta.umur_tertua(conn, now=0.0) is None

    dasar = 1_760_000_000.0
    pair_meta._refresh(conn, now=dasar, fetch=lambda: ROWS)
    assert pair_meta.umur_tertua(
        conn, now=dasar + 10) == pytest.approx(10.0)


def test_qty_dibulatkan_ke_bawah_tidak_pernah_ke_atas():
    assert rules.bulatkan_qty_bawah(1.23456789, "1e-08") == Decimal(
        "1.23456789")
    assert rules.bulatkan_qty_bawah(1.23456789, "1e-04") == Decimal("1.2345")
    assert rules.bulatkan_qty_bawah(5e-05, "1e-08") == Decimal("0.00005")
    # Pembulatan ke terdekat akan memberi 1.2346; yang dipakai selalu ke bawah.
    assert rules.bulatkan_qty_bawah(1.23459, "1e-04") == Decimal("1.2345")


def test_qty_teks_payload_titik_tetap_tanpa_notasi_ilmiah():
    teks = rules.qty_teks_tetap(5e-05)
    assert teks == "0.00005"
    assert "e" not in teks.lower()
