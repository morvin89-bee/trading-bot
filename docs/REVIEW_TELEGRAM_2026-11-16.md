# Review 2026-11-16 — Telegram 401, denyut yang membeku, dan recvWindow

Dokumen ini menjawab butir 4 dan 5 review. Isinya **laporan + usulan**; satu
cacat tanda tangan v1 sudah diperbaiki karena murni defect (timestamp
dibekukan sebelum retry), sisanya menunggu keputusan pemilik.

## 1. Kejadian dan bukti

| Waktu (WIB) | Bukti | Arti |
|---|---|---|
| sebelum 07.00 | proses sudah berjalan (jam tenang 23.00-07.00) | tidak ada denyut yang diizinkan |
| 07.00:09 | `pair_meta.fetched_ts` = `pair_meta_state.last_attempt_ts` = 07.00:09 (450 baris) | denyut penuh pertama setelah DND berakhir; langkah 2 berhasil |
| 07.00:14 | `errors.log`: `Denyut penuh gagal ... 401 Client Error ... getUpdates` | langkah 4 (polling) gagal 5 detik setelah metadata |
| 07.00:14 → 20.03:33 | 127 baris 401, jarak ~5,7 menit | setiap denyut penuh memukul API dan gagal |
| 09.56, 18.42, 19.23 | `v2 HTTP 400: Invalid timestamp ... outside of the recvWindow` | jalur pembaca pasar (v2) |
| sekarang | `bot_state`: `shadow_day = 0`, `shadow_checked_day = '2026-11-03'`, `status_terakhir_hari = NULL`, `sapa_terakhir_ts = NULL` | hari bayangan beku sejak 3 November |
| sekarang | `proposals` terakhir `2026-11-02` | tidak ada usulan baru |

Kesimpulan: bot **hidup** (proses jalan, metadata segar) tetapi **tidak pernah
menyelesaikan satu denyut penuh** sejak 07.00. Tidak ada satu angka pun yang
menjawab "kapan terakhir bot bekerja" — karena itu kolom `denyut_ok_ts` dan
`galat_terakhir` ditambahkan (commit `496376f`).

## 2. Langkah `tick_cycle` yang dilewati

Urutan di `main_loop.tick_cycle` dan nasibnya saat langkah 4 gagal:

| # | Langkah | Saat polling gagal (401) | Saat jam tenang (DND) |
|---|---|---|---|
| 1 | rekonsiliasi buku vs bursa | **jalan** (sebelum polling) | tidak jalan |
| 2 | `pair_meta.refresh` | **jalan** (sebelum polling) | tidak jalan |
| 3 | `permissions_ok` (bila ada transport) | **jalan** (sebelum polling) | tidak jalan |
| 4 | polling Telegram (`_polling`) | **melempar** | tidak jalan |
| 5 | pengingat DCA (`should_remind`) | **dilewati** | tidak jalan |
| 6 | `engine.tick` (usulan + pesan konfirmasi) | **dilewati** | tidak jalan |
| 7 | `apply_shadow_gate` (hari bayangan) | **dilewati** | tidak jalan |

Mekanismenya: `_polling` tidak menangkap galat `getUpdates`; galat naik ke
`tick_cycle`, lalu ke penjaga denyut di `main` ("Denyut penuh gagal — dilewati,
dicoba lagi"). Karena galat keluar dari fungsi, langkah 5-7 **tidak pernah
dieksekusi**. Langkah 1-3 sudah berjalan lebih dulu, jadi metadata tetap segar
walau denyut dinyatakan gagal.

Tes yang mengunci perilaku ini: `tests/test_tick_urutan.py` (langkah 5-7 tidak
dipanggil saat polling gagal; jam tenang tidak menjalankan apa pun).

## 3. Mengapa `pair_meta` baru disegarkan 07.00:09

Bukan cacat penjadwalan:

1. `pair_meta.refresh` hanya dipanggil di langkah 2 `tick_cycle`, dan
   `tick_cycle` hanya berjalan di dalam `should_run_habitual(now)`.
2. DND = 23.00-07.00 (`dca_window.DND`). Proses yang hidup sejak dini hari
   tidak menyentuh bursa sampai 07.00.
3. Setelah berhasil, `_refresh` berhenti mencoba selama cache masih lebih muda
   dari `MAX_AGE_S` (24 jam) — jadi penyegaran berikutnya memang ~besok 07.00,
   bukan tiap denyut.
4. Percobaan yang GAGAL dibatasi `RETRY_S` (15 menit) dan dicap SEBELUM
   jaringan, jadi kegagalan tidak mengikuti denyut 15 detik.

07.00:09 = denyut penuh pertama yang diizinkan, bukan refresh yang tertunda.

## 4. Mengapa hari bayangan tidak maju

`apply_shadow_gate` adalah langkah 7. Ia tidak pernah berjalan karena langkah 4
melempar lebih dulu, sehingga:

- `shadow_checked_day` tetap `'2026-11-03'` — penanda "hari ini sudah dihitung"
  tidak pernah diperbarui;
- `shadow_day` tetap `0` — penolakan 3 November menolkannya, dan tidak ada
  denyut sukses yang menaikkannya lagi;
- bahkan bila langkah 7 sempat berjalan, ia menaikkan paling banyak **sekali per
  hari lokal** (penanda hari), jadi bukan penyebab utama.

Akar masalahnya: "tidak ada denyut sukses" bukan "logika hari salah".

## 5. Usulan langkah baca-saja (belum diubah)

Yang **tidak** boleh tetap jalan saat Telegram mati: langkah 5 dan 6 — keduanya
mengirim pesan, dan pesan konfirmasi tanpa kemampuan menerima jawaban hanya
menumpuk antrian.

Kandidat yang secara teknis baca-saja: `apply_shadow_gate` (menulis
`bot_state`, membaca `proposals`; tidak menyentuh order dan tidak mengirim
pesan). Namun menaikkan hari bayangan saat bot tidak bisa menerima persetujuan
akan **melemahkan G5**: 7 hari itu seharusnya hari di mana bot benar-benar
bisa bekerja. Karena itu usulan saya:

- **Opsi A (disarankan, tanpa perubahan kode):** biarkan hari bayangan beku
  saat Telegram mati. Hari yang tidak bisa menerima persetujuan tidak layak
  dihitung. Cacat sesungguhnya bukan beku-nya, melainkan tidak terlihatnya
  keadaan itu — sudah ditutup oleh `denyut_ok_ts` (dashboard + preflight).
- **Opsi B (butuh keputusan):** jalankan `apply_shadow_gate` saja saat polling
  gagal, di balik konstanta bernama (mis. `SHADOW_GATE_SAAT_POLLING_GAGAL =
  False`). Risikonya disebut di atas; jangan dinyalakan tanpa keputusan
  pemilik.

Jam tenang (DND) tetap tidak menjalankan apa pun. Konsekuensinya
`denyut_ok_ts` menua setiap malam — karena itu dashboard dan preflight
mengecualikan DND (`main_loop.should_run_habitual`), bukan mencatat "sehat"
palsu.

## 6. `recvWindow` dan penandatanganan

### Temuan

| Jalur | Timestamp | `recvWindow` | Retry |
|---|---|---|---|
| v1 (`_v1_call`, order/`tradeHistory`/`getInfo`) | **dulu** dihitung sekali SEBELUM loop | **tidak dikirim** (bawaan server, tidak terdokumentasi) | 3× (1 + 2 detik + jitter) |
| v2 (`_v2_get`, `myTrades`/`account`) | dihitung saat kirim | **15000 ms** | tidak ada |

Cacat v1: percobaan ketiga membawa timestamp yang sudah berumur 3-4 detik —
cukup untuk "Invalid timestamp ... outside of the recvWindow" di jalur lambat,
dan tanda tangannya tidak lagi mewakili waktu kirim. **Sudah diperbaiki**
(commit `8519409`): timestamp + tanda tangan + `Request` dibuat ulang per
percobaan. Tes: `tests/test_indodax_tanda_tangan.py`.

Galat 400 yang benar-benar terlihat (09.56, 18.42, 19.23) berasal dari **v2**
(`Pembaca pasar gagal ... v2 HTTP 400`), yang tidak punya loop retry. Jadi
sebabnya bukan timestamp beku, melainkan **selisih jam / permintaan yang
tertahan** lebih dari 15 detik.

### Usulan koreksi offset jam (belum diimplementasikan)

Preflight sudah mengukur selisih jam (`preflight._waktu_bursa()` terhadap
`server_time`) dengan toleransi `TOLERANSI_JAM_DETIK = 5.0`.

Usulan:

1. Simpan `offset_bursa_ms` di `bot_state` (migrasi idempoten, pola yang sama
   dengan `denyut_ok_ts`).
2. Perbarui dari `GET /api/server_time` sebelum panggilan bertanda tangan
   pertama tiap denyut (satu permintaan publik, tanpa kunci).
3. Pakai `timestamp = int((time.time() + offset) * 1000)` di v1 dan v2.
4. Tolak offset di luar batas wajar (mis. ±60 detik): lebih baik gagal jelas
   daripada menandatangani dengan jam yang salah. Tidak ada tebakan.
5. Catat satu baris per jam bila |offset| > `TOLERANSI_JAM_DETIK` — pola
   `_catat_galat_telegram`.
6. Tes: jam disuntikkan, `server_time` palsu, timestamp yang dikirim harus
   sudah terkoreksi; offset di luar batas = galat, bukan koreksi buta.

Ini menyentuh jalur tanda tangan yang dipakai order, jadi **tidak** diubah
tanpa keputusan pemilik.

## 7. DECISIONS FOR ME

1. **Timestamp v1 per percobaan — sudah dikerjakan** (`8519409`). Ini murni
   defect: tidak ada gerbang keselamatan yang berubah.
2. **Hari bayangan saat Telegram mati:** biarkan beku (Opsi A, disarankan)
   atau jalankan `apply_shadow_gate` saja di balik konstanta (Opsi B)? Opsi B
   melemahkan makna G5.
3. **`recvWindow` untuk v1:** kirim eksplisit 15000 ms seperti v2, atau
   biarkan bawaan server? Menambah parameter di jalur order perlu persetujuan.
4. **Koreksi offset jam:** setuju diterapkan? Butuh batas offset yang
   diizinkan dan sumber pembaruan (tiap denyut vs sekali sejam).
5. Tidak ada perubahan pada keputusan "DCA: satu percobaan per bulan"
   (2026-10-17) maupun parameter strategi.
