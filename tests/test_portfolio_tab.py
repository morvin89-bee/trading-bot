import dashboard.portfolio_tab as tab


def test_fmt_idr():
    assert tab.fmt_idr(1234567).startswith("Rp")
    assert tab.fmt_idr(1234567) == "Rp 1.234.567"
