# Rancangan: Shadow Runner — menjalankan periode shadow sungguhan

Tanggal: 2026-10-18 · Status: **dikerjakan setelah dokumen ini ditulis** (brainstorming
di-*opt-out* pengguna: "langkah saja" — keputusan dinyatakan terbuka di bawah, tanpa
gerbang persetujuan).

## 1. Mengapa dokumen ini ada

Tugas sebelumnya: "jalankan periode shadow 7–14 hari". Rekon (2026-10-18, jam 10.14)
menunjukkan **tiga jalur produksi yang belum pernah ada**, sehingga bot tidak bisa sekadar
dinyalakan:

| # | Temuan | Akibat bila dibiarkan |
|---|---|---|
| 1 | `telegram_gate.get_updates` wajib menerima `fetch` produksi; tidak ada satupun di repo | Denyut aktif pertama melempar `NotImplementedError` → loop keluar 1 → proses mati |
| 2 | `getUpdates` hanya dipanggil sekali per `HEARTBEAT_S = 300` detik, di dalam `tick_cycle` | Jendela konfirmasi reaktif 90 detik tidak akan pernah terpenuhi (jawaban dibaca ±5 menit sesudahnya → selalu `ignored`) — tombol Setuju tidak berguna |
| 3 | `main()` memakai `_konteks_kosong()` — "belum punya pembaca pasar" | Tanpa holdings/harga/saldo: nol usulan, G5 dijalankan di atas data kosong |

Bahan untuk #3 sudah ada dan terbukti jalan produksi (snapshot harian 21.00):
`portfolio.sync_trades`, `fetch_balances`, `fetch_price`, `compute_holdings`,
`portfolio_store.list_trades`.

## 2. Keputusan (dinyatakan, bukan ditanya)

| Keputusan | Nilai | Alasan |
|---|---|---|
| Interval bangun loop | `POLL_GAP_S = 15` dtk | Jawaban tombol ≤ ~15 dtk + `baca_harga` segar saat jawaban diproses → jendela 90 dtk realistis (tekan dalam ~60 dtk pertama) |
| Denyut penuh | tetap `HEARTBEAT_S = 300` dtk | Tick engine + pembaca pasar penuh tidak boleh lebih sering (biaya API, aturan spec) |
| Pembaca pasar | tiap denyut penuh: `sync_trades` + `fetch_balances` + `fetch_price` per `COIN_PAIRS` + `list_trades` → `engine.Context` | Komposisi ulang pola `portfolio.refresh` **tanpa** menulis snapshot (snapshot tetap milik tugas 21.00) |
| `reserve_idr` (cadangan terkunci) | **Rp 0** selama shadow | Tidak ada konfigurasi cadangan di mana pun (spec §6.4 hanya menyebut "cadangan terkunci" tanpa sumber angka). `free − 0 = free` jujur; `rules.reserve_buy_actions` dengan budget 0 → `< min_order` → reserve-buy diam sendiri. **Tidak mengarang angka.** Bila pengguna ingin reserve-buy bernominal: keputusan terpisah |
| `today_counts` / `last_fill_ts` / `held_pairs_24h` | dihitung dari riwayat `list_trades` (isi hari ini, isi terakhir per pair, pair dengan trade ≤ 24 jam) | Jujur terhadap kenyataan; selama shadow bot tak mengisi apa pun sehingga angka = riwayat manual/legacy. Pekerjaan go-live (baca `order_status` terisi) **dicatat, ditunda** |
| Gagal baca pasar | `print` 1 baris + tick DILEWATKAN + polling ditahan sampai bacaan berikutnya sukses | "Ragu = tidak kirim": tidak ada keputusan di atas data basi, tidak ada retry agresif |
| Harga saat jawaban diproses | `_polling` menerima `baca_harga` opsional; bila gagal → `ctx.prices[pair] = None` → `price_still_valid` = False → konfirmasi hangus | Angka di pesan harus mewakili harga saat ini (R-E); harga tak terbaca = tidak ada keputusan |
| Fetch produksi | `telegram_gate.live_fetch` (GET `api.telegram.org/bot…/getUpdates`, pola `alerts.send_telegram`), disuntik **hanya** dari blok `__main__` | Tes tak pernah menyentuh jaringan (conftest tetap jaga); `main()` dalam tes tak berubah perilaku |
| Baris sapa awal | `telegram.line(...)` hanya bila `fetch is not None` | Membuktikan token+chat hidup sejak detik pertama; tes sengaja tanpa fetch tetap `lines == []` |
| Peluncur | `run_v1.bat` + `register_v1_task.ps1` → tugas "IndodaxV1Shadow" (AtLogOn, `pythonw`, restart 3×, log `v1_loop.log`) | Mengikuti pola `register_task.ps1` yang sudah terbukti |

## 3. Bentuk perubahan kode

1. **`telegram_gate.live_fetch(token, *, offset, timeout) -> dict`**
   - Memanggil `https://api.telegram.org/bot{token}/getUpdates` dengan parameter
     `offset`/`timeout`; mengembalikan JSON hasil. `requests.get` dapat disuntik
     untuk tes (default: `requests.get` sungguhan — diblokir conftest bila tersentuh tes).

2. **`main_loop.baca_konteks(conn, *, now=None, sinkron=portfolio.sync_trades, ambil_info=indodax_api.account_info, ambil_harga=portfolio.fetch_price) -> engine.Context`**
   - `sinkron(conn)` dipanggil PERTAMA di dalam pembaca (diam-diam menulis
     riwayat; kegagalan per pair sudah ditelan `portfolio.sync_trades`).
     **Ruling penyelarasan:** rencana awal menaruh `sinkron` di `main` —
     dilipat ke pembaca supaya satu titik injeksi (lihat ledger Task 2).
   - `ambil_info()` → respons MENTAH `account_info` (bawaan produksi:
     `indodax_api.account_info`); `free_idr` = jumlah field `free` aset `idr`
     saja (bentuk TAPIv2 `balances` list; bentuk lain → `raise`).
   - `ambil_harga(pair)` → `float|None` untuk seluruh `portfolio.COIN_PAIRS`
     (bawaan produksi: `portfolio.fetch_price`); satu pun `None` → `raise`
     (tanpa angka parsial).
   - Menghitung `free_idr`, `prices` (seluruh `COIN_PAIRS`), `holdings`
     (`compute_holdings`), `today_counts`, `last_fill_ts`,
     `held_pairs_24h` (dari riwayat `list_trades`, tanggal lokal),
     `reserve_idr = 0.0`.

3. **`main_loop` — loop dipisah dua irama**
   - Konstanta `POLL_GAP_S = 15`.
   - Tiap bangun: (a) denyut penuh bila `now - denyut_terakhir >= HEARTBEAT_S`
     → `ctx = pembaca_konteks(conn)` (gagal = lewati + tahan polling) + `tick_cycle`;
     (b) selain itu: `_polling` ringan dengan `baca_harga` (hanya bila konteks terakhir
     berhasil dibaca).
   - Parameter baru `main(..., pembaca_konteks=None, baca_harga=None)` — bila keduanya
     `None`, perilaku persis seperti sekarang (tes lama tetap hijau).
   - `__main__` → `main(fetch=telegram_gate.live_fetch, pembaca_konteks=…, baca_harga=…)`.

4. **`_polling` / `tick_cycle`**: parameter opsional `baca_harga=None` (default: apa
   adanya — semua tes lama hijau). Sebelum `_jawab`, bila `baca_harga` tersedia:
   segarkan `ctx.prices[pair]` untuk usulan yang dijawab; gagal → `None`.

## 4. Tes (TDD — gagal dulu, baru implementasi)

- `live_fetch`: URL/parameter/offset/JSON; `requests` disuntik (tanpa jaringan).
- `baca_konteks`: konteks terisi dari balasan palsu; harga gagal → `None`;
  `today_counts`/`last_fill_ts`/`held_pairs_24h` dari riwayat trade; `reserve_idr == 0`.
- Loop dua irama: `--siklus` bangun tiap `POLL_GAP_S`; tick penuh pertama di bangun
  pertama; denyut berikutnya hanya setelah `HEARTBEAT_S`; `_polling` dipanggil tiap
  bangun; pembaca gagal → tick dilewati + polling ditahan + pesan log.
- `baca_harga` di `_polling`: sukses menulis harga segar ke `ctx.prices`; gagal →
  `None` → jawaban `ignored` (hangus), bukan memakai harga basi.
- Sumber `__main__` memuat `live_fetch` dan `pembaca_konteks` (pola tes sumber R-2).

**Kontrak tes lama yang berubah (satu baris, dengan alasan):**
`test_main_tanpa_pabrik_transport_melcatat_eksekusi_mati` — `tidur == [HEARTBEAT_S]`
menjadi `[POLL_GAP_S]`, karena bangun loop kini tiap 15 detik (denyut penuh tetap 300).
Tes akar `test_*.py` (46) tidak disentuh.

## 5. Di luar cakupan sengaja (dicatat, ditunda)

- Pekerjaan `order_status` terisi → `today_counts` pascago-live (shadow tak punya order).
- Angka cadangan terkunci non-nol & sakelar `reserve_buy` bernominal — keputusan pengguna.
- F41 (definisi jawaban sukses bursa), kunci trade-only, sakelar eksekusi (spec §16).

## 6. Runbook G5 (periode shadow)

- Mulai: **2026-10-18**, `shadow_day = 0`, kunci View-Only, tanpa transport.
- Cek harian: `SELECT shadow_day, v1_enabled FROM bot_state WHERE id=1;` dan
  `SELECT COUNT(*) FROM shadow_log WHERE status='rejected' AND hari=tanggal_hari_ini;`
  (penolakan = shadow_day reset ke 0; `shadow_day >= 7` menyalakan `v1_enabled`
  otomatis — AMAN karena transport tetap `None`: order mustahil keluar, dan guard F40
  menjawab Setuju tanpa transport dengan pesan jujur).
- Lolos G5 = 7–14 hari tanpa penolakan → baru keputusan pengguna: kunci trade-only,
  runner dengan `transport_factory`, sakelar eksekusi.
