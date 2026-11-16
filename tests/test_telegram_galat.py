"""Galat Telegram: diklasifikasi, satu baris jelas per jenis per jam, 401 backoff.

Cacat nyata 2026-11-16 (`errors.log`): token mati menghasilkan

    401 Client Error: Unauthorized for url: .../getUpdates

51 kali dalam lima jam (~tiap 5,7 menit), tanpa satu baris yang menyebut
sebabnya ("token ditolak"), dan bot tetap memukul API tiap denyut tanpa batas.
"""
import pytest

import main_loop
import telegram_gate
from test_main_loop import _TelegramPerekam, _fresh_db, _ts


class _FetchGagal:
    """`fetch` yang selalu melempar galat yang diberikan."""

    def __init__(self, galat):
        self.galat = galat
        self.panggilan = 0

    def __call__(self, token, *, offset, timeout):
        self.panggilan += 1
        raise self.galat


def _jalan(tmp_path, fetch, siklus, jam):
    """Satu loop pendek dengan jam yang maju `POLL_GAP_S` tiap bangun."""
    conn = _fresh_db(tmp_path)
    keluar = main_loop.main(
        ["--siklus", str(siklus)], conn=conn, telegram=_TelegramPerekam(),
        fetch=fetch, jam=lambda: jam[0],
        tidur=lambda s: jam.__setitem__(0, jam[0] + s))
    return keluar, conn


# ---------------------------------------------------------------------------
# Klasifikasi: jenis yang berbeda, pesan yang berbeda, tanpa token
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("galat,jenis", [
    (RuntimeError("Telegram menolak: 401 Unauthorized"), "401"),
    (RuntimeError("Telegram menolak: 403 Forbidden"), "403"),
    (RuntimeError("Telegram menolak: 409 Conflict"), "409"),
    (RuntimeError("Telegram menolak: 400 Bad Request"), "400"),
    (RuntimeError("429 Too Many Requests"), "429"),
    (ConnectionError("jaringan putus"), "jaringan"),
    (TimeoutError("timeout"), "jaringan"),
    (ValueError("aneh sekali"), "lain"),
])
def test_klasifikasi_jenis_galat_telegram(galat, jenis):
    """RED: hari ini tidak ada klasifikasi sama sekali."""
    assert telegram_gate.klasifikasi_galat(galat)[0] == jenis


def test_401_menyebut_token_dan_periksa_env():
    """Pesan 401 harus memberi tahu APA yang harus diperiksa."""
    jenis, pesan = telegram_gate.klasifikasi_galat(
        RuntimeError("Telegram menolak: 401 Unauthorized"))
    assert jenis == "401"
    assert "401" in pesan
    assert "TELEGRAM_TOKEN" in pesan
    assert ".env" in pesan


def test_pesan_klasifikasi_tidak_memuat_token():
    token = "123456789:RAHASIA-XXXXXXXX"
    jenis, pesan = telegram_gate.klasifikasi_galat(RuntimeError(
        f"401 Unauthorized for url: "
        f"https://api.telegram.org/bot{token}/getUpdates"))
    assert jenis == "401"
    assert token not in pesan
    assert "RAHASIA" not in pesan


def test_jenis_berbeda_punya_baris_berbeda():
    """409 (getUpdates ganda) bukan 401 (token mati): saran perbaikannya beda."""
    _, p401 = telegram_gate.klasifikasi_galat(RuntimeError("401 Unauthorized"))
    _, p409 = telegram_gate.klasifikasi_galat(RuntimeError("409 Conflict"))
    assert p401 != p409
    assert "proses lain" in p409.lower()


# ---------------------------------------------------------------------------
# Satu baris per jenis per jam — bukan 12 baris/jam yang sama
# ---------------------------------------------------------------------------

def test_401_dicatat_sekali_dalam_sejam(tmp_path, capsys):
    """RED: 40 bangun (10 menit) menghasilkan baris berulang, tanpa sebab."""
    jam = [_ts(hour=12)]
    fetch = _FetchGagal(RuntimeError("Telegram menolak: 401 Unauthorized"))

    keluar, _conn = _jalan(tmp_path, fetch, 40, jam)

    keluaran = capsys.readouterr().out
    assert keluar == 0
    assert "Polling Telegram gagal" in keluaran
    assert keluaran.count("TELEGRAM_TOKEN ditolak (401)") == 1, keluaran
    assert "RAHASIA" not in keluaran


def test_401_dicatat_lagi_pada_jam_berikutnya(tmp_path, capsys):
    """Satu baris per jam, bukan satu baris selamanya: jam berikutnya = baris baru."""
    jam = [_ts(hour=12)]
    fetch = _FetchGagal(RuntimeError("Telegram menolak: 401 Unauthorized"))
    bangun = [0]

    def tidur(s):
        bangun[0] += 1
        jam[0] += 3600 if bangun[0] == 20 else s

    conn = _fresh_db(tmp_path)
    main_loop.main(
        ["--siklus", "40"], conn=conn, telegram=_TelegramPerekam(),
        fetch=fetch, jam=lambda: jam[0], tidur=tidur)

    keluaran = capsys.readouterr().out
    assert keluaran.count("TELEGRAM_TOKEN ditolak (401)") == 2, keluaran


# ---------------------------------------------------------------------------
# Backoff 401: jangan memukul API tiap denyut tanpa batas
# ---------------------------------------------------------------------------

def test_backoff_401_maks_sekali_per_10_menit():
    """RED: hari ini tidak ada backoff — tiap denyut memukul API."""
    main_loop.reset_state()
    fetch = _FetchGagal(RuntimeError("Telegram menolak: 401 Unauthorized"))
    telegram = _TelegramPerekam()

    with pytest.raises(RuntimeError):
        main_loop._ambil_update(telegram, fetch, now=1_000.0)
    assert main_loop._ambil_update(telegram, fetch, now=1_000.0 + 599) is None
    assert fetch.panggilan == 1, "dalam backoff tidak boleh menyentuh API"

    with pytest.raises(RuntimeError):
        main_loop._ambil_update(telegram, fetch, now=1_000.0 + 600)
    assert fetch.panggilan == 2, "setelah backoff habis, satu percobaan lagi"


def test_galat_jaringan_tidak_memblokir_denyut_berikutnya():
    """Jaringan putus bukan token mati: percobaan berikutnya tetap wajar."""
    main_loop.reset_state()
    fetch = _FetchGagal(ConnectionError("jaringan putus"))
    telegram = _TelegramPerekam()

    with pytest.raises(ConnectionError):
        main_loop._ambil_update(telegram, fetch, now=1_000.0)
    with pytest.raises(ConnectionError):
        main_loop._ambil_update(telegram, fetch, now=1_015.0)
    assert fetch.panggilan == 2


def test_backoff_tidak_mengaku_pulih(tmp_path, capsys):
    """Dilewati karena backoff BUKAN "pulih" — klaim sehat tidak boleh palsu."""
    jam = [_ts(hour=12)]
    panggilan = []

    def fetch(token, *, offset, timeout):
        panggilan.append(offset)
        if len(panggilan) == 2:
            raise RuntimeError("Telegram menolak: 401 Unauthorized")
        return {"result": []}

    keluar, _conn = _jalan(tmp_path, fetch, 3, jam)

    keluaran = capsys.readouterr().out
    assert keluar == 0
    assert len(panggilan) == 2, "denyut ke-3 masih dalam backoff"
    assert "sudah pulih" not in keluaran, keluaran


def test_polling_denyut_penuh_gagal_tidak_mencetak_baris_ganda(tmp_path, capsys):
    """Galat Telegram yang sudah dicatat tidak diulang oleh penjaga denyut."""
    jam = [_ts(hour=12)]
    fetch = _FetchGagal(RuntimeError("Telegram menolak: 401 Unauthorized"))

    _jalan(tmp_path, fetch, 2, jam)

    keluaran = capsys.readouterr().out
    assert keluaran.count("401") == 1, keluaran
    assert "Denyut penuh gagal" not in keluaran
