"""Sapa awal: gagal kirim diulang TERBATAS, lalu polling tetap jalan.

`v1_loop.log` 2026-11-07..13 memperlihatkan lima kali "Pesan sapa awal tidak
terkirim" — satu percobaan per proses, tanpa ulangan, padahal gangguan
Telegram biasanya sesaat. Ulangan tidak boleh menghalangi polling: jumlah DAN
jeda dibatasi.
"""
import main_loop
from test_main_loop import _FetchPerekam, _fresh_db, _ts


class _TelegramSapaGagal:
    """`line` sapa gagal `gagal` kali pertama, lalu berhasil.

    Pesan lain (DCA/status) selalu terkirim, supaya hitungan hanya menyangkut
    sapa awal.
    """

    token = "token-uji"

    def __init__(self, gagal=2):
        self.sisa_gagal = gagal
        self.sapa = []

    def line(self, text):
        if "semi-otomatis berjalan" not in text:
            return True
        self.sapa.append(text)
        if self.sisa_gagal > 0:
            self.sisa_gagal -= 1
            return False
        return True

    def confirmation(self, p):
        return None


def _jalan(tmp_path, telegram, jeda):
    conn = _fresh_db(tmp_path)
    keluar = main_loop.main(
        ["--siklus", "1"], conn=conn, telegram=telegram,
        fetch=_FetchPerekam([]), jam=lambda: _ts(hour=12),
        tidur=jeda.append)
    return keluar


def test_sapa_diulang_sampai_terkirim(tmp_path, capsys):
    """RED: hari ini hanya SATU percobaan, lalu menyerah."""
    telegram = _TelegramSapaGagal(gagal=2)
    jeda = []

    keluar = _jalan(tmp_path, telegram, jeda)

    keluaran = capsys.readouterr().out
    assert keluar == 0
    assert len(telegram.sapa) == 3, telegram.sapa
    assert jeda[:2] == [main_loop.SAPA_JEDA_ULANG_S] * 2, jeda
    assert "tidak terkirim" not in keluaran, keluaran


def test_sapa_menyerah_setelah_batas_dan_polling_lanjut(tmp_path, capsys):
    telegram = _TelegramSapaGagal(gagal=99)
    jeda = []

    keluar = _jalan(tmp_path, telegram, jeda)

    keluaran = capsys.readouterr().out
    assert keluar == 0, "polling tidak boleh berhenti karena sapa gagal"
    assert len(telegram.sapa) == main_loop.SAPA_ULANG_MAKS
    assert keluaran.count("tidak terkirim") == 1, keluaran
    assert jeda.count(main_loop.SAPA_JEDA_ULANG_S) == (
        main_loop.SAPA_ULANG_MAKS - 1)


def test_sapa_sukses_langsung_tanpa_jeda(tmp_path):
    """Jalur normal tidak boleh menambah latensi sama sekali."""
    telegram = _TelegramSapaGagal(gagal=0)
    jeda = []

    _jalan(tmp_path, telegram, jeda)

    assert len(telegram.sapa) == 1
    assert main_loop.SAPA_JEDA_ULANG_S not in jeda, jeda


def test_batas_ulang_wajar():
    """Batas jumlah DAN waktu: sapa tidak boleh menahan polling lama."""
    assert 1 <= main_loop.SAPA_ULANG_MAKS <= 5
    assert (main_loop.SAPA_ULANG_MAKS - 1) * main_loop.SAPA_JEDA_ULANG_S <= 30
