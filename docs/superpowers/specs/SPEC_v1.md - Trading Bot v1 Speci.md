# SPEC_v1.md - Trading Bot v1 Specification
# OpenClaw Semi-Automated Trading Bot — Indodax
# Generated: 2026-10-15 19:47 GMT+7
# Decision gate: shadow mode 7–14 days, zero rejections → enable execution switch.

---

## 1. Philosophy
**"Ragu = tidak kirim."** Telegram confirmation is a gate, not a notification. The bot has **zero initiative** — it never executes unless the human explicitly approves, and it never retries a lost response. All risk guardrails are technical, not behavioural.

---

## 2. Core Rules (non-negotiable, V1 scope)

### 2.1 Order Idempotency & Kejujuran Data
- **`client_order_id`** wajib kirim saat `trade` (max 36 char, `0-9A-Za-z_-`).
- Kalau koneksi putus / kena 429 / tidak ada balasan → **tidak mengulang kirim**.
- Bot hanya boleh bertanya status lewat `GET /api/v2/openOrders` / `GET /api/v2/order` pakai `client_order_id` yang sama.
- Kalau hasil tanya ambigu (404 tanpa alasan jelas, atau respons tidak mengandalkan `status`) → **bot berhenti, catat, notifikasi**. Jangan coba lagi dengan ID lama.
- **Maximum 3 retry** dengan backoff `[2, 5, 10]` detik. Kalau still ambiguous → stop + notify.

### 2.2 Telegram Confirmation Windows
- **Main trade (take-profit / rebalance sell)**: 60–90 detik.
  - Pesan konfirmasi wajib menampilkan: pair, side, nominal IDR, jumlah koin, harga limit, aturan memicu, perkiraan fee 0,2%, sisa cadangan setelah order.
  - Kalau harga bergerak > 0,5% dari angka di pesan dalam 60–90 detik → konfirmasi hangus, batal, minta cek manual.
  - Tidak ada tombol "setuju semua".
- **DCA bulanan**: 24 jam.
  - Bot kirim konfirmasi pagi hari DCA (09:00 lokal).
  - Tombol `Setuju` / `Tunda` / `Batal bulan ini` berlaku sampai 09:00 keesokan harinya.
  - Setiap 6 jam bot kirim *satu* pengingat ringan kalau belum dijawab.
  - Lewat 24 jam → status `expired`, catat `DCA bulan ini dilewati`, **tidak ada order**.
  - Tidak ada "setuju semua bulan".

### 2.3 Spread Threshold (gerbang utama pair eligibility)
- **`MAX_SPREAD_BPS = 30`** (0,30% = 30 basis point).
- Bot mengambil `GET /api/depth/{pair_id}` pada saat akan bertindak.
- Jika `spread_bps > MAX_SPREAD_BPS` → **tidak dieksekusi otomatis**.
- **Notifikasi wajib**: Kirim satu baris Telegram: `"{pair} tidak dieksekusi: spread {spread_bps:.0f} bps > {MAX_SPREAD_BPS} bps"`.
- **Denylist tetap opsional** hanya untuk pengecualian permanent (stablecoin, pair delisted). Bukan daftar izin.

### 2.4 Hasil Balansa & Cadangan
- `GET /api/v2/account` mengembalikan `free`, `locked`, `canTrade`, `canWithdraw`.
- **Free** sudah bersih dari order terbuka. Aturan cadangan: `available = free - cadangan_terkunci`. Tidak perlu mengurangi order terbuka secara manual.
- **`canTrade: False`** dan **`canWithdraw: False`** terbaca dari bursa. Karena kunci view-only, order tidak akan pernah lolos — shadow mode bisa dijalankan dengan risiko eksekusi nol.
- Jika suatu saat **`canTrade: True`** → bot berhenti dan memberi tahu Anda. Aturan 8 jadi pengaman yang diverifikasi.

### 2.5 Disiplin Harga & Kegagalan Diam
- Data Indodax yang ditolak (WAF/429) / basi (terbaca Rp 0) / tidak tersedia → **bukan alasan menilai aset nol lalu tetap jalan**. Itu alasan berhenti.
- **Order terbuka (`openOrders`)** ditanya sebelum eksekusi selanjutnya. Selisih apa pun antara `portfolio.db` vs bursa → trading mati + Anda diberi tahu.
- **Pair sepi (SOL/BCH)**: tidak dieksekusi otomatis karena spread > 0,30%. BCH 0,95% ≈ 3× toleransi → butuh size jauh lebih kecil dari Rp 275.000, atau tidak dieksekusi sama sekali. Solusi: spread threshold ganti "pair sepi".

### 2.6 Batas Uang & Cadangan Terkunci
- **Satu order, satu kali**: maksimal Rp 275.000 per order.
- **Paling banyak 3 order per hari per aset**, plus jeda 15–30 menit setelah terisi.
- **Rugi harian** = drawdown dari total modal disetor > 15% → bot istirahat 24 jam + notifikasi (bukan mati total, cuma pause).
- **Belum boleh beli-jual berulang dalam 24 jam** (anti-loop fee).
- **Saldo bebas** = `free - cadangan terkunci`. Tidak boleh disentuh kode reserve.

### 2.7 Aturan yang Tidak Boetan Dibangun
- **Take-profit + rebalance** yang menyarankan penjualan digabung jadi satu aksi sebelum pesan Telegram dibuat. Jual parsial dihitung dari sisa posisi, bukan dari ukuran semula.
- **`portfolio.db`** hanya ditulis setelah bursa menyatakan terisi, batal, atau ditolak. Setiap putaran, saldo dicocokkan dengan bursa. Selisih sekecil apa pun → trading mati + Anda diberi tahu.
- **Kunci API view-only** tidak bisa menarik dana. Izin trade saja, withdraw mati, IP dikunci ke komputer ini.

---

## 3. Execution Switch (SAKELAR)
Enable ONLY after:
- Minimum 7–14 days mode bayangan.
- Setiap hari Anda buka log usulan → **tidak ada satu pun yang Anda tolak / ingin ubah parameter / ingin tambah pengecualian**.
- Setiap `expired` / `rejected spread` / `dilewati DCA` semua masuk akal dan tidak mengejutkan.
- Anda bisa menjelaskan ke orang lain *mengapa* setiap usulan itu muncul, tanpa menebak.
- **Jika ada satu pun ditolak → perbaiki aturan, reset mode bayangan dari nol.** Tidak "lanjutkan sambil perbaiki".

---

## 4. V1 Scope (What's IN v1)
| Component | Description |
|-----------|-------------|
| **DCA bulanan** | 24-jam konfirmasi window, pasif reminder tiap 6 jam. |
| **Take-profit (+50%/+100%)** | Hanya jual di ambang tersebut. Tidak ada stop-loss trailing. |
| **Reserve buy** | Hanya jika `total_disetor` surplus > 0. Definisikan di bawah. *(Lihat 4.1)* |
| **Pengaman teknis** | Spread threshold 0,30% + idempotensi `client_order_id` + notifikasi Telegram wajib + drawdown 15% dari total_disetor. |
| **Frame kerja** | Mode bayangan 7–14 hari → 0 penolakan → sakelar. |

### 4.1 Reserve Buy Parameter (jika dimasukkan v1)
```python
RESERVE_BUY_PCT = 10%          # dari surplus di atas cadangan
RESERVE_MAX_PER_DAY = 1       # max 1 order/hari
RESERVE_PAIRS = ["btc_idr", "eth_idr"]  # hanya pair ini
RESERVE_LIMIT_OFFSET = 0.1%   # harga limit = bid + 0.1%
```
Kalau Anda **tidak** mau reserve buy di v1, hapus seluruh blok di atas. Cadangan tetap terkunci, bot tidak boleh beli dari cadangan.

---

## 5. V2 Deferred (What's DEFERRED to v2)
| Component | Reason |
|-----------|--------|
| **Rebalance** | Risiko stacking "dua aturan menumpuk" (DCA beli + rebalance beli same asset). Butuh mode bayangan v2 sendiri, target allocation, band ±%, dan aturan anti-stacking: satu aset, satu konfirmasi per siklus, atau rebalance hanya saat TIDAK ADA DCA bulan itu. |
| **Trailing / Partial take-profit** | Desain disengaja (simpel) tapi butuh parameter baru. Jika mau → v3. |
| **Order history audit log** | Butuh DB schema + log structur. Bisa ditambahkan sesudah v1 stabil. |

---

## 6. Endpoints & Parameters (Ringkasan)

| Endpoint | Method | Parameter Wajib | Catatan |
|----------|--------|----------------|---------|
| `/api/v2/order` | POST | `method`, `nonce`/`timestamp`, `recvWindow`, `pair`, `type`, `btc`/`idr`, `order_type`, `client_order_id` | `recvWindow: 15000 ms` disankan (default 5000 terlalu sempit). |
| `/api/v2/openOrders` | GET | `symbol`, `limit`, `timestamp`, `recvWindow` | Poll max 1×/10 detik. Cache 10 detik. Jika 429 → backoff 30 detik. |
| `/api/v2/account` | GET | `symbol` (opsional) | Mengembalikan `free`, `locked`, `canTrade`, `canWithdraw`. |
| `/api/depth/{pair_id}` | GET | Tidak ada | `buy`/`sell` arrays `[price, amount]`. Spread = `(ask-bid)/mid*10_000`. |
| `/api/v2/order/histories` | GET | `symbol`, `limit` | Optional: cek order lama. |

---

## 7. Notifikasi Telegram (List Wajib)

Setiap kali ada perubahan status kirim satu baris:

| Trigger | Pesan |
|---------|-------|
| Spread rejected | `"{pair} tidak dieksekusi: spread {spread_bps:.0f} bps > 30 bps"` |
| Order filled | `"{pair} {side} Rp{nominal} berhasil diisi, order_id: {order_id}"` |
| Order expired | `"{pair} konfirmasi kadaluarsa (60s/24h), tidak ada order yang dikirim"` |
| DCA expired | `"DCA bulan ini dilewati (tidak dibalas dalam 24 jam)"` |
| Drawdown pause | `"Drawdown > 15% dari total modal disetor → bot istirahat 24 jam, notifikasi ini"` |
| Reconcile mismatch | `"Selisih portfolio.db vs bursa terdeteksi → trading mati, cek manual"` |
| Mode bayangan review | `"Hari ke-X mode bayangan: semua usulan diterima, siap sakelar"` |

---

## 8. State & Persistence (di DB/file)
| Field | Tipe | Keterangan |
|-------|------|------------|
| `total_disetor` | float (Rp) | Kumulatif, hanya bertambah tiap setoran. |
| `peak_equity` | float (Rp) | `max(equity_now)` SEJAK `total_disetor` terakhir bertambah. |
| `drawdown` | float (%) | `(total_disetor - equity_now) / total_disetor * 100`. |
| `last_dca_date` | date | Bulan dan tahun DCA terakhir dieksekusi (atau `dilewati`). |
| `v1_enabled` | boolean | Hanya `true` setelah 7–14 hari shadow mode 0 penolakan. |
| `reserve_buy_enabled` | boolean | Hanya `true` jika parameter di atas diedit & disetujui. |

---

## 9. Jalan Dokumentasi Selanjutnya
1. Isi `RESERVE_BUY_PCT` / `RESERVE_MAX_PER_DAY` sesuai keinginan (atau hapus kalau tidak mau).
2. Uji `recvWindow = 15000 ms` di mode bayangan hari 1–2: catat latency & 429 rate.
3. Mulai mode bayangan: kirim konfirmasi tapi **tidak** eksekusi selama 7–14 hari.
4. Setiap hari: baca log usulan → catat ada/tolak apa. Jika 0 penolakan → enable sakelar.
5. Catat `SPEC_v1.md` versi di `MEMORY.md` setiap kali ada update minor.

---
---
**Catatan akhir**: SPEC_v1.md adalah *single source of truth*. Seluruh kode harus mengikuti spesifikasi ini, bukan sekadar "mengingat" dari chat. Setiap perubahan minor harus di-update file ini dan direview sebelum di-_deploy_ ke mode bayangan.