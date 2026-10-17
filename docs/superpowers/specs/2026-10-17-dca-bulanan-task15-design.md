# Desain: DCA Bulanan (Task 15) — Addendum Spesifikasi

Tanggal: 2026-10-17
Cabang: `feat/semi-auto-execution`
Status: lolos pemeriksaan builder; pertanyaan pengguna sudah dijawab (2026-10-17)

## Konteks

Spesifikasi utama (2026-10-15) memuat DCA bulanan sebagai salah satu dari tiga
aturan v1 (spec §6.4), dan Task 13 sudah membangun seluruh mesinnya: jendela
DCA 24 jam, pengingat pasif, DND, penanda bulan (`bot_state`), gerbang
`mark_confirmed`/`mark_batal`/`mark_missed`. Namun dua lubang tersisa setelah
Task 14 (ledger progress.md):

- **F45** — `rules.py` tidak punya sumber aksi DCA. `engine.collect_actions`
  (`engine.py:110`) hanya memanggil `take_profit_actions` dan
  `reserve_buy_actions`. `mark_pending` tidak pernah dipanggil dari produksi.
  Akibatnya: jendela tidak pernah terbuka, pengingat tak pernah menyala, DCA
  tak pernah diusulkan.
- **F46** — `main_loop._jawab` memanggil `mark_confirmed` untuk **setiap**
  penekanan Setuju, padahal tombol itu dipakai bersama take-profit, reserve
  buy, dan DCA. Hari ini no-op (F45), tetapi begitu `mark_pending` ada,
  menyetujui take-profit akan menutup jendela DCA.

## Intent

Agar bot benar-benar mengeksekusi DCA bulanan sesuai spesifikasi, dengan
angka-angka yang diputuskan pengguna pada 2026-10-17, tanpa mengubah perilaku
take-profit, reserve buy, atau gerbang keamanan mana pun.

## Angka yang diputuskan pengguna (2026-10-17)

| Keputusan | Nilai |
|---|---|
| Jumlah DCA per bulan | Rp 500.000 (= `MONTHLY_IDR` yang sudah ada di repo) |
| Bobot | `DEFAULT_TARGET` = BTC 55% / ETH 25% / SOL 10% / IDR 10% — **dipakai apa adanya**, tidak mengarang bobot baru |
| Sifat pembagian | Ikut `dca_split` yang sudah ada: miring otomatis ke aset yang sedang di bawah target, dihitung ulang dari portofolio tiap bulan |
| Batas per order | Rp 275.000; kelebihan dari bagian aset dan alokasi di bawah Rp 10.000 diamkan sebagai cadangan IDR |
| Keterlambatan | Jendela24 jam tak terjawab = bulan itu **dilewati**, bulan berikutnya jalan normal |
| Gerak harga (drift) | **Tetap 0,5%** untuk DCA juga — tidak dilebarkan. Pengguna memilih sadar, dua kali, setelah akibatnya dijelaskan |
| Percobaan | **Sekali** di tanggal 1 pukul 09.00. Gagal diusulkan (spread, saldo, gerbang) = bulan dilewati, tanpa percobaan ulang |
| Order gagal setelah Setuju | Bulan dihitung **sudah selesai** — tidak ditawarkan ulang |
| Sumber dana | Setoran bulanan pengguna; **reserve buy tidak menambah pengeluaran bulanan** — hanya boleh membeli dari sisa cadangan IDR yang memang ada di saldo |

Keputusan terakhir menyepakati desain `reserve_buy_actions` yang sudah ada
(ia membaca `idr_reserve` = saldo IDR − cadangan wajib); tugas desain ini
hanya memastikan DCA tidak menguras cadangan itu sebelum reserve buy sempat
memakainya.

## Goals

1. Sumber aksi DCA di `rules.py` dengan provenance terdaftar (F32): nama
   aturan keluar dari `collect_actions`, `engine.rule_name` dapat
   menunjuknya, tidak ada `ValueError`.
2. `mark_pending` dipanggil **tepat sekali** saat proposal DCA benar-benar
   terkirim — tidak lebih awal (jendela bohong), tidak dua kali
   (`IntegrityError`).
3. `mark_confirmed` hanya untuk tombol Setuju pada **proposal DCA** (F46).
4. Angka DCA tidak ada yang dikarang: jumlah dari `MONTHLY_IDR`, bobot dari
   `DEFAULT_TARGET`, pembagian dari `dca_split`.
5. Batas Rp 275.000 per order dipotong di **usulan** (sebelum gerbang
   `amount_ok` menolak), sehingga penolakan tidak menghitung mundur
   `shadow_day` — menahan go-live tanpa perlu.

## Non-goals (anti-goals)

- **Tidak** mengubah bobot `DEFAULT_TARGET`, tidak mengganti `dca_split`.
- **Tidak** mengubah take-profit, reserve buy, `rebalance_actions` (tetap
  tidak dipanggil v1), atau `MAX_SPREAD_BPS`.
- **Tidak** menambah limit, drawdown harian, atau daftar pair — semuanya sudah
  dihapus dari desain.
- **Tidak** memanggil `mark_pending` untuk proposal non-DCA, dan **tidak**
  memanggil `mark_missed` manual.
- **Tidak** membuat tabel, kolom, atau pembuat state kedua (R-G).
- **Tidak** menyentuh kunci dagang, penarikan, atau penempatan order.

## Bagian "how" yang sudah dikunci (kabel dari Task 11–14)

- **F32**: aksi DCA didaftarkan di `collect_actions` dengan provenance
  eksplisit — satu-satunya cara `rule_name` tidak melempar `ValueError`
  (yang wajib diperlakukan sebagai halt senyap).
- **Urutan `mark_pending`**: harus setelah usulan DCA benar-benar terkirim
  ke Telegram (pesan konfirmasi terkirim), bukan sekadar proposal
dibuat — kalau gerbang menolak usulan itu sebelum terkirim, tidak ada
  jendela yang layak dibuka.
- **Kirim tanggal 1 pukul 09.00** (kabel Task 13 #6): `dca_state`
  mengembalikan `("none", 0.0)` pada ketidakcocokan bulan **sebelum**
  aritmetika jendela — jendela yang melintasi batas bulan tidak bisa tercatat.
- **`mark_confirmed` bergerbang** (T-13.3) pada baris audit
  `awaiting` — gerbang inilah yang membuat F46 no-op hari ini, dan tetap
  menjadi jaring pengaman terakhir.
- **Pengingat**: `should_remind` True sepanjang satu jam — dedupe tugas
  pemanggil (maksimal satu pesan per slot), sudah dipatok di Task 14.
- **Jangan menulis catatan `dca_missed` sendiri** — `dca_window` menjaganya.

## Hasil pemeriksaan builder (2026-10-17)

Pemeriksaan builder membaca dokumen ini terhadap kode dan menemukan enam
titik. Tiga di antaranya hanya pengguna yang boleh memutuskan — sudah
dijawab di atas (drift 0,5%; sekali lalu lewat; order gagal = bulan
selesai). Tiga sisanya di bawah ini adalah **keputusan builder** (ditandai
supaya pengguna bisa memeriksanya):

1. **Jendela proposal DCA ≠ 90 detik.** `engine.tick` memaksa
   `window_s=proposal.REACTIVE_WINDOW_S` (90 dtk) untuk semua usulan
   (`engine.py:307`), dan `on_answer` menolak yang lewat waktu
   (`is_expired`). Usulan DCA harus memakai jendela 24 jam — kalau tidak,
   satu-satunya Setuju yang diterima adalah yang ditekan dalam 90 detik
   setelah jam 09.00. Keputusan builder: beri cabang jendela untuk aksi
   DCA (konstanta `DCA_WINDOW_S = 86400` di `proposal.py`, dipilih di
   `engine.tick` berdasarkan nama aturan aksi).
   **Drift 0,5% tetap berlaku penuh** (permintaan pengguna) —
   `price_still_valid` tidak diubah sama sekali.
2. **Benturan pasangan dengan reserve buy.** `actions_merge` menolak dua
   aksi berbeda yang mengklaim `(kind, pair)` yang sama — melempar
   `ValueError` yang men-halt seluruh tick termasuk take-profit. Kalau DCA
   dan reserve buy sama-sama mengusulkan `BUY btc_idr` di tick yang sama,
   bot berhenti diam-diam. Keputusan builder: **DCA mengklaim lebih dulu**;
   kaki reserve buy untuk pasangan yang sudah diklaim DCA **dilewati pada
   tick itu** (terjadi di `collect_actions`, `reserve_buy_actions` tidak
   disentuh). Ini konsisten dengan keputusan pengguna bahwa reserve buy
   hanya membeli dari cadangan yang tersisa.
3. **Bot offline tepat di jam 09.00 tanggal 1.** Keputusan builder: bot
   tetap mengusulkan begitu hidup lagi, **selama jendela 24 jam-nya masih
   berakhir di bulan yang sama** (kalau tidak, bulan dilewati — konsisten
   dengan kabel Task 13 bahwa jendela yang melintasi batas bulan tidak
   tercatat). Percobaan tetap satu kali per bulan.

## Mekanisme yang diunci (untuk builder)

- **Nama**: `RULE_DCA = "dca_bulanan"` (baru, tidak bentrok dengan
  `take_profit`/`reserve_buy`).
- **Pemicu `mark_pending`**: ketika usulan DCA **benar-benar dikirim** ke
  Telegram — bukan saat aksi dibuat. Karena `collect_actions(ctx)` tidak
  menerima `conn`, pemanggilan `mark_pending` terjadi di lapisan
  `tick_cycle`/`main_loop` setelah `send_proposals` sukses, dengan
  `conn` dan `month` yang sama dengan yang dipakai menyusun aksi.
- **Pemotongan Rp 275.000**: saat menyusun aksi DCA di `rules.py`, sebelum
  `amount_idr` diisi — gunakan konstanta batas yang sudah ada
  (`safeguards.MAX_ORDER_IDR` atau setara), jangan angka baru. Sisa yang
  terpotong tidak dibagikan ulang; ia tetap sebagai cadangan IDR dan
  dapat diambil reserve buy (sesuai keputusan pengguna).
- **Alokasi tidak terpakai**: jika `dca_split` menghasilkan alokasi aset
  < Rp 10.000, diamkan sebagai cadangan IDR — jangan dibagi ulang, jangan
  dibulatkan ke nol secara diam-diam.
- **Urutan `collect_actions`**: `take_profit` tetap paling depan (kontrak
  merge), DCA di urutan yang membuat reserve buy bisa dilewati per-pasangan
  (keputusan #2 di atas).
- **`mark_confirmed` hanya untuk proposal DCA** (F46): `main_loop._jawab`
  memeriksa `rule_name` usulan yang dijawab sebelum memanggil
  `mark_confirmed`. Saran: sama untuk `mark_batal` — tombol "Batal bulan
  ini" pada proposal take-profit tidak boleh menyentuh jendela DCA.
- **Jangan menulis catatan `dca_missed` sendiri** — `dca_window` menjaganya.

## Constraints (tetap berlaku dari rencana induk)

- Tidak ada jaringan dari tes mana pun (guard `JaringanDibuka`).
- Tidak ada kunci dagang, penarikan, atau penempatan order di tugas ini.
- Order hanya `limit`; `recvWindow 15000`; query status ≤3× `[2,5,10]`.
- `canWithdraw == True` → halt. `ValueError` dari `rule_name` → halt senyap.
- `portfolio.db` hanya ditulis setelah bursa menjawab status akhir.
- 46 tes asli tak boleh tersentuh; baseline 181 diukur ulang, bukan dihafal.
- `time.localtime` saja. Bahasa Indonesia, bahasa sehari-hari.
- Tes mutasi bukti wajib; kegagalan TDD harus benar-benar merah dulu.
