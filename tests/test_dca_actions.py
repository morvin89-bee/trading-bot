import time

import portfolio
import rules


def test_konstanta_bulanan_satu_sumber():
    import dashboard.portfolio_tab as tab   # pola tests/test_portfolio_tab.py
    assert rules.MONTHLY_IDR == 500000
    assert tab.MONTHLY_IDR == rules.MONTHLY_IDR  # dashboard menyalin dari rules
    assert rules.RULE_DCA == "dca_bulanan"


def test_dca_actions_membagi_setoran_sesuai_bobot():
    aksi = rules.dca_actions(
        [], 0.0,
        {"btc_idr": 1_000_000.0, "eth_idr": 500_000.0, "sol_idr": 100_000.0},
        max_order=275_000)
    assert [(a.pair, a.amount_idr) for a in aksi] == [
        ("btc_idr", 275_000.0), ("eth_idr", 125_000.0), ("sol_idr", 50_000.0)]
    assert [a.qty for a in aksi] == [0.275, 0.25, 0.5]
    assert all(a.kind == "BUY" and a.executable for a in aksi)
    assert all(a.price_ref > 0 for a in aksi)


def test_dca_actions_memotong_di_batas_tanpa_membagi_sisa():
    # Portofolio miring berat ke ETH+SOL -> `dca_split` menggeser porsi ke
    # BTC sampai ~423.000; atap 275.000 memotongnya, dan sisa TIDAK berpindah.
    holdings = [
        portfolio.Holding("eth_idr", "eth", 4.0, 400_000.0, 500_000.0,
                          2_000_000.0, 1_600_000.0, 400_000.0, True),
        portfolio.Holding("sol_idr", "sol", 3.0, 300_000.0, 400_000.0,
                          1_200_000.0, 900_000.0, 300_000.0, True),
    ]
    aksi = rules.dca_actions(
        holdings, 0.0,
        {"btc_idr": 1_000_000.0, "eth_idr": 500_000.0, "sol_idr": 100_000.0},
        max_order=275_000)
    assert [(a.pair, a.amount_idr) for a in aksi] == [("btc_idr", 275_000.0)]


def test_dca_actions_melewati_aset_tanpa_harga():
    aksi = rules.dca_actions([], 0.0, {"btc_idr": 1_000_000.0},
                             max_order=275_000)
    assert [a.pair for a in aksi] == ["btc_idr"]   # ETH & SOL tanpa harga: dilewati


def test_dca_actions_kosong_bila_semua_ke_cadangan():
    assert rules.dca_actions([], 0.0, {}, monthly=15_000,
                             max_order=275_000) == []


def test_dca_actions_alasan_indonesia_menyebut_dca():
    aksi = rules.dca_actions([], 0.0, {"btc_idr": 1_000_000.0,
                                       "eth_idr": 500_000.0,
                                       "sol_idr": 100_000.0},
                             max_order=100_000)  # memaksa pemotongan
    # Ketiga aset punya harga -> TIGA aksi keluar (bukan satu).
    assert len(aksi) == 3
    for a in aksi:
        assert "DCA" in a.reason and a.executable
    a = aksi[0]                              # BTC 275.000 -> terpotong 100.000
    assert a.pair == "btc_idr" and a.amount_idr == 100_000.0
    assert "cadangan" in a.reason            # hanya yang terpotong menyebut sisa
    assert "cadangan" not in aksi[2].reason   # SOL 50.000 < 100.000, tak terpotong
