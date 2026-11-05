---
name: bot-safety-invariants
description: Aturan keselamatan keras bot DCA Indodax dan cara memverifikasinya. Muat sebelum menyentuh rules.py, safeguards.py, engine.py, main_loop.py, atau dca_window.py, atau sebelum mengubah jalur order/konfirmasi.
---

# Invarian Keselamatan Bot

Bot ini semi-otomatis: manusia menyetujui lewat Telegram, mode bayangan adalah
bawaan, dan tidak ada order yang boleh keluar tanpa seluruh gerbang.

## Aturan keras

1. **Branch baru, data hidup tidak disentuh.**
   - Jangan pernah mengubah `.env`, `portfolio.db`, atau API key.
   - Jangan pernah menyalakan live (`v1_enabled = 1` / `aktifkan_live`).
   - Jangan pernah mengirim atau mensimulasikan order ke bursa nyata.

2. **Fail closed.**
   - Ragu = tidak ada order.
   - Tidak boleh ada jalur order yang melewati: persetujuan Telegram,
     pemeriksaan mode bayangan, `safeguards.gates`, gerbang spread,
     `permissions_ok`, dan idempotensi `client_order_id`.
   - Harga hilang (`None`) berarti berhenti, bukan harga nol.

3. **Tes tanpa jaringan.**
   - `conftest.py` di root memblokir `urllib`, `requests`, dan `socket`.
   - Waktu selalu disuntikkan sebagai parameter (`now`), bukan `time.time()`
     langsung di logika baru.
   - Jaringan nyata hanya boleh lewat blok `__main__` produksi.

4. **Konvensi repo.**
   - Docstring, komentar, dan pesan pengguna dalam bahasa Indonesia.
   - Logika murni di `rules.py`; `rules.py` tidak boleh mengimpor `safeguards`.
   - Satu modul memiliki satu skema/tanggung jawab (mis. `dca_window` memiliki
     `bot_state`, `shadow_log` memiliki `proposals`).

5. **Jangan mengubah keputusan desain diam-diam.**
   - Contoh: "DCA: satu percobaan per bulan" (2026-10-17).
   - Bila suatu perbaikan menyentuh keputusan itu, buat pengecualian tersempit
     di balik konstanta bernama dan catat di **DECISIONS FOR ME**.

## Cara memverifikasi

Jalankan sebelum dan sesudah perubahan:

```powershell
git status -sb
git diff --stat
python -m pytest -q
```

Periksa jalur order dengan mata, bukan asumsi:

```powershell
rg -n "submit_once|post_order|placeOrder|v1_enabled|aktifkan_live" .
rg -n "def gates|def permissions_ok|def spread_verdict" safeguards.py spread.py
```

Untuk setiap perubahan pada `engine.on_answer`, pastikan urutannya tetap:

1. jawaban ganda ditolak sebelum menyentuh transport;
2. jendela konfirmasi dan geser harga diperiksa;
3. gerbang spread diulang;
4. `safeguards.gates` diulang;
5. `permissions_ok` diperiksa;
6. `executor.submit_once` dipanggil SATU kali dengan id yang sama;
7. status final baru ditulis ke `portfolio.db`.

Jika perubahan menyentuh `main_loop._catat_dca`, ingat: bulan DCA hanya boleh
ditutup untuk penolakan yang benar-benar final (gerbang/spread), bukan untuk
kegagalan pengiriman Telegram atau halt sementara.

## Bukti yang wajib dilaporkan

- Tes yang gagal sebelum perbaikan dan lulus sesudahnya.
- Hasil `python -m pytest -q` penuh.
- `git diff` yang menunjukkan tidak ada jalur order baru yang melewati gerbang.
- Risiko yang tersisa, ditulis jujur.
