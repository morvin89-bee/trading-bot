# Checklist Go-Live — Bot DCA Indodax

Dokumen ini adalah **gerbang manusia**, bukan sakelar. Tidak ada satu baris pun
di sini yang menyalakan `v1_enabled`. Sakelar tetap hanya lewat
`main_loop.aktifkan_live(conn)` yang dipanggil manusia setelah semua kotak
di bawah dicentang.

Tanggal: 2026-11-08 · Branch: `fix/review-p1`

---

## 1. Bukti otomatis (wajib, ulangi tepat sebelum go-live)

- [ ] `python -m pytest -q` → seluruh suite lulus, nol `skipped` yang
      menutupi jalur order.
- [ ] `python -m pytest tests/scenarios -q` → 12 skenario ujung-ke-ujung lulus
      (DCA penuh, tidak dijawab + pengingat, Batal + Setuju terlambat, drift
      1,5%/8%/TP, Telegram mati 40 menit/sehari, restart, harga basi >120 s,
      spread `None`/lebar saat Setuju, TP lengkap, saldo kurang lalu setoran
      10.00, izin halt, hari terakhir bulan).
- [ ] `python -m pytest tests/scenarios/test_acak.py -q` → uji acak berbenih
      lulus. Enam invarian yang diperiksa pada setiap order:
      1. tidak ada order tanpa `setuju` tercatat;
      2. tidak ada dua order live untuk `(pair, bulan DCA)`;
      3. tidak ada order > `safeguards.MAX_ORDER_IDR`;
      4. tidak ada order bila `can_withdraw` aktif atau saldo kurang;
      5. maksimal 3 order per aset per hari;
      6. qty jual ≤ kepemilikan.
- [ ] `git log --oneline` menunjukkan tidak ada commit yang menaikkan
      `MAX_ORDER_IDR`, `MAX_PER_ASSET_PER_DAY`, atau `GO_LIVE_SHADOW_DAYS`
      tanpa keputusan tertulis di `docs/REVIEW_2026-11-08.md`.

## 2. Bukti shadow NYATA (bukan hanya tes)

Tes membuktikan logika; hanya jalan sungguhan yang membuktikan akun, kunci,
dan Telegram. Minimal **satu siklus DCA bulanan penuh** di mode bayangan:

- [ ] Bot hidup ≥ 7 hari bayangan bersih (`shadow_day >= 7`) — lantai
      `GO_LIVE_SHADOW_DAYS`.
- [ ] Satu tanggal DCA (tanggal 1) benar-benar menghasilkan tiga konfirmasi
      (`btc_idr`, `eth_idr`, `sol_idr`) di Telegram, dan salah satunya
      dijawab Setuju.
- [ ] `shadow_log` berisi baris `setuju` untuk usulan itu dan **tidak ada**
      baris `order_status` — bukti mode bayangan tidak menyentuh bursa.
- [ ] Tidak ada pesan "berhenti" (halt) yang tak dijelaskan di `v1_loop.log`
      selama periode itu.
- [ ] Pesan penolakan berulang (spread/pagu/harga) muncul **paling banyak
      sekali per hari per alasan**, bukan tiap lima menit.
- [ ] Saldo kurang dari setoran: pesan muncul sekali, dan usulan DCA tetap
      muncul pada denyut setelah saldo cukup (bulan tidak tertutup).

Perintah pemeriksaan (jalankan dari folder bot, bukan dari tes):

```powershell
python -c "import sqlite3,main_loop; c=sqlite3.connect('portfolio.db'); c.row_factory=sqlite3.Row; print(main_loop.load_state(c))"
```

- [ ] `v1_enabled` masih `False` saat pemeriksaan ini.

## 3. Kunci API (wajib sebelum menyalakan sakelar)

Kunci **tidak boleh** punya izin tarik dana. Bot memeriksa `canWithdraw` dan
berhenti bila aktif, tetapi pemeriksaan itu adalah jaring kedua — kunci itu
sendiri harus sudah dibatasi.

- [ ] Kunci dibuat di Indodax dengan izin **read-only** atau **trade-only**
      (tanpa penarikan).
- [ ] Uji negatif: dengan kunci itu, percobaan `withdraw` ditolak bursa
      (lakukan di luar bot, sekali, nominal kecil — bukan lewat kode bot).
- [ ] `canWithdraw` terbaca `False` oleh `indodax_api`; bila `None`
      (tidak terbaca), bot berhenti — itu perilaku yang benar, bukan bug.
- [ ] Kunci yang dipakai mode bayangan dan mode live adalah kunci yang sama
      dengan izin yang sama; tidak ada kunci "sementara" berizin lebih.

## 4. Lingkungan

- [ ] `.env` hanya dibaca oleh blok `__main__`; tidak ada kunci tercetak di
      `v1_loop.log` (token Telegram disamarkan oleh `_samarkan_pesan_galat`).
- [ ] `portfolio.db` sudah dicadangkan (salinan bertanggal) sebelum go-live.
- [ ] Jam mesin dan zona waktu benar (Asia/Jakarta); DND 23.00–07.00 dan
      batas 21.00 bergantung padanya.
- [ ] Proses auto-revive berjalan dari folder modul (`ERRORS_LOG` absolut),
      bukan dari home direktori.
- [ ] Telegram: bot yang sama tidak dipakai proses lain (satu konsumen
      `getUpdates`).

## 5. Setelah sakelar dinyalakan (jam pertama)

- [ ] Satu order kecil benar-benar terkirim dan statusnya `filled` di
      Telegram.
- [ ] `client_order_id` di bursa sama dengan id usulan (idempotensi).
- [ ] Tombol Setuju kedua pada usulan yang sama diabaikan ("sudah dijawab").
- [ ] Bila ada yang tidak sesuai: matikan dengan
      `set_shadow_day(conn, 0)` (memaksa `v1_enabled = 0`), jangan menunggu.

---

## DECISION FOR ME — pengaitan otomatis

Saat ini menyalakan live **selalu manual**: `aktifkan_live` dipanggil manusia
dan tidak ada kode yang memanggilnya otomatis. Usulan (belum diterapkan):
setelah `shadow_day >= 7`, bot mengirim satu pesan Telegram berisi ringkasan
bukti shadow dan perintah `/aktifkan` yang tetap harus ditekan manusia —
tanpa pernah menyalakan sakelar sendiri.

Pilihan:

1. **Tetap manual** (sekarang). Paling aman, paling lambat.
2. **Pesan siap-aktif + tombol manusia** (usulan). Otomatis hanya
   pemberitahuannya; sakelar tetap butuh tekanan manusia.
3. **Aktif otomatis setelah 7 hari bersih.** Ditolak secara default: satu
   bug yang lolos dari tes akan langsung memakai uang.

Saya tidak mengubah `aktifkan_live` maupun `v1_enabled` sampai Anda memilih.
