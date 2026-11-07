import pytest

import rules
from portfolio import Holding


def _h(avg, price, qty=1.0):
    return Holding("btc_idr", "btc", qty, avg, price, qty * price, avg * qty,
                   (price - avg) * qty, True)


def test_fires_cumulative_25pct_at_plus_50_net():
    a = rules.take_profit_actions([_h(1_000_000, 1_600_000)])   # net +59,7%
    assert len(a) == 1 and a[0].kind == "SELL" and a[0].executable
    assert a[0].qty == pytest.approx(0.25)
    assert a[0].qty_target == pytest.approx(0.25)


def test_fires_satu_level_per_hari_at_plus_100():
    """Dua ambang terlewati: tranche +50% dulu; +100% menyusul hari berikutnya."""
    a = rules.take_profit_actions([_h(1000, 2100)], hari="2026-11-07")
    assert len(a) == 1
    assert a[0].level == 50
    assert a[0].qty == pytest.approx(0.25)


def test_level_berikutnya_setelah_50_selesai():
    h = _h(1000, 2100)                       # net +109,6%
    sudah = {("btc_idr", 50): (0.25, 0.25, "2026-11-06")}
    a = rules.take_profit_actions([h], sudah=sudah, hari="2026-11-07")
    assert len(a) == 1
    assert a[0].qty == pytest.approx(0.25)   # hanya tranche +100%
    assert a[0].level == 100
    assert a[0].qty_target == pytest.approx(0.25)


def test_no_action_when_basis_unknown():
    h = Holding("bch_idr", "bch", 0.001, None, 7.5e6, 7500, None, None, False)
    assert rules.take_profit_actions([h]) == []


def test_below_min_order_not_executable():
    a = rules.take_profit_actions([_h(1000, 1600, qty=0.001)])  # 25% ~ Rp 0,4
    assert a and a[0].executable is False


def test_ambang_yang_sudah_dieksekusi_tidak_diusulkan_lagi():
    """Setelah +50% dijual, harga +59,7% bukan alasan menjual 25% lagi."""
    h = _h(1000, 1600)
    sudah = {("btc_idr", 50): (0.25, 0.25, "2026-11-06")}
    assert rules.take_profit_actions(
        [h], sudah=sudah, hari="2026-11-07") == []


def test_ambang_tidak_direset_saat_avg_cost_berubah_karena_dca():
    """DCA mengubah avg_cost; ambang yang sudah dieksekusi TIDAK berlaku lagi.

    Ini temuan A: state lama dikunci pada avg_cost, sehingga setiap pembelian
    DCA membuka kembali ambang +50% yang sudah dijual.
    """
    h = _h(950.0, 1600.0)                    # avg_cost baru setelah DCA
    sudah = {("btc_idr", 50): (0.25, 0.25, "2026-11-06")}
    assert rules.take_profit_actions(
        [h], sudah=sudah, hari="2026-11-07") == []


def test_perbedaan_float_kecil_avg_cost_tidak_mereset():
    """Perbandingan float tidak boleh menjadi pintu reset kedua."""
    h = _h(1000.0000000001, 1600.0)
    sudah = {("btc_idr", 50): (0.25, 0.25, "2026-11-06")}
    assert rules.take_profit_actions(
        [h], sudah=sudah, hari="2026-11-07") == []


def test_state_dihapus_saat_posisi_tertutup_maka_ambang_berlaku_lagi():
    """Posisi tertutup = siklus baru; state kosong mengusulkan +50% lagi."""
    h = _h(1000, 1600)
    a = rules.take_profit_actions([h], sudah={}, hari="2026-11-07")
    assert len(a) == 1 and a[0].level == 50


def test_sisa_tranche_diusulkan_besok_bukan_hari_ini():
    """Sisa karena atap per order menjadi tranche berikutnya, satu per hari."""
    h = _h(1000, 1600, qty=1000)
    a = rules.take_profit_actions(
        [h], max_order=275_000, hari="2026-11-07")
    assert len(a) == 1
    assert a[0].qty_target == pytest.approx(250.0)
    assert a[0].qty == pytest.approx(275_000 / 1600)
    assert a[0].sisa_qty == pytest.approx(250 - 275_000 / 1600)

    sudah = {("btc_idr", 50): (250.0, 275_000 / 1600, "2026-11-07")}
    assert rules.take_profit_actions(
        [h], sudah=sudah, max_order=275_000, hari="2026-11-07") == []

    a2 = rules.take_profit_actions(
        [h], sudah=sudah, max_order=275_000, hari="2026-11-08")
    assert len(a2) == 1
    assert a2[0].level == 50
    assert a2[0].qty == pytest.approx(250 - 275_000 / 1600)
    assert a2[0].sisa_qty == pytest.approx(0.0)


def test_berhenti_bila_gain_turun_di_bawah_ambang():
    """Sisa tranche +100% tidak diusulkan saat gain turun ke +59,7%."""
    h = _h(1000, 1600)
    sudah = {
        ("btc_idr", 50): (0.25, 0.25, "2026-11-06"),
        ("btc_idr", 100): (0.25, 0.10, "2026-11-06"),
    }
    assert rules.take_profit_actions(
        [h], sudah=sudah, hari="2026-11-07") == []


def test_sell_dipotong_ke_atap_dan_sisa_dilaporkan():
    """SELL 400.000 dipotong ke atap 275.000; sisa koin disebut, bukan hilang."""
    h = _h(1000, 1600, qty=1000)
    a = rules.take_profit_actions([h], max_order=275_000)
    assert len(a) == 1
    assert a[0].amount_idr == pytest.approx(275_000)
    assert a[0].qty == pytest.approx(275_000 / 1600)
    assert a[0].sisa_qty == pytest.approx(250 - 275_000 / 1600)
    assert "sisa" in a[0].reason.lower()
