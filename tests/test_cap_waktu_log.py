"""`v1_loop.log`: tiap baris bercap waktu WIB, dan berkas dibatasi ukurannya.

`v1_loop.log` adalah stdout mentah `main_loop` yang dialihkan `cmd >>` dari
`run_v1_hidden.vbs`/`run_v1.bat`. Tanpa cap waktu, baris log tidak bisa
dihitung per hari (bahan jujur hitungan periode shadow G5).

Rotasi TIDAK bisa dilakukan saat bot hidup: `cmd` menahan berkas append,
sehingga rename/truncate ditolak Windows ("being used by another process",
diukur 2026-11-16). Karena itu rotasi dilakukan PELUNCUR, sebelum python
dijalankan — saat tidak ada yang memegang berkas.
"""
import pathlib
import re

import main_loop
from test_main_loop import _FetchPerekam, _fresh_db, _TelegramPerekam, _ts

AKAR = pathlib.Path(main_loop.__file__).resolve().parent


def test_tiap_baris_keluaran_bercap_waktu_wib(tmp_path, capsys):
    """RED: hari ini v1_loop.log berisi baris tanpa cap waktu sama sekali."""
    conn = _fresh_db(tmp_path)

    keluar = main_loop.main(
        ["--siklus", "1"], conn=conn, telegram=_TelegramPerekam(),
        fetch=_FetchPerekam([]), jam=lambda: _ts(hour=12),
        jam_cetak=lambda: _ts(hour=12), tidur=lambda s: None)

    keluaran = capsys.readouterr().out
    assert keluar == 0
    baris = [b for b in keluaran.splitlines() if b.strip()]
    assert baris, "loop harus mencetak sesuatu"
    for b in baris:
        assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} WIB ", b), b
    assert "12:00:00 WIB" in keluaran, "cap waktu harus dari jam yang disuntik"


def test_cetak_berjam_tidak_menggandakan_cap_pada_baris_lanjutan():
    """Satu cap per baris, termasuk saat `print` menulis potongan."""
    class _Aliran:
        def __init__(self):
            self.teks = ""

        def write(self, t):
            self.teks += t

        def flush(self):
            pass

    aliran = _Aliran()
    cetak = main_loop._CetakBerjam(aliran, lambda: _ts(hour=9))
    cetak.write("baris satu\n")
    cetak.write("baris")
    cetak.write(" dua\n\n")
    cetak.write("baris tiga\n")

    baris = aliran.teks.splitlines()
    assert re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} WIB baris satu$",
                    baris[0]), baris
    assert baris[1].count("WIB") == 1, baris
    assert baris[2] == "", "baris kosong tidak diberi cap"
    assert baris[3].count("WIB") == 1, baris


def test_stdout_dikembalikan_setelah_main_selesai(tmp_path):
    """Wrapper tidak boleh tertinggal: proses berikutnya memakai stdout asli."""
    import sys

    conn = _fresh_db(tmp_path)
    asli = sys.stdout

    main_loop.main(
        ["--siklus", "1"], conn=conn, telegram=_TelegramPerekam(),
        fetch=_FetchPerekam([]), jam=lambda: _ts(hour=12),
        tidur=lambda s: None)

    assert sys.stdout is asli


def test_peluncur_vbs_membatasi_ukuran_log_sebelum_python_jalan():
    sumber = (AKAR / "run_v1_hidden.vbs").read_text(
        encoding="utf-8", errors="replace")

    assert "LOG_MAKS_BYTE" in sumber, "batas ukuran log harus bernama"
    assert "MoveFile" in sumber, "rotasi = pindahkan berkas lama"
    assert sumber.index("LOG_MAKS_BYTE") < sumber.index("sh.Run"), (
        "rotasi wajib SEBELUM python diluncurkan (cmd menahan berkasnya)")


def test_peluncur_bat_juga_membatasi_ukuran_log():
    sumber = (AKAR / "run_v1.bat").read_text(
        encoding="utf-8", errors="replace")

    assert "v1_loop.log.1" in sumber
