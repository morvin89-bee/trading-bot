# Semi-Auto Execution (v1) — Desain

- **Tanggal:** 2026-10-15
- **Status:** disetujui pengguna 2026-10-15
- **Rencana implementasi:** `docs/superpowers/plans/2026-10-15-semi-auto-execution.md`
- **Lokasi proyek:** `C:\Users\KakiTangan\trading-bot`
- **Pendahulu:** `2026-10-14-portfolio-growth-system-design.md` (tetap mengikat)
- **Digabung dari:** `SPEC_v1.md - Trading Bot v1 Speci.md` (lihat §17 untuk rekonsiliasi)
- **Dokumen pendukung:** `docs\MIGRASI-VPS.md` (runbook pindah VPS)

> **Tidak ada yang dibangun sebelum dokumen ini disetujui.**

---

## 1. Maksud & Latar

Sistem yang sudah jalan (`feat/portfolio-growth`, merged `ce0047f`) berdiri 100% read-only.
Ia menghitung, menyarankan, dan memberi tahu — tetapi tidak pernah mengirim order. Semua
saran DCA, take-profit, dan reserve buy hari ini adalah **nasehat**.

Pengguna ingin langkah berikutnya: bot **mencatat dan mengeksekusi** sesuai aturan yang
sudah disepakati, dengan **konfirmasi Telegram untuk setiap aksi**. Dana terkait kebutuhan
biaya hidup, sehingga pengaman adalah isi utama dokumen ini — bukan pelengkapnya.

Dokumen ini menggabungkan tiga sumber: putusan langsung pengguna selama brainstorming,
recon API Indodax 2026-10-15 (baca saja), dan `SPEC_v1.md`. Setiap perbedaan dicatat di
§17 — tidak ada yang disatukan diam-diam.

**Prinsip yang menentukan seluruh desain:**

> **Ragu = tidak kirim.** Konfirmasi Telegram adalah lapisan tambahan, **bukan**
> pengganti pengaman teknis. Bot punya **nol inisiatif** — tidak pernah mengeksekusi tanpa
> persetujuan manusia, tidak pernah mencoba ulang balasan yang hilang. Bot yang benar
> adalah yang **berhenti sendiri** sebelum pengguna sempat menekan Setuju.

---

## 2. Tujuan (Goals)

- **G1** Bot mengeksekusi order **hanya** setelah konfirmasi Telegram yang menampilkan
  seluruh data yang relevan.
- **G2** Kegagalan apa pun **berhenti dengan diam** — tidak mengulang, tidak mengarang
  angka, tidak memakai data lama.
- **G3** Saldo cadangan IDR **tidak bisa disentuh kode**.
- **G4** Setiap aksi dapat ditelusuri ke **satu aturan** yang memicunya, sehingga pengguna
  bisa menjelaskan setiap usulan *tanpa menebak*.
- **G5** Mode bayangan menjadi **review atas keputusan**, bukan uji ketahanan kode.
- **G6** Sistem tetap portabel: inti berjalan di Linux/VPS tanpa penulisan ulang.
- **G7** Setiap perubahan status menghasilkan **satu baris notifikasi** (§12.1) — tidak
  ada kegagalan yang hilang tanpa jejak.

## 3. Non-Goals (sengaja TIDAK dilakukan)

**v1:**

- **Rebalance** — lihat §10. Modul v2 terpisah, dengan mode bayangan v2 sendiri.
- **Withdraw / penarikan dana** — izin tidak pernah dicentang, dicek berulang.
- **Market order** — hanya limit.
- **Perangkat penilaian waktu pasar** apa pun. Tidak ada "jangan beli saat turun N%".
- Multi-exchange, leverage/margin, prediksi harga, sinyal FOMO.

**v3 / sesudah v1 stabil (dari SPEC_v1 §5):**

- Trailing / partial take-profit — desain sederhana sudah disengaja, tetapi butuh
  parameter baru. **V3.**
- Order history audit log — butuh skema DB dan struktur log terpisah. **Sesudah v1 stabil.**

## 4. Anti-Goals (gagal walaupun "jalan")

- Mengulang pengiriman order karena balasan hilang / timeout / HTTP 429.
- Menilai aset jadi **Rp 0** ketika harga hilang, lalu tetap melanjutkan.
- Menumpuk dua aturan menjadi satu aksi yang tidak pernah disetujui siapa pun.
- Menggerakkan cadangan IDR, melebihi atap per-order, atau menembus pagu per hari.
- Menulis `portfolio.db` sebelum bursa menjawab status order.
- Menambah pengaman berbasis keadaan pasar (lihat §6.5).
- Menyembunyikan aksi yang gagal: setiap penolakan **harus** sampai ke pengguna.
- Membisukan peringatan karena dianggap "sudah pernah dibahas".

---

## 5. Batasan (Constraints)

- **Modal sekarang:** ± Rp 206.236 (IDR 203.394 + dust BCH/ETH). Kontribusi tercatat Rp 0.
- **Setoran bulanan:** maksimum **Rp 500.000**. Alokasi: BTC 275k / ETH 125k / SOL 50k /
  cadangan 50k.
- **Atap per order:** Rp 275.000. **Pagu/hari: 3 order per aset** (bukan global).
  **Jeda:** 15–30 menit setelah terisi.
- **Fee:** taker 0,2% (perhitungan take-profit sudah net fee, di **bid**).
- **Min order:** Rp 10.000 untuk keempat pair.
- **Mesin:** Windows, nyala 24 jam selama listrik ada; VPS menyusul.
- **Bahasa:** Indonesia, bahasa sederhana. Akses tetap lewat Tailscale.

---

## 6. Aturan pengaman (inti dokumen)

### 6.1 Satu order, satu kali

- **Maksimal satu order "di udara"** pada satu waktu.
- Order dikirim dengan **client order id tetap** (≤ 36 karakter, hanya `[0-9A-Za-z_-]`).
- Bila balasan hilang (koneksi putus, 429, timeout): **jangan mengulang kirim**. Tanyakan
  status order itu memakai **ID yang sama**, lewat `GET /api/v2/order` atau
  `GET /api/v2/openOrders`.
- **Maksimal 3 kali bertanya**, dengan jeda bertahap **`[2, 5, 10]` detik**.
- Masih ambigu (404 tanpa alasan jelas, atau respons tidak mengandung `status`) →
  **berhenti, catat, beri tahu pengguna.** Jangan coba lagi dengan ID lama.
- Ambigu berarti **berhenti**, bukan mengulang. Lihat §11.3 tentang jebakan 404.

### 6.2 Telegram adalah gerbang, bukan notifikasi

Pesan konfirmasi wajib memuat: **pair · beli/jual · nominal IDR · jumlah koin · harga
limit · aturan yang mempicu · perkiraan fee 0,2% · sisa cadangan setelah order.**

- Jendela reaktif: **60–90 detik** (usulan desain: **90 detik**).
- Jendela DCA: **24 jam** (§7.2).
- Harga bergeser > **0,5%** dari angka di pesan dalam jendela → konfirmasi **hangus**,
  batal, minta cek manual.
- **Tidak ada tombol "setuju semua"**, dan **tidak ada "setuju semua bulan"**.

### 6.3 Harga hilang berarti tidak ada order

Data yang ditolak WAF/429, berumur > 10 detik, atau terbaca Rp 0 → **bukan alasan menilai
aset nol lalu tetap jalan; itu alasan berhenti.**

- **Hanya limit.** Harga beli tidak boleh > **0,3%** di atas pasar.
- `openOrders` ditanya sebelum eksekusi berikutnya.
- Selisih apa pun antara `portfolio.db` dan bursa → **trading mati + pengguna diberi tahu.**

### 6.4 Uang punya atap keras

| Batas | Nilai | Bila dilewati |
|---|---|---|
| Per order | Rp 275.000 | order ditolak |
| Per aset | **3 order per hari per aset** | order berikut untuk aset itu tidak dikirim |
| Jeda | 15–30 menit setelah terisi | ditahan |
| Beli↔jual aset sama | ≤ 1× per 24 jam | aset dijeda |

Cadangan: **saldo yang boleh dipakai = `free` − cadangan terkunci.** Jika hasilnya <
ukuran order → tolak. *(Hasil recon: `free` sudah bersih dari order terbuka — §11.2.)*
Order tertunda tetap mengurangi saldo tersedia walaupun di layar masih terlihat "ada".

### 6.5 Yang DIHAPUS dari rancangan awal

> Dicatat eksplisit agar tidak dikembalikan oleh builder atau dokumen lain.

- **Rugi harian 2%** — dihapus.
- **Max drawdown 10–15%** — dihapus. *(Sudah berulang kali muncul kembali, termasuk di
  `SPEC_v1.md` §2.6/§4/§7/§8 — lihat §17. Tidak dimasukkan.)*
- **Daftar pair tetap** — diganti ambang spread terukur (§8).

Alasannya satu dan sama: keduanya adalah **perangkat penilaian waktu pasar** yang mengenakan
baju keselamatan. "Jangan beli saat turun 15%" adalah keputusan bahwa sekarang bukan waktu
beli — persis penilaian yang **dilakukan oleh DCA** agar pengguna tidak melakukannya.
Pengaman semacam itu akan **menahan DCA** tepat saat disiplin paling dibutuhkan, dan
berbenturan langsung dengan filosofi sistem.

Pengaman yang tersisa terbagi rapi:

- **Gerbang per-aksi** (§6.4, §8): menilai *satu order pada satu saat*, tidak pernah
  mematikan sistem.
- **Pengaman global karena sistem bermasalah** (§6.6): pemicunya bukan pasar turun,
  melainkan *sesuatu yang salah dengan bot*.

### 6.6 Dua aturan tidak menumpuk, dan buku bukan kenyataan

- Saran take-profit dan rebalance yang menunjuk penjualan yang sama **digabung di lapisan
  aturan, sebelum** pesan Telegram dibuat. Bug "25% + 25% = jual 50%" harus mati di sini.
- Penjualan parsial dihitung dari **sisa posisi**, bukan ukuran semula.
- `portfolio.db` **hanya** ditulis setelah bursa menyatakan terisi / batal / ditolak.
- Setiap putaran: saldo & order terbuka dicocokkan dengan bursa. **Selisih sekecil apa
  pun** → trading dimatikan + pengguna diberi tahu.
- Kunci API **trade-only, withdraw mati, IP dikunci**. Kunci View-Only tetap terpisah.

**Pengaman izin yang dibaca dari bursa setiap putaran:**

| Kondisi | Tindakan |
|---|---|
| `canWithdraw: True` | **Berhenti + beri tahu** — izin tarik dana tidak boleh ada. |
| `canTrade` **tidak cocok dengan status sakelar** | **Berhenti + beri tahu.** Bayangan menyala & `canTrade: True` → ada kunci trade terpasang padahal belum waktunya. Sakelar mati & `canTrade: False` → eksekusi tidak akan bisa jalan. |

Mode bayangan **secara fisik** tidak bisa mengirim order karena `canTrade: False`.

---

## 7. Perilaku sinyal, kadaluarsa, dan jendela

### 7.1 Sinyal reaktif (take-profit, reserve buy)

1. **Kadaluarsa = batal.** Jendela lewat tanpa jawab → status `expired`, dicatat ke log,
   **tidak ada order**. Tidak dicoba ulang dengan harga lama, tidak disimpan ke antrean.
2. **Notifikasi sekali.** Satu baris (§12.1). Bukan alarm, bukan spam.
3. **Sinyal lama dibuang.** Bila kondisi masih berlaku 5 menit kemudian, bot **menghitung
   ulang** harga, spread, dan saldo lalu membuat konfirmasi baru. **Nol angka** dipakai
   ulang dari pesan lama.
4. **Tidak ada replay otomatis.** Hanya pengguna yang bisa memicu ulang (`/ulang`).
   Bot **tidak punya hak inisiatif**.

### 7.2 DCA bulanan — satu aturan, jendela lebih panjang

> **Satu aturan konfirmasi untuk semua order.** Hanya panjang jendela yang berbeda.
> Tidak ada pengecualian — kalau satu order boleh lolos tanpa konfirmasi, semua order
> punya alasan untuk lolos (*"ini rutin"*, *"ini kecil"*), dan prinsip *ragu = tidak kirim*
> hancur.

- Konfirmasi DCA dikirim **09:00 lokal**. Tombol: **Setuju / Tunda / Batal bulan ini**.
- Jendela berlaku **24 jam** (sampai 09:00 keesokan hari).
- **Pengingat tiap 6 jam** selama belum dijawab — pengingat **pasif**: memberi tahu, tidak
  meminta, tidak pernah mengubah *Tunda* jadi *Setuju*.
  Jadwal: **15.00 dan 21.00 terkirim**; pengingat berikutnya jatuh **03.00**, berada di
  dalam jendela DND (§7.3) sehingga **tidak dikirim — hanya dicatat**. Jadi efektifnya
  **2 pengingat terkirim**, dan **DND menang atas jadwal pengingat**.
- Lewat 24 jam → status `missed` (§12.2), dicatat **"DCA bulan ini dilewati"**,
  **tidak ada order**. Jadwal baru muncul bulan depan. **Tidak dikejar, tidak dihitung
  ulang dengan data lama.**

### 7.3 DND (jangan ganggu)

Jendela usulan: **23:00–07:00 lokal**. Sinyal yang muncul di dalamnya hanya **dicatat**;
pengguna melihatnya sebagai daftar tertunda keesokan pagi. Daftar itu **pasif** — tidak ada
antrean. Satu-satunya jalan jadi order tetap `/ulang`.

---

## 8. Gerbang pair: ambang spread (bukan daftar izin)

**Keputusan: angka spread yang diukur saat bot akan bertindak, dengan pemberitahuan wajib
setiap kali pair gugur.**

```python
MAX_SPREAD_BPS = 30        # 0,30% = 30 basis point
# spread_bps = (ask - bid) / mid * 10_000
```

- Diambil dari `GET /api/depth/{pair_id}` **pada saat akan bertindak** — bukan disimpan.
- `spread_bps > MAX_SPREAD_BPS` → **tidak dieksekusi otomatis**.
- **Notifikasi wajib** (§12.1), bukan opsional:
  `"{pair} tidak dieksekusi: spread {spread_bps:.0f} bps > 30 bps"`
- Gugur ≠ diusir permanen: kalau spread mengencang, pair **kembali lolos sendiri**.
  Kalau pasar panik dan **BTC** melebar melewati ambang, BTC ikut tertahan — itu memang
  yang diinginkan.
- Pemeriksaan dilakukan **dua kali**: saat menyusun konfirmasi (agar angka di pesan jujur)
  dan **saat eksekusi** (karena jendela sudah berlalu).

**Denylist permanen (opsional)** hanya untuk pengecualian tetap — stablecoin, pair
delisted. Ini **bukan daftar izin**; pasangan tetap lolos atau gugur oleh angka spread.

### 8.1 Data pengukuran (2026-10-15, satu sampel)

| pair | bid | ask | spread | vs ambang 30 bps |
|---|---|---|---|---|
| btcidr | 1.450.310.000 | 1.450.311.000 | 0,00% | lolos |
| ethidr | 42.300.000 | 42.414.000 | **27 bps** | **mepet — sisa 3 bp** |
| solidr | 1.828.000 | 1.829.795 | 10 bps | lolos |
| bchidr | 4.655.000 | 4.699.000 | **94 bps** | gugur (3× ambang) |

**Ambang belum boleh dikunci dari satu sampel** — lihat §14. ETH adalah pair yang paling
mungkin tertahan, dan ETH termasuk DCA bulanan.

> **Koreksi terhadap `SPEC_v1.md` §2.5:** spek itu menulis BCH *"butuh size jauh lebih
> kecil dari Rp 275.000"*. **Dibantah oleh recon:** kedalaman BCH menyerap **Rp 21 juta**
> di dalam +0,3% — 77× lipat order. Masalahnya **spread**, bukan ukuran. Spek itu sendiri
> menyimpulkan hal yang sama di kalimat berikutnya.

---

## 9. Mode bayangan & sakelar

> **Mode bayangan adalah review, bukan soak test.** Tujuannya bukan "lihat apakah pecah",
> melainkan "lihat apakah saya setuju". Setiap usulan yang muncul — meski tidak dieksekusi
> — adalah pertanyaan: *"Ini benar?"*

**Urutan yang mengikat:**

```
1. Kode bersih (tanpa dobel / cadangan tersentuh / order saat harga kosong)
      → PRASYARAT masuk mode bayangan
2. Aturan disetujui penuh selama 7–14 hari mode bayangan
      → SAKELAR
3. Ada satu pun yang ditolak → perbaiki aturan, RESET mode bayangan dari nol
      → BUKAN "lanjut sambil perbaiki"
```

**"Beberapa hari tanpa kecelakaan" adalah syarat masuk pintu, bukan tanda aman.**

Kode bisa 100% bersih dari dobel, cadangan, dan harga kosong sambil terus mengusulkan
hal yang tidak pernah disetujui — kodenya benar, keputusannya salah.

**Syarat sakelar:**

- 7–14 hari mode bayangan (cukup siklus pasar naik/turun).
- Setiap hari pengguna membuka **log usulan**: tidak ada yang ditolak, diubah parameternya,
  atau meminta pengecualian.
- Catatan `expired` / `rejected spread` / `DCA dilewati` **masuk akal dan tidak mengejutkan**.
- Pengguna bisa **menjelaskan mengapa setiap usulan muncul, tanpa menebak** — bahkan
  kepada orang lain.

**Selama mode bayangan, risiko eksekusi nol** — kunci View-Only, `canTrade: False`
(§6.6). Order tidak mungkin lolos secara fisik.

---

## 10. Cakupan v1 / v2 / v3

| | Isi | Pintu |
|---|---|---|
| **v1** | **DCA + take-profit + reserve buy** | bayangan → sakelar (§9) |
| **v2** | **Rebalance** (modul terpisah) | data yang v1 belum punya; **bayangan v2 sendiri**; target alokasi & band disetujui dulu |
| **v3** | Trailing / partial take-profit | parameter baru; desain simpel sengaja dipertahankan |
| **v3+** | Order history audit log | skema DB + struktur log; sesudah v1 stabil |

**Alasan rebalance ditahan:** rebalance butuh data yang v1 belum punya, dan ia adalah
satu-satunya sumber bug duplikat jual asli ("jual 25% + 25% = 50%").

**Benturan laten yang sudah ditutup oleh penundaan ini:** dengan portofolio ± 98% dalam
IDR, rebalance akan menyarankan *beli BTC + ETH + SOL* — **pair yang sama, bulan yang sama,
nominal berbeda dari DCA**. Itu pelanggaran §6.6 dalam bentuk beli, dan lebih berbahaya
karena tidak terlihat: dua pesan konfirmasi, dua nominal, keduanya terdengar benar
sendiri-sendiri.

**Opsi anti-stacking untuk v2 (belum dipilih, sengaja):**

- satu aset, satu konfirmasi per siklus (gabung DCA + rebalance jadi satu nominal); **atau**
- rebalance hanya jalan saat **tidak ada DCA** bulan itu.

### 10.1 Reserve buy — **PUTUSAN: versi A**

> **Berlaku:** pemicu **harga aset turun ≥ 15% dari harga rata-rata kita**, memakai
> sebagian cadangan. Dasar: `rules.reserve_buy_actions` (spec asli §6.4), sudah diuji.

```python
drop_pct     = 15.0   # ambang penurunan dari avg_cost
max_fraction = 0.5    # belanja ≤ 50% cadangan — setengah cadangan selalu aman
min_order    = 10000
# amount = min(target[pair] * monthly, sisa budget)
```

Sifat yang perlu dicatat builder:

- **Ukurannya dari harga rata-rata kita**, bukan dari puncak pasar. Ini penting: pemicunya
  adalah *"posisi kita turun 15%"*, bukan penilaian bahwa sekarang waktu yang tepat beli.
  Ia **menambah** pembelian, tidak pernah **menahan** DCA — sehingga tidak mengulang
  kesalahan drawdown yang sudah dihapus (§6.5).
- **Tidak ada daftar pair.** Kandidat diambil dari **posisi yang sedang dipegang** dan
  punya `basis_known` + harga. BCH gugur sendiri karena basisnya tidak diketahui; dan
  sekalipun lolos, ia tetap kena gerbang spread 94 bps (§8). **`RESERVE_PAIRS` dari
  `SPEC_v1.md` tidak diperlukan.**
- **Tidak bergantung `total_disetor`** — jadi tidak butuh pencatatan setoran dulu.
- Setiap aksi **tetap melewati** gerbang spread (§8) dan jendela konfirmasi (§6.2) seperti
  order lain. Tidak ada pengecualian.
- Total belanja seluruh aksi dibatasi `0.5 × cadangan`, dan tiap nominal tetap ≤ atap
  Rp 275.000 (§6.4).

**Versi B dari `SPEC_v1.md` §4.1 dibuang**: bergantung `total_disetor` (saat ini Rp 0,
jadi tidak akan pernah menyala), memakai daftar pair tetap, dan tidak teruji.

---

## 11. Konfirmasi API Indodax (recon 2026-10-15, baca saja)

Sumber: `btcid/indodax-official-api-docs` + pengukuran langsung dengan kunci View-Only.
**Tidak ada kunci trade dibuat. Tidak ada order dikirim.**

### 11.1 TAPI v1 **tertutup** untuk kunci ini

`getInfo` maupun `getOrderByClientOrderId` keduanya membalas:

```json
{"success": 0, "error": "Access denied for this API key version.", "error_code": "invalid_version_key"}
```

**Konsekuensi:** seluruh desain harus memakai **v2**. Istilah `getInfo` / `balance_hold`
yang ada di catatan awal **tidak berlaku** — bentuk aslinya `GET /api/v2/account`.

### 11.2 Saldo: `free` sudah bersih dari order terbuka

```
GET /api/v2/account → {canTrade, canWithdraw, accountType, uid,
                       balances: [{asset, free, locked}]}
```

- `free` = tersedia · `locked` = ditahan order terbuka.
- **`free` sudah bersih** — rumus §6.4 menyusut jadi `free − cadangan` saja.
  Bot **tidak perlu** mengurangi order terbuka sendiri (lebih aman: tidak bergantung
  pada hitungan bot).
- Contoh nyata: `canTrade: False | canWithdraw: False | uid: 18966`,
  `IDR free=203394 locked=0`, `openOrders: []`.

### 11.3 Order by client id: ada, dengan satu jebakan jujur

- Bertanya: `GET /api/v2/order?symbol=BTCIDR&origClientOrderId=<id>`.
- **ID tidak ada → `HTTP 404` — `"Order not found or not yet available."`**
  (bukan `success:0`).

> **Jebakan yang harus disandikan:** dokumen v2 menulis terbuka — *"An order may not be
> immediately available through this endpoint after successful creation."* Artinya **tepat
> setelah order sukses, query dengan ID yang sama bisa saja masih 404 walau ordernya ada.**
>
> **404 seketika ≠ order gagal.** Sesuai §6.1: **maks 3 kali bertanya, jeda [2, 5, 10] dtk,
> dengan ID yang sama.** Masih ambigu → **berhenti**, minta cek manual. Jalur alternatif
> yang disarankan dokumen: Private WebSocket untuk status real-time.

### 11.4 Izin terpisah — dan keduanya terbaca dari bursa

`view` / `trade` / `withdraw` terpisah. `withdrawCoin` tanpa izin → `"No permission"`.

`canTrade` dan `canWithdraw` adalah **nilai yang bisa dibaca setiap putaran**, bukan sekadar
pengaturan UI — dipakai sebagai pengaman keras di §6.6.

### 11.5 Endpoint & parameter

| Endpoint | Method | Parameter | Catatan |
|---|---|---|---|
| `/api/v2/order` | POST | `pair`, `type`, `btc`/`idr`, `order_type`, client order id, `timestamp`, `recvWindow` | **`recvWindow: 15000 ms`** disarankan (default 5000 ms terlalu sempit) |
| `/api/v2/order` | GET | `symbol` + `origClientOrderId` | status order; 404 = tidak ada / belum tersedia |
| `/api/v2/openOrders` | GET | `symbol`, `limit`, `timestamp`, `recvWindow` | poll **maks 1×/10 detik**, cache 10 detik; **429 → backoff 30 detik** |
| `/api/v2/account` | GET | `symbol` (opsional) | `free`, `locked`, `canTrade`, `canWithdraw` |
| `/api/depth/{pair_id}` | GET | — | kunci `buy`/`sell`, 150 jenjang, **publik**; `pair_id` **tanpa underscore** |
| `/api/pairs` | GET | — | min order, `price_precision`, flag maintenance/suspended |
| `/api/ticker/{pair}` | GET | — | dipakai `scanner.py` |
| `/api/v2/order/histories` | GET | `symbol`, `limit` | opsional, cek order lama |

**Batas:**

- Publik **180 request/menit**.
- **Trade 20 request/detik per akun per pair**; lewat → pair diblok 5 detik, **HTTP 429**.
- Cancel 30/detik. Privat umumnya 300/menit.
- **429 bukan alasan mengulang** — 429 apa pun = berhenti.

> **Belum terverifikasi:** nama parameter pada **POST** order. Recon sengaja tidak pernah
> membuat order, sehingga tidak ada yang menguji jalur tulis. Dokumen menyebut
> `newClientOrderId`, `SPEC_v1.md` menyebut `client_order_id`. **Wajib dipastikan pada
> hari 1 mode bayangan sebelum sakelar** (lihat §13).

### 11.6 Temuan 429 yang jujur

200 request publik dalam 21,8 detik (≈550/menit, **3× batas terdokumentasi**) → **nol 429**,
tidak ada 429 WAF. 429 yang pernah terlihat di dasbor berasal dari **Tokocrypto**, bukan
Indodax.

**Konsekuensi:** tanda tangan 429 Indodax belum pernah diamati, dan **tidak perlu**
dibedakan — tindakannya sama untuk keduanya: berhenti tanpa mengulang.

---

## 12. Arsitektur, notifikasi, dan persistensi

### 12.1 Diagram

```
┌─────────────┐   baca    ┌──────────────────┐   usul    ┌─────────────────┐
│ Indodax v2  │◄──────────│  Engine aturan   │──────────►│  Telegram bot   │
│ account     │           │  (rules.py ada)  │           │  konfirmasi     │
│ openOrders  │           └────────┬─────────┘           │  90 dtk / 24 j  │
│ depth       │                    │                     └────────┬────────┘
└──────┬──────┘                    ▼                              │
       │              ┌────────────────────┐◄─────────────────────┘
       │              │  Eksekutor (v1)    │  Setuju / Tunda / Batal
       │              │  1 di udara        │  /ulang
       │              │  client order id   │
       │              └─────────┬──────────┘
       │                        │ hanya setelah bursa menjawab
       ▼                        ▼
┌─────────────────────────────────────────────┐
│ portfolio.db — ditulis SETELAH status final │
│ + log usulan harian (materi review bayangan)│
└─────────────────────────────────────────────┘
```

**Proses:** satu proses jangka panjang (bukan tugas terjadwal harian), karena harus hidup
untuk mengirim konfirmasi DCA 09:00 dan menunggu jawaban dalam 90 detik.

**Ulang siklus (usulan):** setiap **5 menit** di 07.00–23.00 lokal; pemeriksaan DCA pada
09.00; snapshot harian tetap 21.00. Jauh di bawah pagu publik 180/menit.

**Portabilitas:** inti = Python murni + `portfolio.db` + `.env`. Lapisan Windows
(`run_snapshot.bat`, Task Scheduler) berada **di luar** inti dan digantikan cron di VPS.
Rincian di `docs\MIGRASI-VPS.md`.

### 12.2 Notifikasi wajib (satu baris per perubahan status)

| Pemicu | Pesan |
|---|---|
| Spread ditolak | `"{pair} tidak dieksekusi: spread {bps} bps > 30 bps"` |
| Konfirmasi reaktif kadaluarsa | `"{pair} konfirmasi kadaluarsa (90 dtk), tidak ada order yang dikirim"` |
| DCA kadaluarsa | `"DCA bulan ini dilewati (tidak dibalas dalam 24 jam)"` |
| Order terisi | `"{pair} {beli/jual} Rp{nominal} berhasil diisi, order_id: {id}"` |
| Order batal / ditolak | `"{pair} order {id} dibatalkan bursa, tidak ada posisi berubah"` |
| Selisih rekonsiliasi | `"Selisih portfolio.db vs bursa terdeteksi → trading mati, cek manual"` |
| Izin berubah | `"canWithdraw/canTrade berubah → trading mati, cek kunci API"` |
| Batas per aset tercapai | `"Pagu 3 order/hari untuk {pair} tercapai, sisa sinyal dicatat"` |
| Review mode bayangan | `"Hari ke-X mode bayangan: semua usulan diterima"` |

### 12.3 Persistensi

| Field | Tipe | Keterangan |
|---|---|---|
| `total_disetor` | float (Rp) | kumulatif, hanya bertambah tiap setoran |
| `last_dca_date` | date | bulan & tahun DCA terakhir (atau `dilewati`) |
| `v1_enabled` | boolean | `true` **hanya** setelah 7–14 hari bayangan tanpa penolakan |
| `reserve_buy_enabled` | boolean | `true` hanya setelah parameter §10.1 disetujui |
| `shadow_day` | integer | hari bayangan berjalan, reset ke 0 bila ada penolakan |
| status order | enum | `proposed → confirmed → submitting → open → filled / cancelled / expired / rejected / halted` |

> **Tidak ada `peak_equity` dan tidak ada `drawdown`** — sengaja dihapus (§6.5, §17).

`last_dca_date` memakai status **`missed`** terpisah dari `expired`, agar kadaluarsa DCA
(24 jam) tidak tercampur dengan kadaluarsa konfirmasi reaktif (90 detik) di laporan.

---

## 13. Keputusan yang diserahkan ke builder

- Bentuk tabel status order dan siapa yang boleh mengubah tiap transisi.
- Format identitas klien (mis. prefix + waktu) yang tetap ≤ 36 karakter.
- Struktur **log usulan harian** untuk review mode bayangan.
- **Validasi nama parameter POST order** (§11.5) pada hari 1 bayangan.
- Uji `recvWindow = 15000 ms` hari 1–2 bayangan: catat latency & laju 429.
- Strategi pengujian jalur eksekusi — **wajib bisa diuji tanpa mengirim order.**
- Reuse: `rules.py` (`take_profit_actions`, `reserve_buy_actions`, `max_position_ok`),
  `portfolio.py` (`fetch_price`, `sync_trades`), `alerts.py` (`send_telegram`).
- **`build_alerts` harus men-merge saran ganda per `(kind, pair)` sebelum pesan dibuat** —
  penerus dedupe `24ad5ef`.

## 14. Keputusan yang masih milik pengguna

- **Ambang spread final** — usulan **30 bps**, tetapi **belum boleh dikunci**. ETH terukur
  27 bps (sisa 3 bp) dari **satu sampel**; perlu pengukuran beberapa hari.
- Jam DND sebenarnya (usulan 23:00–07:00).
- Tanggal mulai mode bayangan dan siapa yang menandai "tidak ada penolakan" tiap hari.
- Kapan kunci **trade-only** dibuat — **setelah** sakelar, tidak sebelumnya.
- Menyimpan modal awal:
  `python portfolio_cli.py deposit 206000 2026-10-01 --note "modal awal"`
  (kontribusi masih Rp 0, sehingga laporan menampilkan semuanya sebagai "pasar").
- Opsi anti-stacking rebalance untuk v2.

## 15. Keputusan yang diambil perancang (silakan dicek)

- Jendela konfirmasi reaktif **90 detik** (di dalam rentang 60–90 yang Anda tetapkan).
- **DND menang atas jadwal pengingat DCA** → efektif 2 pengingat terkirim (15.00, 21.00).
- **Tiga pengaman global** dinyatakan eksplisit: selisih saldo, harga hilang, cadangan
  tersentuh — ketiganya mematikan trading, **tanpa** menyentuh DCA karena pemicunya sistem,
  bukan pasar.
- Heartbeat 5 menit di jam aktif untuk menjaga beban API jauh di bawah 180/menit.
- Status DCA `missed` dipisah dari `expired`.
- **Denylist permanen** dibiarkan opsional (§8) — bukan daftar izin.

---

## 16. Catatan & prasyarat sebelum sakelar

1. Kunci **View-Only tetap dipakai** seluruh mode bayangan.
2. Kunci **trade-only** baru dibuat ketika sakelar disetujui: izin **view + trade**,
   **withdraw tidak dicentang**, IP whitelist = IP berjalan.
3. `canTrade` / `canWithdraw` dipantau setiap putaran seumur hidup sistem (§6.6).
4. Prasyarat G5: **tanpa satu pun penolakan** selama 7–14 hari.
5. Angka dalam dokumen ini adalah **titik mulai, bukan hukum**. Yang tidak boleh dilonggarkan:

   > **jangan ulang kirim · jangan jual saat harga hilang · jangan sentuh cadangan ·
   > jangan jumlahkan dua aturan · jangan percaya `portfolio.db` lebih dari bursa.**

**Tidak ada yang dalam dokumen ini menghilangkan risiko atau menjanjikan untung.**
Fungsinya supaya kesalahan paling mahal — order ganda, jual dobel, harga nol, cadangan
terpakai, loop fee — **berhenti sendiri** sebelum pengguna sempat menekan Setuju.

---

## 17. Rekonsiliasi dengan `SPEC_v1.md`

### 17.1 Diadopsi (baru, tidak ada di draf sebelumnya)

- Backoff bertanya status: **maks 3×, jeda `[2, 5, 10]` detik** (menggantikan usulan lama
  "4×, jeda 5 detik").
- `MAX_SPREAD_BPS = 30` dan formula **`(ask − bid) / mid × 10_000`** — satuan basis point.
- `recvWindow: 15000 ms` untuk POST order.
- `openOrders`: poll maks 1×/10 detik, cache 10 detik, 429 → backoff 30 detik.
- Tabel **notifikasi wajib** (§12.2).
- Field persistensi: `total_disetor`, `last_dca_date`, `v1_enabled`, `reserve_buy_enabled`.
- **Assert mode `canTrade`** harus cocok dengan status sakelar (§6.6).
- Penundaan **v3**: trailing/partial take-profit, order history audit log.
- **Denylist permanen** (opsional) — bukan daftar izin.
- Pernyataan bahwa **`SPEC_v1.md` adalah single source of truth** untuk perubahan minor
  ke depan — dokumen ini menggantikannya untuk v1, dan setiap perubahan harus direview
  sebelum masuk mode bayangan.

### 17.2 Ditolak (bertentangan dengan putusan pengguna yang lebih baru)

| Isi `SPEC_v1.md` | Alasan ditolak |
|---|---|
| **Drawdown 15% dari `total_disetor` → pause 24 jam** (§2.6, §4, §7, §8; field `peak_equity` & `drawdown`) | Pengguna menghapusnya eksplisit: *"Saya mau pengaman yang tidak pause DCA saat drawdown dalam, hapus angka ini sepenuhnya."* Pengaman itu akan **menahan DCA** tepat saat pasar turun — bertentangan dengan filosofi sistem. Spek ini dibuat **19:47** dan membawa putusan lama. |
| Klaim *"BCH butuh size jauh lebih kecil dari Rp 275.000"* | **Dibantah recon**: kedalaman BCH menyerap Rp 21 juta dalam +0,3% (77×). Masalahnya spread, bukan ukuran — simpulan yang juga ditulis spek itu sendiri di kalimat berikutnya. |
| Istilah `client_order_id` / `getInfo` / `balance_hold` | TAPI v1 tertutup (`invalid_version_key`, §11.1). Nama parameter v2 wajib diverifikasi (§11.5). |

### 17.3 Perlu putusan pengguna — **SEMUA SUDAR DIJAWAB**

1. ~~**Pemicu reserve buy**~~ → **versi A** (§10.1). Versi B dibuang.
2. ~~**Pagu 3 order/hari**~~ → **per aset**, bukan global (§6.4). DCA memakai 1 per aset,
   menyisakan 2 per aset untuk sinyal reaktif pada hari yang sama.
3. ~~**`RESERVE_PAIRS`**~~ → **tidak diperlukan**; seleksi pair versi A sudah dinamis dan
   tetap melewati gerbang spread (§10.1).

> **Catatan berkas:** nama file `SPEC_v1.md - Trading Bot v1 Speci.md` tampak terpotong.
> Disarankan diganti menjadi `SPEC_v1.md` agar jelas mana yang single source of truth.
