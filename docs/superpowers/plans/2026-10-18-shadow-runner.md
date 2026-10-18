# Shadow Runner Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Menjalankan periode shadow sungguhan: bot membangun tiap 15 detik untuk membaca tombol Telegram (jendela 90 dtk realistis), denyut penuh + pembaca pasar tiap 5 menit, dengan fetch Telegram produksi — lalu didaftarkan ke Task Scheduler dan mulai menghitung G5.

**Architecture:** Tiga lapis baru di atas loop yang sudah ada, semuanya *opsional-injectable* sehingga tes lama tak berubah perilaku: (1) `telegram_gate.live_fetch` disuntik hanya dari blok `__main__`; (2) `main_loop.baca_konteks` menyusun `engine.Context` dari pola `portfolio.py` yang sudah jalan harian; (3) loop `main()` dipisah dua irama — `POLL_GAP_S=15` untuk `_polling` ringan, `HEARTBEAT_S=300` untuk `tick_cycle` + pembaca. Gagal baca = denyut dilewati + polling ditahan (ragu = tidak kirim).

**Tech Stack:** Python 3.14, pytest (`--basetemp=.ptmp` wajib), `requests` (sudah dipakai `alerts.py`), Task Scheduler Windows.

**Spec:** `docs/superpowers/specs/2026-10-18-shadow-runner-design.md` (keputusan & runbook G5); rujukan induk: `docs/superpowers/specs/2026-10-15-semi-auto-execution-design.md` §6.2/§6.3/§6.4/§9/§16.

## Global Constraints

- Bahasa pesan/log = Indonesia, bahasa sederhana.
- Tidak ada tes/modul yang boleh membuka koneksi jaringan; conftest akar memblokir `urlopen`/`requests.Session.request` — semua fungsi jaringan wajib disuntikkan di tes.
- Tes akar `test_*.py` (46) **tidak disentuh**; hanya SATU baris kontrak di `tests/test_main_loop.py` yang berubah (lihat Task 3, dengan alasan tercatat).
- "Ragu = tidak kirim": kegagalan baca tidak pernah menghasilkan angka (0/palsu/basi); tidak pernah retry order; tidak pernah memakai data basi untuk keputusan.
- Angka dalam spec = titik awal. `reserve_idr = 0` selama shadow (tidak mengarang cadangan).
- Kunci tetap View-Only; tanpa `transport_factory` order mustahil keluar (guard F40 tetap menjawab Setuju tanpa transport).
- Jalankan: `C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\python.exe` dari `C:\Users\KakiTangan\trading-bot`.
- Suite lengkap: `python -m pytest -q --basetemp=.ptmp` harus lulus semua (baseline 253 + tes baru).

## Review Focus

1. **Respons `account_info()` berbentuk tak dikenal** — `baca_konteks` wajib `raise` (denyut dilewati), bukan diam-diam `free_idr=0` yang bikin semua gerbang menolak palsu. → tes Task 2 `test_baca_konteks_bentuk_saldo_tak_dikenal_mengangkat`.
2. **Satu pair gagal harga (`None`, mis. 429)** — pembaca `raise`, tick dilewati + polling ditahan (tanpa angka parsial); pemulihan dicetak sekali. → tes Task 2 (`raise`) + Task 3 (denyut dilewati & polling ditahan, log sekali).
3. **Jaringan putus saat polling antar denyut** — loop tetap hidup (keluar 0), pesan log, offset Telegram TIDAK maju (update tetap tersimpan di server). → tes Task 3 `test_polling_antar_denyut_gagal_loop_tetap_hidup`.
4. **Tekan Setuju beberapa detik setelah denyut** — harga pasangan dijawab dibaca ulang (`baca_harga`) SEBELUM `on_answer`; gagal → `ctx.prices[pair] = None` → `price_still_valid` False → konfirmasi hangus, bukan memakai harga denyut 5 menit lalu. → tes Task 3 dua kasus.
5. **Dua proses berebut `getUpdates`** — hanya satu proses (task scheduler `MultipleInstances IgnoreNew`; `monitor.py` terverifikasi tak menyentuh Telegram). Verifikasi operasional di Task 5, bukan tes.

---

### Task 1: Fetch Telegram produksi (`live_fetch`)

**Files:**
- Modify: `telegram_gate.py` (tambah fungsi setelah `get_updates`)
- Test: `tests/test_telegram_gate.py` (tambah bagian baru — tes lama di file ini tidak diubah)

**Interfaces:**
- Produces: `telegram_gate.live_fetch(token: str, *, offset: int = 0, timeout: int = 0, get=None) -> dict` — `get` default `requests.get`; mengembalikan `resp.json()`. Dipakai `__main__` (Task 4) sebagai `fetch` produksi. Bentuk pemanggilan sudah dikunci `get_updates`: `fetch(token, offset=..., timeout=0)`.

- [ ] **Step 1: Tulis tes gagal** — `test_live_fetch_memanggil_url_telegram_dengan_offset`: `get` palsu mencatat `(url, params)`; assert URL `https://api.telegram.org/botTOKEN/getUpdates`, `params == {"offset": 7, "timeout": 0}`, hasil == dict JSON palsu. `test_live_fetch_tanpa_get_memakai_requests_asli` cukup memastikan default `get` adalah `requests.get` (dicek via `inspect`/identitas — TIDAK memanggilnya).
- [ ] **Step 2: Jalankan → gagal** (`python -m pytest tests/test_telegram_gate.py -k live_fetch -q --basetemp=.ptmp`) — `AttributeError: live_fetch`.
- [ ] **Step 3: Implement** di `telegram_gate.py`: bangun URL + `get(url, params=..., timeout=max(timeout, 10))` lalu `return hasil.json()`; tanpa try/except (pemanggil yang menangani — Task 3).
- [ ] **Step 4: Jalankan → lulus** + seluruh `tests/test_telegram_gate.py` lulus.
- [ ] **Step 5: Commit** `feat: tambah live_fetch produksi untuk getUpdates`

### Task 2: Pembaca pasar (`baca_konteks`)

**Files:**
- Modify: `main_loop.py` (dekat `_konteks_kosong`; impor `portfolio` + `indodax_api`)
- Test: `tests/test_main_loop.py` (bagian baru "Pembaca pasar produksi")

**Interfaces:**
- Consumes: `portfolio.parse_balances(info)`, `portfolio.compute_holdings(trades, coins, prices)`, `portfolio.COIN_PAIRS`, `portfolio_store.list_trades(conn)` (baris dict: `pair`, `side`, `price`, `qty`, `ts`, …).
- Produces: `main_loop.baca_konteks(conn, *, now=None, sinkron=portfolio.sync_trades, ambil_info=indodax_api.account_info, ambil_harga=portfolio.fetch_price) -> engine.Context`.
  - `sinkron(conn)` dipanggil PERTAMA (diam-diam menulis riwayat; `sync_trades` sudah menelan kegagalan per pair).
  - `ambil_info()` → respons mentah `account_info`; `free_idr` = jumlah `free` asset `idr` (bentuk TAPIv2 `balances` list). Bentuk lain → `raise ValueError`.
  - `ambil_harga(pair)` → `float|None` untuk SEMUA pair di `portfolio.COIN_PAIRS`; **satu pun `None` → `raise ValueError`** (tanpa angka parsial).
  - `coins` = `parse_balances(info)` tanpa `IDR` (qty otoritatif untuk holdings).
  - `reserve_idr = 0.0`.
  - `today_counts` = jumlah trade buy/sell dengan tanggal lokal == tanggal lokal `now`, per pair; `last_fill_ts` = `ts` maksimum per pair; `held_pairs_24h` = `{pair: ts trade ≤ 86400 detik dari now}` (kedua sisi).

- [ ] **Step 1: Tulis tes gagal** (5 tes, semua dengan fakes — tanpa jaringan):
  - `test_baca_konteks_mengisi_konteks_penuh`: saldo palsu TAPIv2 (`{"balances": [{"asset":"idr","free":"150000","locked":"50000"}, {"asset":"eth","free":"0.5","locked":"0"}]}`), harga palsu semua pair, `sinkron` pencatat pemanggilan, riwayat trade: 1 buy eth hari ini `ts=now-100`, 1 sell eth kemarin `ts=now-90000`, 1 buy btc `ts=now-7200`. Assert: `free_idr == 150000` (bukan 200000 — locked tidak dipakai), `reserve_idr == 0.0`, `prices` berisi semua `COIN_PAIRS`, holdings berisi eth dengan `qty == 0.5`, `today_counts == {"eth_idr": 1}`, `last_fill_ts["eth_idr"] == now-100`, `held_pairs_24h == {"eth_idr", "btc_idr"}`, `sinkron` dipanggil sekali dengan `conn`.
  - `test_baca_konteks_harga_satu_pair_gagal_mengangkat`: satu `ambil_harga` balik `None` → `pytest.raises(ValueError)`.
  - `test_baca_konteks_bentuk_saldo_tak_dikenal_mengangkat`: `ambil_info` balik `{}` → `raise ValueError`.
  - `test_baca_konteks_idr_dikunci_tidak_dihitung_free`: `free=0, locked=70000` → `free_idr == 0.0`.
  - `test_baca_konteks_tanpa_dca_untuk_konteks_lama` tidak perlu — `dca_enabled` tetap default `False` (diisi `main_loop` per siklus, sudah ada).
- [ ] **Step 2: Jalankan → gagal** (`python -m pytest tests/test_main_loop.py -k baca_konteks -q --basetemp=.ptmp`).
- [ ] **Step 3: Implement** `baca_konteks` di `main_loop.py` sesuai kontrak di atas. Pendekatan: urutan `sinkron` → `ambil_info` → validasi bentuk → `prices` (semua `COIN_PAIRS`, `None` = `raise`) → `coins` → `compute_holdings` → hitung tiga turunan riwayat dari `list_trades` dengan `time.localtime` (satu-satunya cara tanggal lokal — larangan `utcnow`).
- [ ] **Step 4: Jalankan → lulus** + seluruh `tests/test_main_loop.py` lulus.
- [ ] **Step 5: Commit** `feat: pembaca pasar produksi baca_konteks`

### Task 3: Loop dua irama + `baca_harga` + penjaga kegagalan

**Files:**
- Modify: `main_loop.py` (`_polling`, `tick_cycle`, `main`, konstanta, docstring `--siklus`)
- Test: `tests/test_main_loop.py` — bagian baru + **satu perubahan kontrak** (satunya-satunya tes lama yang disentuh): `test_main_tanpa_pabrik_transport_melcatat_eksekusi_mati` baris `assert tidur == [main_loop.HEARTBEAT_S]` → `assert tidur == [main_loop.POLL_GAP_S]` (alasan: bangun loop kini tiap 15 detik; denyut penuh tetap 300).

**Interfaces:**
- `POLL_GAP_S = 15` konstanta baru di `main_loop.py`.
- `_polling(conn, ctx, *, now, telegram, transport, shadow, verdict, fetch, baca_harga=None)`: sebelum `_jawab` tiap update yang mengenal id usulan — `try: ctx.prices[pair] = baca_harga(pair) except Exception: ctx.prices[pair] = None` (hanya bila `baca_harga is not None`).
- `tick_cycle(..., baca_harga=None)` — diteruskan ke `_polling`; parameter baru di belakang (default `None` → semua tes lama hijau).
- `main(..., pembaca_konteks=None, baca_harga=None)`:
  - Sapa awal: `if fetch is not None and not sapa_terkirim:` → `print` 1 baris + `telegram.line("Bot semi-otomatis berjalan — mode bayangan, tidak ada order yang bisa dikirim.")` (sekali, di luar DND).
  - Per bangun (di bawah `should_run_habitual`): denyut penuh bila `denyut_terakhir is None or now - denyut_terakhir >= HEARTBEAT_S`.
    - Denyut penuh: `pembaca_konteks is None` → konteks disuntik dianggap sah; selain itu `try: ctx = pembaca_konteks(conn); konteks_baik=True except Exception as e:` → `konteks_baik=False`, cetak SAAT MASUK kegagalan saja (`gagal_terakhir` flag) `"Pembaca pasar gagal — denyut dilewatkan, tidak ada keputusan: {e}"`. Bila sah → `tick_cycle(..., baca_harga=baca_harga)` + `denyut_terakhir = sekarang`; `denyut_terakhir TIDAK di-update` bila gagal (retry baca berikutnya 15 dtk kemudian, bacaan = tanpa side-effect).
    - Selain itu (inter-denyut): bila `konteks_baik` → `try: _polling(..., baca_harga=baca_harga) except Exception as e:` → cetak sekali-per-transisi `"Polling Telegram gagal — dilewati: {e}"` (offset tak maju, update tetap di server).
  - `tidur(POLL_GAP_S)`; `siklus` dihitung per bangun; docstring/help: `"Jumlah bangun tidur (tiap POLL_GAP_S) lalu keluar; denyut penuh tiap HEARTBEAT_S."`

- [ ] **Step 1: Tulis tes gagal** (semua di `tests/test_main_loop.py`; pola `jam`/`tidur` yang mengalir: `tidur=lambda s: setattr… t[0] += s`):
  - `test_dua_irama_tick_tiap_heartbeat_polling_tiap_bangun`: monkeypatch `main_loop.tick_cycle` pencatat; `fetch` pencatat; `jam` mengalir 15 dtk per `tidur`; `--siklus 3` jam 12.00 → assert `tick_cycle` dipanggil 1× (bangun pertama), `fetch` dipanggil 3× (denyut + 2 inter-denyut), `tidur` == `[15, 15]`… (perhatikan: `--siklus 3` → 3 bangun → 2 tidur bila loop berhenti sebelum tidur ketiga — ikuti struktur yang sebenarnya).
  - `test_pembaca_gagal_denyut_dilewati_polling_ditahan`: `pembaca_konteks` pertama sukses (denyut 1 jalan), kedua melempar (denyut 2 & inter menyusul) → assert `tick_cycle` hanya 1×, `_polling`/`fetch` TIDAK dipanggil lagi, stdout memuat `"Pembaca pasar gagal"` TEPAT SATU KALI meski dua kegagalan beruntun, keluar 0.
  - `test_polling_antar_denyut_gagal_loop_tetap_hidup`: denyut 1 sukses (fetch sukses), inter-denyut berikut `fetch` melempar → keluar 0, stdout memuat `"Polling Telegram gagal"` sekali, `fetch` berikutnya (bangun ke-3) dipanggil lagi (pemulihan).
  - `test_baca_harga_menyegarkan_harga_sebelum_jawaban`: daftarkan usulan (pola `_ingat`/`_USULAN_MENUNGGU` yang ada), kirim update `setuju:id` lewat fetch palsu, `baca_harga` balik harga baru → `ctx.prices[pair]` berubah sebelum `on_answer` (palsukan verdict/telegram seperti tes tombol yang ada).
  - `test_baca_harga_gagal_konfirmasi_hangus`: `baca_harga` balik `None` → jawaban `ignored` + pesan hangus, tanpa order, tanpa exception.
  - `test_sapa_awal_hanya_bila_ada_fetch`: `fetch` disuntik (walau gagal nanti tidak penting) → `telegram.line` pertama memuat `"mode bayangan"`; tes lama tanpa fetch → tetap `lines == []` (dijamin tes `test_denyut_jantung_diluar_jam_aktif...` yang sudah ada).
  - Ubah SATU baris kontrak `tidur == [HEARTBEAT_S]` → `[POLL_GAP_S]` di `test_main_tanpa_pabrik_transport_melcatat_eksekusi_mati`.
- [ ] **Step 2: Jalankan → gagal** (`python -m pytest tests/test_main_loop.py -q --basetemp=.ptmp` — tes baru merah; baris kontrak berubah ikut merah pada tes lama yang relevan).
- [ ] **Step 3: Implement** sesuai Interfaces di atas; JANGAN sentuh badan `tick_cycle` selain meneruskan `baca_harga`.
- [ ] **Step 4: Jalankan → lulus** seluruh `tests/test_main_loop.py`, lalu **seluruh suite** `python -m pytest -q --basetemp=.ptmp` (253 + tes baru, nol gagal).
- [ ] **Step 5: Commit** `feat: loop dua irama 15 detik + penyegaran harga saat jawaban`

### Task 4: Penyambungan `__main__` produksi

**Files:**
- Modify: `main_loop.py` blok `if __name__ == "__main__":`
- Test: `tests/test_main_loop.py` (tes sumber, pola R-2)

**Interfaces:**
- Produces: jalur CLI `python main_loop.py` = `main(fetch=telegram_gate.live_fetch, pembaca_konteks=baca_konteks, baca_harga=portfolio.fetch_price)`; tanpa argumen `main()` tetap persis perilaku lama (tes).

- [ ] **Step 1: Tulis tes gagal**: `test_main_blokinisialisasi_memuat_jalur_produksi` — `inspect.getsource(main_loop)` memuat `"live_fetch"` dan `"pembaca_konteks=baca_konteks"`; dan `inspect.getsource(main_loop.main)` TIDAK memuat `"live_fetch"` (jalur produksi tidak boleh ada di dalam `main` — conftest menjaga tes).
- [ ] **Step 2: Jalankan → gagal.**
- [ ] **Step 3: Ganti** blok `__main__` menjadi `raise SystemExit(main(fetch=telegram_gate.live_fetch, pembaca_konteks=baca_konteks, baca_harga=portfolio.fetch_price))` (impor `portfolio` sudah masuk lewat Task 2).
- [ ] **Step 4: Jalankan → lulus** + suite penuh hijau.
- [ ] **Step 5: Commit** `feat: sambungkan jalur produksi main_loop di blok __main__`

### Task 5: Peluncur, Task Scheduler, nyalakan shadow, verifikasi, ledger G5

**Files:**
- Create: `run_v1.bat` (pola `run_snapshot.bat`), `register_v1_task.ps1` (pola `register_task.ps1`: AtLogOn, `pythonw -u main_loop.py >> v1_loop.log 2>&1`, `MultipleInstances IgnoreNew`, restart 3×/1 mnt, `ExecutionTimeLimit` nol, nama tugas `IndodaxV1Shadow`)
- Create/Update: `.superpowers/sdd/2026-10-18-periode-shadow/progress.md` (ledger G5)
- Opsional: pastikan `v1_loop.log` masuk `.gitignore`

**Interfaces:**
- Consumes: Task 1–4 (jalur CLI produksi).

- [ ] **Step 1: Uji satu-satunya pencari `getUpdates`** — pastikan tidak ada proses lain memakai token (rekon: `monitor.py` tanpa Telegram; satu-satunya pemakai baru nanti). Verifikasi: `Get-Process python*` + catat PID lama di ledger.
- [ ] **Step 2: Uji jaringan sekali (operasional, bukan tes)** — `python -c` memanggil `telegram_gate.live_fetch(TELEGRAM_TOKEN)` (offset 0, tanpa konsumsi offset) → balasan `{"ok": true, ...}`; dan `python -c "import indodax_api as A; print(A.account_info()['canWithdraw'])"` → `False`.
- [ ] **Step 3: Smoke test foreground** — `python main_loop.py --siklus 1` (jam aktif) → keluar 0, tanpa `Gagal menjalankan loop`, baris mode bayangan tercetak, pesan sapa sampai ke Telegram; periksa `bot_state` terbentuk (`shadow_day` akan naik lewat `apply_shadow_gate` pada denyut).
- [ ] **Step 4: Buat + jalankan tugas scheduler** — `register_v1_task.ps1`, `schtasks /run /tn IndodaxV1Shadow`; verifikasi: proses `pythonw` hidup, `v1_loop.log` tumbuh, setelah ≥1 menit tidak ada `"Gagal"`, pesan sapa hanya SATU (getUpdates punya satu pemilik).
- [ ] **Step 5: Ledger G5** — tulis `.superpowers/sdd/2026-10-18-periode-shadow/progress.md`: mulai `2026-10-18`, `shadow_day` awal, cek harian (`SELECT shadow_day, v1_enabled FROM bot_state WHERE id=1`), aturan reset bila penolakan, kalender `day 7 ≈ 2026-10-24`, daftar tundaan (order_status→counts, cadangan non-nol, F41, kunci trade).
- [ ] **Step 6: Commit** `chore: peluncur shadow — task scheduler, ledger G5` + jalankan suite penuh terakhir (pastikan berkas baru tidak mengubah hasil tes).

---

## Self-review (ringkas)

- Cakupan spec: fetch (T1), pembaca (T2), dua irama + penjaga (T3), penyambungan (T4), operasi+G5 (T5) — §6.2 (jendela 90 dtk) di T3, §6.3 (harga segar/hangus) di T3, §6.4 (free bersih, reserve 0) di T2, §9 (shadow gate tak tersentuh — T3 hanya meneruskan), §16 (key/switch di luar cakupan, dicatat di ledger).
- Tipe konsisten: `live_fetch(token, *, offset, timeout, get)` ↔ pemanggilan `get_updates`; `baca_konteks(conn, *, now, sinkron, ambil_info, ambil_harga)` ↔ `pembaca_konteks(conn)`; `baca_harga(pair) -> float|None` di `_polling`/`tick_cycle`/`main`.
- Review Focus: 5 baris, semuanya punya tes di task yang memilik kecuali #5 (operasional, verifikasi T5).
- Proporsi: dokumen ini jauh lebih pendek dari spec; badan kode diserahkan ke implementer.
