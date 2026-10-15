"""Tes rekonsiliasi portfolio.db vs bursa — tidak pernah membuka jaringan.

`reconcile.mismatches` murni membandingkan dua dict yang disuntikkan
pemanggil; pengambilan data ada di engine. Selisih sekecil apa pun =
trading mati + pengguna diberi tahu (spec §6.3 & §6.6). Setiap angka yang
diassert di sini sudah diverifikasi tercapai sebelum tes dijalankan.
"""
import reconcile


def test_any_balance_gap_is_reported():
    db = {"IDR": 200000.0, "ETH": 0.000001}
    ex = {"IDR": 203394.0, "ETH": 0.000001}
    out = reconcile.mismatches(db, ex)
    assert out and "IDR" in out[0]


def test_identical_state_reports_nothing():
    s = {"IDR": 203394.0}
    assert reconcile.mismatches(s, s) == []


def test_open_orders_must_match_db():
    # Order tertunda mengurangi saldo tersedia — selisih apa pun = halt
    out = reconcile.mismatches({"open_orders": 0}, {"open_orders": 1})
    assert out


def test_open_orders_identical_reports_nothing():
    assert reconcile.mismatches({"open_orders": 2},
                                 {"open_orders": 2}) == []


def test_message_shape_and_actual_values():
    # Pesan harus menyebut angka yang benar-benar dibandingkan — pesan
    # yang salah angka lebih buruk daripada tidak ada pesan.
    out = reconcile.mismatches({"IDR": 200000.0}, {"IDR": 203394.0})
    assert out == [
        "Selisih IDR: db=200000.0 bursa=203394.0 -> trading mati, cek manual"]


def test_idr_tolerance_is_exactly_one_rupiah():
    # Selisih tepat Rp 1 masih dalam toleransi; Rp 2 sudah selisih.
    assert reconcile.mismatches({"IDR": 100000.0},
                                {"IDR": 100001.0}) == []
    out = reconcile.mismatches({"IDR": 100000.0}, {"IDR": 100002.0})
    assert out and "IDR" in out[0]


def test_coin_tolerance_is_1e_minus_8():
    # 1e-9 jauh di bawah toleransi 1e-8 -> dianggap sama; 1e-7 (sepuluh
    # kali toleransi) -> selisih.
    assert reconcile.mismatches({"eth": 1.0},
                                {"eth": 1.0 + 1e-9}) == []
    out = reconcile.mismatches({"eth": 1.0}, {"eth": 1.0 + 1e-7})
    assert out and "eth" in out[0]


def test_every_gap_gets_its_own_line():
    db = {"IDR": 100000.0, "eth": 1.0}
    ex = {"IDR": 100002.0, "eth": 2.0}
    out = reconcile.mismatches(db, ex)
    assert len(out) == 2
    assert "IDR" in out[0] and "eth" in out[1]


def test_asset_reported_only_by_exchange_is_a_selisih():
    # Bursa melaporkan aset yang tidak dicatat db -> selisih, bukan
    # diabaikan; sisi yang hilang tertulis "tidak ada", bukan angka karangan.
    out = reconcile.mismatches({}, {"eth": 1.0})
    assert out and "eth" in out[0] and "tidak ada" in out[0]


def test_asset_recorded_only_in_db_is_a_selisih():
    # Db mencatat saldo yang tidak dilaporkan bursa (bursa menyaring saldo
    # nol) -> buku bukan kenyataan -> halt (spec §6.6).
    out = reconcile.mismatches({"eth": 0.5}, {})
    assert out and "eth" in out[0] and "tidak ada" in out[0]
