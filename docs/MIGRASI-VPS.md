# Migrasi ke VPS — Runbook

Dokumen ini berisi tindakan yang **harus** dikerjakan saat sistem pindah dari komputer
Windows di rumah ke VPS. Urutannya penting. Jangan loncati tahap.

Prinsip yang menentukan seluruh dokumen ini:

> **Jangan matikan mesin lama sampai VPS terbukti jalan penuh selama beberapa hari.**

Mesin lama yang menyala tidak merusak apa pun. VPS yang setengah jalan bisa membuat
DCA terlewat diam-diam atau laporan bulanan salah hitung.

---

## Mengapa dokumen ini ada

Tiga kegagalan yang paling mahal, dan semuanya bisa dihindari dengan checklist di bawah:

| Kegalahan | Akibat | Apakah aman? |
|---|---|---|
| IP whitelist kunci API tidak diganti | Bot berhenti total, semua permintaan ditolak | **Aman** — berarti tidak ada order |
| Zona waktu VPS dibiarkan UTC | Batas harian & bulanan bergeser 7 jam; laporan bulanan salah kelompok | **Berbahaya** — angka terlihat benar padahal salah |
| Dua proses berebut Telegram | Pesan konfirmasi hilang atau terpotong | **Berbahaya** — konfirmasi tidak sampai ke Anda |

---

## Tahap 0 — Uji dulu, jangan pindah apa pun

Kerjakan **di VPS**, sambil mesin lama tetap jalan seperti biasa. Tahap ini gagal =
migrasi batal, bukan dilanjutkan dengan nekat.

### 0.1 Koneksi ke Indodax dari IP VPS

Ini kemungkinan besar titik gagal pertama. WAF Indodax kadang memblokir IP datacenter.
Uji dari VPS:

```bash
curl -s -A "Mozilla/5.0" "https://indodax.com/api/ticker/btcidr"
```

Harus membalas JSON berisi `ticker`. Kalau balasannya HTML, kosong, atau 403 —
**berhenti di sini.** Migrasi ke VPS tidak mungkin dilakukan dengan Indodax dari IP itu.

Lanjutkan dengan uji baca-only memakai kunci yang sudah ada (View-Only):

```bash
cd /path/trading-bot
python -c "import indodax_api as A; print(A.account_info())"
```

Harus membalas `canTrade: False`, `canWithdraw: False`, dan daftar saldo.

### 0.2 Versi Python dan seluruh tes

```bash
python --version        # mesin lama: 3.14
python -m pytest -q     # harus 46 tes lulus
```

Kalau versi Python berbeda, jalankan tes dulu sebelum percaya apa pun.

### 0.3 Telegram

Kirim satu pesan percobaan dari VPS memakai `TELEGRAM_TOKEN` dan `TELEGRAM_CHAT_ID`
yang sama. Pesan harus sampai.

> **Catatan tentang `getUpdates`.** Hari ini sistem hanya **mengirim** pesan
> (`sendMessage`), tidak pernah membaca. Jadi belum ada bentrok. Tetapi begitu bot
> semi-otomatis ditambahkan dan mulai mempolling `getUpdates`, **hanya satu proses di
> seluruh dunia yang boleh melakukan itu.** Dua pemanggil = pesan konfirmasi Anda
> berebut dan bisa hilang. Lihat Tahap 2.2.

### 0.4 Akses ke dashboard

Pastikan Anda bisa membuka VPS dari jaringan Anda sendiri — Tailscale, atau IP publik
dengan firewall yang benar. Dashboard Streamlit sekarang ada di
`http://100.99.49.65:8501` (Tailscale di mesin rumah). Alamat itu **akan berubah**.

---

## Tahap 1 — Pindahkan data (mesin lama tetap jalan)

### 1.1 Wajib dibawa

| Berkas | Cara pindah | Catatan |
|---|---|---|
| `portfolio.db` | Salin manual | **Satu-satunya sumber kebenaran** untuk kontribusi setoran dan harga rata-rata koin. Tanpa ini, seluruh riwayang lenyap dan semua posisi jadi "hasil pasar". |
| `.env` | Salin tempel, hati-hati | Berisi `INDODAX_KEY`, `INDODAX_SECRET`, `TELEGRAM_TOKEN`, `TELEGRAM_CHAT_ID`. **Jangan lewat git. Jangan lewat chat. Jangan lewat email.** |
| Seluruh kode `.py` | Lewat git | Repo bersih di `ce0047f`. Pastikan commit yang sama dipakai di VPS. |

### 1.2 Boleh ditinggal

- `dashboard_shared_state.json` — state runtime, sengaja tidak di-tracker.
- `dca.db` — sudah dibaca sekali lewat `migrate_dca.py`. Biarkan sebagai arsip di
  mesin lama.
- `scheduler.log` — log lama, tidak ada gunanya di VPS.
- `run_snapshot.bat` dan Task Scheduler — ini milik Windows. Di VPS digantikan cron.
  Lihat Tahap 2.4.

### 1.3 Pastikan `.env` benar-benar tidak ikut git

```bash
git status --porcelain   # harus kosong
git check-ignore -v .env # harus menunjukkan .gitignore
```

Kalau `.env` ternyata pernah ter-commit, buang riwayatnya **sebelum** menyalin repo ke
mana pun, lalu putar semua kunci di panel Indodax dan bot Telegram.

---

## Tahap 2 — Tindakan kritis

### 2.1 Ganti IP whitelist kunci API

Saat pindah ke VPS, kunci API yang trade-only nanti harus dikunci ke **IP VPS**, bukan
IP rumah.

- Kalau lupa: semua permintaan ditolak, bot mati. **Ini aman** — berarti tidak ada
  order yang bisa keluar.
- Tetapi juga berarti DCA bulan itu bisa terlewat tanpa Anda sadari. Jadi catat
  tanggalnya, dan setelah whitelist diganti, jalankan verifikasi Tahap 3 segera.

Kunci View-Only yang sekarang tidak perlu whitelist, jadi pengujian Tahap 0 tidak
terhalang.

### 2.2 Hentikan proses lama sebelum VPS mulai

Urutan yang benar:

1. Matikan bot semi-otomatis / proses polling Telegram di **mesin lama**.
2. Pastikan tidak ada yang berjalan: `getUpdates` hanya boleh dipanggil satu proses.
3. **Baru** kemudian nyalakan proses di VPS.

Kebalikannya (VPS menyala duluan) membuat dua proses berebut `getUpdates`, dan pesan
konfirmasi Anda bisa hilang — persis pesan yang tidak boleh hilang.

Kalau ragu apakah ada proses lama yang masih hidup, matikan mesin lama dulu selama
beberapa jam. Kalau VPS jalan normal, berarti sudah bersih.

### 2.3 Setel zona waktu VPS ke WIB

**Ini yang paling mudah disepelekan dan paling sulit dikenali.**

Kode sistem memakai waktu lokal mesin:

- `portfolio.py:223` — `time.strftime("%Y-%m-%d", time.localtime(ts))`
  → menentukan apakah snapshot hari ini sudah diambil (aturan: satu snapshot per hari).
- `growth_report.py:13` — `time.strftime("%Y-%m", time.localtime(ts))`
  → menentukan **bulan** mana sebuah snapshot atau setoran masuk.

VPS Linux hampir selalu UTC. Dibiarkan begitu saja, semua tanggal bergeser 7 jam ke
belakang. Yang paling berbahaya: snapshot yang diambil pukul 01:00 WIB tanggal 1
akan masuk ke **bulan sebelumnya**, dan laporan bulanan jadi salah kelompok tanpa
peringatan apa pun.

```bash
sudo timedatectl set-timezone Asia/Jakarta
timedatectl        # Local time harus menunjukkan WIB
```

Untuk cron, setel juga secara eksplisit agar tidak bergantung pada sistem:

```cron
TZ=Asia/Jakarta
```

Verifikasi setelah diubah: jalankan snapshot, lalu cek kolom `ts` di `portfolio.db`
harus jatuh pada tanggal WIB hari ini, bukan kemarin.

### 2.4 Gantikan Task Scheduler dengan cron

Task Scheduler Windows "Portofolio Snapshot" (harian 21:00) tidak berpindah. Di VPS:

```cron
0 21 * * * cd /path/trading-bot && /usr/bin/python3 scheduler.py --telegram >> scheduler.log 2>&1
```

Perhatikan `cd` — tanpa itu, `portfolio.db` tidak ditemukan dan database baru kosong
akan dibuat diam-diam. `run_snapshot.bat` di Windows melakukan hal yang sama.

---

## Tahap 3 — Verifikasi di VPS

Jalankan **semua** ini, jangan sebagian.

- [ ] `python -m pytest -q` → 46 tes lulus.
- [ ] `timedatectl` → `Asia/Jakarta`.
- [ ] `python -c "import indodax_api as A; print(A.account_info())"` →
      `canWithdraw: False`, saldo IDR sesuai kenyataan (sekitar Rp 203.394 saat
      dokumen ini ditulis).
- [ ] `python portfolio_cli.py deposit` atau perintah baca yang menampilkan kontribusi
      → **kontribusi tidak nol**. Kalau nol, `portfolio.db` yang terbawa adalah salinan
      kosong, bukan yang asli.
- [ ] `python scheduler.py --telegram` → pesan sampai ke Telegram **tepat satu kali**.
- [ ] Snapshot tidak menduplikasi: jalankan dua kali berturut-turut, jumlah baris di
      tabel snapshot tidak boleh bertambah pada putaran kedua.
- [ ] Dashboard terbuka dari perangkat Anda.

---

## Tahap 4 — Baru kemudian matikan mesin lama

Setelah seluruh checklist Tahap 3 lulus **dan** sistem berjalan di VPS selama
minimal beberapa hari tanpa kejanggalan:

1. Matikan Task Scheduler "Portofolio Snapshot" di mesin lama.
2. Pastikan tidak ada proses Python yang masih berjalan di mesin lama.
3. Biarkan mesin lama menyimpan salinan `portfolio.db` sebagai cadangan selama
   beberapa minggu. Jangan dihapus dulu.

---

## Kalau terjadi masalah

### Bot berhenti total semua permintaan ditolak
IP whitelist tidak cocok dengan IP VPS. Aman — tidak ada order yang bisa keluar.
Ganti whitelist ke IP VPS yang benar, lalu jalankan kembali Tahap 3.

### Laporan bulanan tiba-tiba aneh
Zona waktu. Cek `timedatectl`. Snapshot yang sudah terlanjur masuk ke bulan salah tidak
perlu dihapus — catat saja sebagai catatan kaki, dan pastikan mulai bulan berikutnya
sudah benar.

### DCA bulan ini terlewat
Kalau penyebabnya pemadaman listrik atau VPS mati, catat "DCA bulan ini dilewati".
Jangan dikejar, jangan dihitung ulang dengan data lama. Jadwal baru muncul bulan depan.

### Ingin kembali ke mesin lama
Salin `portfolio.db` terbaru dari VPS ke mesin lama, nyalakan kembali Task Scheduler,
dan pastikan proses di VPS sudah dimatikan lebih dulu (lihat Tahap 2.2 — jangan sampai
dua proses hidup bersamaan).

---

## Checklist singkat

Salin-tempel untuk dipakai saat migrasi benar-benar dilakukan.

```
TAHAP 0 — uji di VPS, mesin lama tetap jalan
  [ ] curl ticker Indodax dari IP VPS berhasil (bukan HTML/403)
  [ ] account_info() dari VPS berhasil, canWithdraw: False
  [ ] pytest 46 lulus
  [ ] Telegram terkirim dari VPS
  [ ] dashboard bisa dibuka dari perangkat saya

TAHAP 1 — pindahkan data
  [ ] portfolio.db disalin (cek: kontribusi tidak nol)
  [ ] .env disalin manual, tidak lewat git/chat
  [ ] git check-ignore -v .env menunjukkan ter-ignore
  [ ] commit repo sama dengan mesin lama

TAHAP 2 — tindakan kritis
  [ ] timezone VPS = Asia/Jakarta, dicek timedatectl
  [ ] cron snapshot 21:00 WIB dibuat, dengan cd ke direktori proyek
  [ ] proses Telegram lama dimatikan SEBELUM proses VPS nyala
  [ ] IP whitelist kunci API (saat ada kunci trade) = IP VPS

TAHAP 3 — verifikasi
  [ ] semua butir verifikasi di atas lulus
  [ ] snapshot tidak duplikat saat dijalankan dua kali

TAHAP 4 — tutup
  [ ] berjalan normal beberapa hari di VPS
  [ ] Task Scheduler mesin lama dimatikan
  [ ] salinan portfolio.db lama disimpan sebagai cadangan
```
