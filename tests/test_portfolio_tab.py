import dashboard.portfolio_tab as tab


def test_fmt_idr():
    assert tab.fmt_idr(1234567).startswith("Rp")
    assert tab.fmt_idr(1234567) == "Rp 1.234.567"


def test_holdings_rows_punya_baris_idr():
    import types
    h = types.SimpleNamespace(coin="bch", qty=0.000583, avg_cost=None,
                              basis_known=False, value_idr=2997.2,
                              unrealized_idr=None)
    rows = tab._holdings_rows([h], 500000.0)
    assert rows[0]["Koin"] == "IDR"
    assert rows[0]["Nilai (Rp)"] == 500000.0
    assert rows[1]["Koin"] == "BCH"
    # Tabel harus penjumlah: nilai baris = total portofolio.
    assert round(sum(r["Nilai (Rp)"] for r in rows), 2) == 502997.2
