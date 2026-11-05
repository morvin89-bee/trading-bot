---
name: test-first-fix
description: Alur test-first untuk memperbaiki temuan di bot DCA Indodax. Gunakan saat memperbaiki bug atau mengubah perilaku: reproduksi, tes gagal, perbaikan minimal, suite penuh, catatan laporan.
---

# Perbaikan Test-First

Setiap temuan diperlakukan sebagai bug yang harus dibuktikan dulu, bukan
diperbaiki dari ingatan.

## Alur

1. **Reproduksi.**
   - Baca kode asli dan cari pemanggil fungsi yang akan diubah.
   - Tulis satu tes pytest yang gagal karena cacatnya, bukan karena fixture
     yang salah.
   - Tes tidak boleh membuka jaringan; suntikkan `verdict`, `telegram`,
     `transport`, `fetch`, dan `now`.

2. **Pastikan RED.**
   - Jalankan tes spesifik:
     `python -m pytest tests/test_<modul>.py::<nama_tes> -q`
   - Baca kegagalannya: harus menunjukkan perilaku salah yang dimaksud.

3. **Perbaikan minimal.**
   - Ubah sesedikit mungkin. Jangan menambah abstraksi, opsi, atau kode
     "untuk nanti".
   - Jaga urutan gerbang keselamatan; jangan melemahkan pemeriksaan apa pun.
   - Waktu tetap parameter injeksi; pesan tetap bahasa Indonesia.

4. **Pastikan GREEN.**
   - Jalankan tes spesifik lagi.
   - Jalankan seluruh suite: `python -m pytest -q`.
   - Jika ada ruff: `ruff check .` (repo ini belum punya konfigurasi ruff).

5. **Catat.**
   - Tambahkan satu paragraf di `docs/REVIEW_<tanggal>.md`: temuan, bukti
     sebelum/sesudah, tes yang ditambahkan, dan risiko.
   - Satu commit per temuan, pesan commit menjelaskan perilaku yang berubah.

## Aturan tes

- Nama tes dan docstring dalam bahasa Indonesia.
- Gunakan fixture `tmp_conn`/SQLite sementara; jangan menyentuh `portfolio.db`.
- Jangan memakai `time.time()` di kode baru; tes memberi `now` eksplisit.
- Untuk perilaku yang melintasi restart, kosongkan state memori lalu panggil
  pemuat state dari DB — jangan menguji objek yang sama.
- Jika tes lama bergantung pada cacat yang diperbaiki, perbarui fixture-nya
  dan jelaskan alasannya di docstring tes.
