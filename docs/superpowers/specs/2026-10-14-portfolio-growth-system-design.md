# Portfolio Growth System — Desain

- **Tanggal:** 2026-10-14
- **Status:** draf, menunggu persetujuan
- **Lokasi proyek:** `C:\Users\KakiTangan\trading-bot`

---

## 1. Maksud & Latar (kenapa ini ada)

Pengguna adalah perorangan di Indonesia dengan portofolio Indodax kecil (± Rp 208.000).
Dana ini terkait kebutuhan biaya hidup, jadi risiko harus paling minim.

Pengalaman lama pengguna: **beli kripto lalu tahan tanpa rencana keluar** — tidak pernah
tahu kapan sebaiknya ambil untung. Pengguna juga belakangan melakukan trade manual jangka
pendek (mis. beli ETH Rp 198.129 pada 10 Okt, pasang limit sell di Rp 44.850.000, terjual
Rp 201.035 pada 11 Okt) dan **memiliki staking ETH yang menambah saldo setiap hari**
(± 0,00469717 ETH).

Proyek sebelumnya mencoba **arbitrase antar-exchange** (Indodax vs Tokocrypto). Terbukti
secara struktur tidak menguntungkan: setelah biaya, seluruh 13 pair bernilai negatif saat
diukur, dan strategi itu butuh inventaris di dua exchange + kemampuan short yang tidak
tersedia di spot. Pendekatan ini **dihentikan**.

Pengguna sekarang ingin: **portofolio Indodax tumbuh lewat menabung rutin, dengan aturan
ambil untung yang jelas, risiko minimal.**

**Premis jujur yang harus dipegang:** tidak ada metode yang menjamin untung. Mesin
pertumbuhan utama adalah **setoran rutin Rp 500.000/bulan**, bukan timing trading. Kripto
bisa turun 80%+ dan bertahan lama — bukti data nyata: DOGE −85% dan LTC −82% dari puncak
2021, hingga 2026 belum pulih. Satu trade manual yang mempertaruhkan ~95% modal demi
~1,5% untung adalah contoh persis yang harus dicegah aturan sistem ini.

## 2. Tujuan (Goals)

- **G1** Dasbor portofolio Indodax: kepemilikan, **harga rata-rata** per aset, nilai sekarang,
  P&L belum/sudah terealisasi, dalam IDR.
- **G2** DCA rutin terlihat & mudah diikuti: target setoran bulanan Rp 500.000, alokasi yang
  dianjurkan, progres nyata.
- **G3** **Aturan ambil untung tertulis** per aset + peringatan saat level tercapai.
- **G4** Panduan alokasi: target vs nyata, dan aksi yang perlu dilakukan.
- **G5** **100% read-only**: tidak pernah bisa memasang, membatalkan, atau menarik order.
- **G6** Laporan pertumbuhan bulanan: pisahkan kontribusi (uang masuk) dari hasil pasar.

## 3. Non-Goals (sengaja TIDAK dilakukan)

- Eksekusi order otomatis (tidak pakai API izin Spot Trading) — minimal di v1.
- Mesin arbitrase.
- Prediksi harga / penjualan sinyal.
- Leverage / margin, kapan pun.
- Multi-exchange (hanya Indodax).
- Aplikasi mobile.

## 4. Anti-Goals (gagal walaupun "jalan")

- Fitur apa pun yang bisa menggerakkan dana atau memasang order.
- Mendorong trading lebih sering — ini sistem menabung + aturan.
- Menampilkan "peluang" yang memicu FOMO.
- Menyarankan satu posisi menghabiskan sebagian besar modal (aturan ukuran posisi wajib).
- Apa pun yang menyiratkan keuntungan terjamin.

## 5. Batasan (Constraints)

- **Modal:** ± Rp 208.000 sekarang. **Setoran bulanan target:** Rp 500.000 (beli manual).
- **Exchange:** hanya Indodax. Fee: maker 0,1% / taker 0,2%. Min order Rp 10.000.
- **API:** `indodax_api.py` yang sudah ada (read-only, auto-detect TAPI v1/v2),
  kredensial `INDODAX_KEY`/`INDODAX_SECRET` di `.env` (izin View Only).
- **Lingkungan:** Windows, Python, dasbor Streamlit yang sudah ada.
- **Pengguna non-ahli:** UI berbahasa Indonesia, bahasa sederhana.
- **Akses:** tetap bisa dibuka lewat Tailscale.
- **Keterbatasan API:** tidak ada endpoint riwayat setoran/transfer; riwayat trade mungkin
  terpotong (v2 `limit=100`, tanpa paginasi). Karena itu setoran dicatat manual.

## 6. Alokasi target & cara pengelolaannya

| Porsi | Aset |
|---|---|
| 55% | BTC |
| 25% | ETH |
| 10% | SOL |
| 10% | Cadangan IDR |

Setoran Rp 500.000/bulan → BTC Rp 275.000, ETH Rp 125.000, SOL Rp 50.000, cadangan Rp 50.000.

**Cara pengelolaan target: "Aliran dulu, pita kedua, cadangan dikunci."**

1. **Alokasi target berlaku untuk SETORAN (aliran), bukan total portofolio sekarang.**
   Tiap bulan setoran dibagi sesuai bobot, dengan penekanan ke aset yang paling jauh di
   bawah target. **Tidak ada aset yang dijual hanya untuk mencapai angka persentase.**
2. **Saldo sekarang dibiarkan** — tidak dibelanjakan sekaligus; DCA yang melanjutkan.
3. **Pita rebalance:** pertimbangkan jual hanya bila satu aset menyimpang **>10 poin
   persentase** dari target **dan** bertepatan dengan momen take-profit.
4. **Cadangan IDR dikunci:** hanya untuk darurat + beli saat jatuh (aturan tertulis, mis.
   aset −15% dari harga rata-rata). **Tidak** boleh dihabiskan oleh rebalance.
5. **Reward staking** dibiarkan auto-compound; dicatat sebagai penambahan kuantitas
   tanpa biaya (menurunkan harga rata-rata efektif). Tidak dijual oleh aturan.

## 7. Aturan yang disandikan

**A. DCA (setoran rutin) — dilacak keduanya**
- Catat **setoran IDR** (manual) DAN **pembelian koin** (dari API/manual).
- Tampilkan selisih target bulanan vs setoran nyata.
- Pembelian manual untuk semua aset (termasuk SOL; auto-invest Indodax tidak dipakai).

**B. Ambil untung (berbasis harga rata-rata)**
- +50% dari rata-rata → sarankan jual 25% posisi aset itu.
- +100% → sarankan jual 25% lagi.
- Sisanya ditahan. Aturan ditentukan sebelum beli; sistem yang mengingatkan.
- Pemicu dievaluasi **neto setelah fee taker 0,2%**, memakai **harga bid** Indodax
  (harga yang benar-benar diterima saat menjual).

**C. Ukuran posisi (pengaman baru)**
- Tidak ada trade yang boleh memakai >25% modal dalam satu aset (kecuali setoran DCA).
- Semua saran aksi tunduk pada minimum order Rp 10.000 dan ambang fee.

**D. Pengaman**
- Tidak pernah menyarankan jual saat rugi hanya demi rebalance.
- Cadangan IDR tidak disentuh rebalance.

## 8. Arsitektur / bagian-bagian

- **Lapisan data — `portfolio.py`**
  Saldo via `indodax_api.account_info()`; harga via ticker publik Indodax; riwayat trade via
  `my_trades` (v1/v2) **digabung** dengan `dca.db` + entri manual → hitung harga rata-rata,
  P&L terealisasi, dan kuantitas. Simpan ke **satu** SQLite: `portfolio.db` (`dca.db`
  diimpor sekali). Tangani saldo staking terpisah dari saldo spot bila API memisahkannya.
- **Mesin aturan — `rules.py`**
  Input: kepemilikan + target + aturan → daftar aksi (BELI/JUAL/TAHAN) dengan jumlah persis
  & panduan harga. Logika murni, mudah diuji.
- **Notifikasi — `alerts.py`**
  Evaluasi aturan tiap refresh; tampilkan di dasbor + opsional Telegram.
- **Dasbor — tab baru, mandiri**
  Tab portofolio **mengambil data Indodax sendiri**; tidak bergantung pada BackgroundUpdater
  arbitrase maupun endpoint Tokocrypto.
- **Laporan — laporan bulanan**
  Kontribusi (uang masuk) dipisahkan dari hasil pasar. Snapshot harian **mulai dicatat
  sekarang** (tanpa backfill di v1).

## 9. Keputusan yang diserahkan ke builder

- Skema SQLite persisnya.
- Pilihan pustaka grafik.
- Detail setup bot Telegram.
- Interval refresh / cache.
- Cara menelusuri apakah API mengekspos saldo staking; fallback = entri manual.

## 10. Keputusan yang masih milik pengguna

- Konfirmasi ambang take-profit (+50% / +100%, jual 25% tiap tahap).
- Mau notifikasi Telegram sekarang atau nanti.
- Kode arbitrase lama: disimpan atau diarsipkan.
- Perilaku saat pasar turun dalam: berhenti beli, atau justru tambah beli.

## 11. Keputusan yang diambil perancang (silakan dicek)

1. **Harga & fee:** valuasi & pemicu take-profit memakai **harga bid** Indodax, dievaluasi
   **neto setelah fee taker 0,2%**.
2. **Minimum order:** saran aksi di bawah Rp 10.000 atau di bawah ambang fee akan
   disembunyikan/dibulatkan, disertai penjelasan.
3. **Satu database:** konsolidasi ke `portfolio.db`; `dca.db` diimpor sekali.
4. **Tab portofolio mandiri:** tidak bergantung pada mesin arbitrase/Tokocrypto.
5. **Laporan v1 forward-only:** snapshot harian mulai sekarang, tanpa backfill.
6. **Reward staking:** dicatat sebagai kuantitas tanpa biaya, tidak dijual.
7. **Debu BCH/ETH:** dibiarkan, ditampilkan sebagai kepemilikan, dikecualikan dari rebalance.
8. **Harga rata-rata tanpa dasar:** aset tanpa riwayat beli (mis. debu BCH) ditandai
   "harga rata-rata tidak diketahui" dan dikecualikan dari take-profit sampai dasar diketahui.
9. **Riwayat jual diserap** dari API untuk menghitung P&L terealisasi dan menjaga dasar biaya.

## 12. Migrasi & catatan proyek lama

- Kode arbitrase (`scanner.py`, `strategy_mean_reversion.py`, `paper_trader.py`, dasbor lama)
  tetap ada; sistem baru hidup berdampingan.
- Bug terkonfirmasi: `scanner.py` memakai `FEE_INDO = 0.003` (kedaluwarsa; fee nyata 0,2%).
- Tidak ada repositori git di folder ini.
