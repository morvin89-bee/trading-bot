# Runbook: mematikan sakelar eksekusi & membersihkan metadata pair

Dokumen ini ditulis 2026-11-12 atas permintaan pemilik bot, setelah
`python -m preflight --target bayangan` di mesin ini melaporkan:

```
[GAGAL] metadata pair: basi/hilang: bch_idr, btc_idr, eth_idr, sol_idr (batas 86400 detik)
[GAGAL] izin kunci: Berhenti: sakelar aktif tetapi kunci tidak bisa trading (canTrade) — ...
[GAGAL] mode: sakelar eksekusi NYALA (v1_enabled=1) — bukan target bayangan; matikan dulu
```

Tanda tiap langkah:

- `[OPENCODE-AMAN]` — hanya membaca. Agent boleh menjalankan, Anda boleh
  melewatinya.
- `[SAYA-JALANKAN]` — mengubah keadaan (proses, task, DB). **Hanya Anda.**
  Agent tidak menjalankan satu pun langkah ini.

Jalankan semua perintah PowerShell dari `C:\Users\KakiTangan\trading-bot`.
Perintah yang butuh hak admin ditandai **(PowerShell Administrator)**.

Python yang dipakai bot dan dashboard:
`C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\python.exe`

---

## a. Pemeriksaan awal

### a1. `[OPENCODE-AMAN]` Siapa yang berjalan

```powershell
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' or Name='python.exe' or Name='wscript.exe'" |
  Select-Object ProcessId, ParentProcessId, Name, CreationDate | Format-Table -AutoSize
(Get-CimInstance Win32_OperatingSystem).LastBootUpTime
Get-ChildItem *.py | Sort-Object LastWriteTime -Descending |
  Select-Object -First 8 Name, LastWriteTime | Format-Table -AutoSize
```

Yang dicari:

- satu `pythonw.exe` tanpa jendela = bot; dashboard = `python.exe` yang
  memegang port 8501 (`Get-NetTCPConnection -LocalPort 8501`). `python.exe`
  lain bisa milik alat lain (mis. editor/agent) — jangan dimatikan;
- `CreationDate` proses bot **lebih baru** dari `LastWriteTime` semua `.py`
  → proses memakai kode HEAD sekarang, bukan kode lama. Bila `CreationDate`
  lebih tua, prosesnya memuat kode lama: tetap ikuti langkah b–c;
- rantai induk: `svchost.exe` → `wscript.exe` → `cmd.exe` → `pythonw.exe`.
  Itu artinya bot dihidupkan Task Scheduler lewat `run_v1_hidden.vbs`.

`ExecutablePath`/`CommandLine` kosong = akses ditolak. Itu **bukan** tanda
proses rusak; ulangi perintah di **(PowerShell Administrator)** bila Anda
ingin melihat jalur skrip persisnya.

### a2. `[SAYA-JALANKAN]` Isi `bot_state` dan 10 usulan terakhir (baca-saja)

Koneksi dibuka `mode=ro`, jadi mustahil menulis walau salah ketik.

```powershell
$py = "C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\python.exe"

& $py -c 'import sqlite3; c = sqlite3.connect("file:portfolio.db?mode=ro", uri=True); print(c.execute("SELECT id, last_dca_date, v1_enabled, shadow_day, dca_sent_ts, dca_status, shadow_checked_day, telegram_offset FROM bot_state").fetchall())'

& $py -c 'import sqlite3; c = sqlite3.connect("file:portfolio.db?mode=ro", uri=True); [print(r) for r in c.execute("SELECT ts, day, kind, pair, amount_idr, outcome, note, client_order_id FROM proposals ORDER BY ts DESC LIMIT 10")]'
```

Yang dicari:

- `v1_enabled = 1` → sakelar nyala (penyebab NO-GO butir "mode");
- `shadow_day` → hari bayangan yang dihitung;
- `last_dca_date` / `dca_status` → apakah jendela DCA bulan ini sedang
  `awaiting`/`confirmed`/`missed`. Jangan tutup jendela yang masih
  menunggu jawaban;
- `proposals` dengan `outcome` kosong/NULL dan `ts` baru = usulan yang masih
  menunggu jawaban Telegram. Usulan ini **tidak** dibatalkan oleh langkah e.

---

## b. Menonaktifkan pemicu otomatis

Bot dihidupkan task `IndodaxV1Shadow` (trigger: boot +1 mnt, Event 41
Kernel-Power +1 mnt, logon +30 dtk; S4U; `MultipleInstancesPolicy IgnoreNew`;
`RestartOnFailure` 3×/1 mnt). Skrip `run_v1_hidden.vbs` menambah ulangan
sendiri: 3× dengan jeda 60 detik. **Mematikan proses saja tidak cukup** —
tanpa langkah ini bot hidup lagi dalam hitungan menit.

### b1. `[SAYA-JALANKAN]` Nonaktifkan task bot (tanpa menghapus apa pun)

```powershell
# (PowerShell Administrator)
Disable-ScheduledTask -TaskName "IndodaxV1Shadow" | Select-Object TaskName, State
Get-ScheduledTask -TaskName "IndodaxV1Shadow" | Select-Object TaskName, State
```

Harapan: `State = Disabled`. Task, XML, dan skrip tetap utuh — bisa
dinyalakan lagi dengan `Enable-ScheduledTask`.

### b2. `[SAYA-JALANKAN]` (opsional) Nonaktifkan dashboard juga

Dashboard hanya membaca `bot_state`, tetapi `portfolio.refresh` di tab
portofolio **menulis** baris `snapshots` ke `portfolio.db`. Bila Anda ingin
DB benar-benar diam saat dicadangkan (langkah d), matikan juga:

```powershell
# (PowerShell Administrator)
Disable-ScheduledTask -TaskName "IndodaxDashboard" | Select-Object TaskName, State
```

Bila dilewati, tidak ada kerusakan: cadangan tetap sah, hanya bisa memuat
snapshot yang lebih baru dari saat bot berhenti.

### b3. `[OPENCODE-AMAN]` Konfirmasi tidak ada pemicu lain

```powershell
Get-ScheduledTask | Where-Object { $_.TaskName -match "ndodax|ashboard|rading|v1|bot" } |
  Select-Object TaskName, State | Format-Table -AutoSize
Get-ChildItem "$env:APPDATA\Microsoft\Windows\Start Menu\Programs\Startup" -Force |
  Select-Object Name
```

Folder Startup tidak boleh berisi pintasan bot. Bila ada, pindahkan (jangan
hapus) sebelum melanjutkan.

---

## c. Menghentikan bot dengan benar

Urutannya penting: task dinonaktifkan **dulu** (b), baru proses dimatikan.
Mematikan proses lebih dulu akan memicu ulangan `vbs` dan `RestartOnFailure`.

### c1. `[SAYA-JALANKAN]` Hentikan task, lalu matikan pohon prosesnya

```powershell
# (PowerShell Administrator)
Stop-ScheduledTask -TaskName "IndodaxV1Shadow"

Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -match "run_v1_hidden|main_loop" } |
  Select-Object ProcessId, ParentProcessId, Name, CommandLine | Format-List
```

Catat PID `wscript.exe` (induk) dan `pythonw.exe` (anak), lalu matikan
seluruh pohon dari induk:

```powershell
# ganti 7668 dengan PID wscript.exe hasil perintah di atas
taskkill /F /T /PID 7668
```

`/T` wajib: `Stop-ScheduledTask` sering meninggalkan anak (`cmd.exe`,
`pythonw.exe`) yang tetap memegang kunci instance dan `portfolio.db`.

### c2. `[OPENCODE-AMAN]` Pastikan tidak hidup lagi

```powershell
Start-Sleep -Seconds 90   # lebih lama dari jeda ulangan vbs (60 dtk)
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
  Select-Object ProcessId, ParentProcessId, CreationDate | Format-Table -AutoSize
Get-ScheduledTask -TaskName "IndodaxV1Shadow" | Select-Object TaskName, State
```

Harapan: **tidak ada** `pythonw.exe`, `State = Disabled`, dan
`v1_loop.log` tidak bertambah. Bila `pythonw.exe` muncul lagi: task masih
Enabled, atau ada pemicu lain (ulangi b1/b3), atau proses dijalankan manual
dari sesi lain.

---

## d. Cadangkan `portfolio.db` DI LUAR folder repo

Cadangan di dalam repo berisiko ikut ter-commit dan menimpa DB asli.

### d1. `[SAYA-JALANKAN]` Salin bertanggal ke `C:\Users\KakiTangan\backups`

```powershell
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$dir   = "C:\Users\KakiTangan\backups"
$dst   = Join-Path $dir "portfolio-$stamp.db"
New-Item -ItemType Directory -Force $dir | Out-Null

Copy-Item "portfolio.db" $dst
Copy-Item "portfolio.db-wal" "$dst-wal" -ErrorAction SilentlyContinue
Copy-Item "portfolio.db-shm" "$dst-shm" -ErrorAction SilentlyContinue

Get-ChildItem $dir | Sort-Object LastWriteTime -Descending |
  Select-Object -First 4 Name, Length, LastWriteTime | Format-Table -AutoSize
```

`-wal`/`-shm` wajib ikut bila ada: tanpa WAL, cadangan bisa kehilangan
transaksi terakhir. Nama `portfolio-<stamp>.db-wal` sudah bentuk yang dicari
SQLite untuk pasangan `portfolio-<stamp>.db`.

### d2. `[SAYA-JALANKAN]` Verifikasi cadangan bisa dibuka

```powershell
$py = "C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\python.exe"
# ganti nama berkas dengan hasil langkah d1
& $py -c 'import sqlite3; c = sqlite3.connect("file:C:/Users/KakiTangan/backups/portfolio-GANTI.db?mode=ro", uri=True); print("integrity:", c.execute("PRAGMA integrity_check").fetchone()); print("trades:", c.execute("SELECT COUNT(*) FROM trades").fetchone()); print("proposals:", c.execute("SELECT COUNT(*) FROM proposals").fetchone()); print("state:", c.execute("SELECT v1_enabled, shadow_day FROM bot_state").fetchone())'
```

Harapan: `integrity: ('ok',)` dan jumlah baris masuk akal. Bila `ok` tidak
muncul, **berhenti** — jangan lanjut ke langkah e.

---

## e. Mematikan sakelar lewat jalur resmi

Jalur resmi satu-satunya untuk `v1_enabled = 0` adalah
`main_loop.set_shadow_day(conn, day)` dengan `day < GO_LIVE_SHADOW_DAYS` (7).
Jangan `UPDATE` manual dan jangan menyentuh `aktifkan_live`.

### e1. `[SAYA-JALANKAN]` Jalankan (paste utuh)

```powershell
$py = "C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\python.exe"
Set-Location "C:\Users\KakiTangan\trading-bot"

& $py -c 'import sqlite3, main_loop; c = sqlite3.connect("portfolio.db"); print("sebelum:", tuple(c.execute("SELECT v1_enabled, shadow_day, last_dca_date, dca_status FROM bot_state").fetchone())); main_loop.set_shadow_day(c, 0); print("sesudah:", tuple(c.execute("SELECT v1_enabled, shadow_day, last_dca_date, dca_status FROM bot_state").fetchone())); c.close()'
```

Harapan: `sebelum: (1, N, ...)` lalu `sesudah: (0, 0, ...)` — `last_dca_date`
dan `dca_status` **tidak berubah**.

### e2. Efek samping yang harus Anda terima

| Hal | Akibat |
|---|---|
| `v1_enabled` | 1 → 0. Tidak ada order yang bisa dikirim. |
| `shadow_day` | jadi 0. **Tujuh hari bayangan bersih harus dikumpulkan ulang**; lantai go-live tidak lagi terpenuhi. |
| Jendela DCA bulan ini | **tidak disentuh**: `last_dca_date`, `dca_sent_ts`, `dca_status` tetap. Jendela yang sedang `awaiting` tetap menunggu jawaban Anda. |
| Usulan menunggu di `proposals` | **tidak dihapus** dan tidak dibatalkan. Usulan lama tetap ada; setelah restart, usulan yang sudah terkirim dipulihkan dari kolomnya. |
| Memori take-profit | **tidak disentuh**. (`aktifkan_live` yang menghapus baris TP mode `shadow`; `set_shadow_day` tidak.) |
| `shadow_checked_day` | tidak disentuh, jadi hari ini tidak dihitung dua kali. |
| `telegram_offset` | tidak disentuh; pesan lama tidak diproses ulang. |

### e3. `[SAYA-JALANKAN]` Verifikasi

```powershell
& $py -c 'import sqlite3; c = sqlite3.connect("file:portfolio.db?mode=ro", uri=True); print(c.execute("SELECT v1_enabled, shadow_day, shadow_checked_day, last_dca_date, dca_status FROM bot_state").fetchall())'
```

Harapan: `[(0, 0, ..., ...)]`. Bila `v1_enabled` masih 1: perintah e1 gagal
(galat di layar) — jangan menambal dengan `UPDATE` manual, laporkan.

---

## f. Jalankan kode baru, tunggu, lalu preflight

### f1. `[OPENCODE-AMAN]` Pastikan kode yang akan dijalankan

```powershell
git -C C:\Users\KakiTangan\trading-bot rev-parse --abbrev-ref HEAD
git -C C:\Users\KakiTangan\trading-bot log --oneline -1
git -C C:\Users\KakiTangan\trading-bot status --short
```

Harapan: branch `fix/review-p1`, HEAD `00f176f`, tidak ada `.py` yang
termodifikasi. Bila ada, hentikan dan bereskan dulu — jangan jalankan bot dari
working tree kotor.

### f2. `[SAYA-JALANKAN]` Nyalakan lagi

```powershell
# (PowerShell Administrator)
Enable-ScheduledTask -TaskName "IndodaxV1Shadow" | Select-Object TaskName, State
Start-ScheduledTask  -TaskName "IndodaxV1Shadow"
```

Alternatif manual (tanpa task): dobel-klik `run_v1.bat`. Bila bot sudah
jalan, berkas itu menolak sendiri lewat kunci instance tunggal.

### f3. `[SAYA-JALANKAN]` Tunggu beberapa siklus

Metadata pair disegarkan paling banyak sekali sehari; percobaan yang **gagal**
dibatasi `pair_meta.RETRY_S = 900` detik (15 menit). Setelah gagal, tunggu
minimal 15 menit sebelum menyimpulkan metadata masih hilang.

```powershell
Get-Content v1_loop.log -Tail 30
Get-Content errors.log   -Tail 20
```

Yang dicari di `v1_loop.log`: baris `Bot siap: polling Telegram aktif — mode
bayangan, tanpa order yang bisa dikirim.` (bukan `eksekusi menyala`). Bila
muncul `Pembaca pasar gagal`, penyegaran metadata ikut gagal — periksa
jaringan ke `https://indodax.com/api/pairs`.

### f4. `[SAYA-JALANKAN]` Preflight

```powershell
Set-Location "C:\Users\KakiTangan\trading-bot"
& "C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\python.exe" -m preflight --target bayangan
"exit code: $LASTEXITCODE"
```

Harapan: `GO — semua pemeriksaan lolos` dan `exit code: 0`.

Bila **tidak** GO, baca baris `[GAGAL]`/`[TAK TERBACA]`:

| Baris | Arti | Tindakan |
|---|---|---|
| `metadata pair: basi/hilang` | penyegaran gagal atau belum 15 menit | cek jaringan + `errors.log`, tunggu 15 menit, ulangi f4 |
| `izin kunci: sakelar aktif tetapi kunci tidak bisa trading` | `v1_enabled` masih 1 | ulangi e1, verifikasi e3 |
| `izin kunci: ... canWithdraw` | kunci API punya izin tarik dana | **berhenti**, cabut kunci (g) |
| `izin kunci: ... canTrade` saat sakelar mati | kunci masih bisa trading padahal bayangan | **berhenti**, cabut kunci (g) |
| `jam bursa: tidak terbaca` | jaringan/endpoint waktu | cek jaringan, ulangi |
| exit code 2 | ada yang tidak bisa dievaluasi | jangan tebak; laporkan isi barisnya |

Jangan menaikkan target ke `live` sebelum `--target bayangan` GO.

---

## g. Tanda bahaya — berhenti dan cabut kunci API

Hentikan semua langkah, nonaktifkan task (b1), matikan pohon proses (c1), lalu
**cabut kunci API di Indodax** (menu API: hapus kunci; jangan hanya
menonaktifkan izin), bila salah satu terjadi:

- ada order/buy/sell di riwayat Indodax yang tidak Anda kenali, atau saldo
  berubah tanpa Anda menyetujui;
- `v1_loop.log` memuat `eksekusi menyala` padahal e3 menunjukkan
  `v1_enabled = 0`;
- preflight melaporkan `canWithdraw=True` (izin tarik dana tidak boleh ada
  sama sekali);
- preflight melaporkan `canTrade=True` saat sakelar mati;
- Telegram mengirim pesan "order dikirim/dieksekusi" yang tidak Anda setujui;
- `v1_enabled` kembali 1 tanpa Anda menjalankan `aktifkan_live`.

Setelah mencabut kunci, jangan menyalakan task lagi sampai penyebabnya
ditemukan. Kunci baru dibuat dengan izin trade **mati** (`INDODAX_CAN_TRADE=0`).

---

## h. Membatalkan seluruh langkah

Urutan balik, dari yang paling akhir:

1. **Batalkan f (kode baru berjalan):** nonaktifkan task lagi (b1), matikan
   pohon proses (c1), verifikasi (c2).
2. **Batalkan e (sakelar):** **tidak ada tombol "kembali" yang aman.**
   `v1_enabled=1` hanya bisa lewat `aktifkan_live`, yang menuntut
   `shadow_day >= 7` dan keputusan sadar Anda. Karena e1 menyetel
   `shadow_day=0`, sakelar **tidak bisa** dinyalakan lagi sampai 7 hari
   bayangan bersih terkumpul. Itu disengaja — jangan diakali dengan `UPDATE`.
3. **Batalkan d (cadangan):** cadangan tidak perlu dibatalkan. Bila Anda
   memang ingin memulihkan DB:
   ```powershell
   # (PowerShell Administrator) — bot & dashboard HARUS sudah mati (b1, c1)
   Copy-Item "portfolio.db" "portfolio.db.sebelum-pulih"
   Copy-Item "C:\Users\KakiTangan\backups\portfolio-<stamp>.db" "portfolio.db" -Force
   Remove-Item "portfolio.db-wal","portfolio.db-shm" -ErrorAction SilentlyContinue
   ```
   **Peringatan:** pemulihan membuang semua baris yang ditulis setelah
   cadangan (snapshot, usulan, trade). Cadangan `sebelum-pulih` disimpan
   supaya langkah ini bisa dibatalkan lagi. Cadangan dari langkah d dibuat
   **sebelum** e1, jadi ia memuat `v1_enabled=1` dan `shadow_day` lama:
   setelah memulihkan, **wajib** jalankan ulang e1 lalu verifikasi e3 sebelum
   bot dinyalakan lagi.
4. **Batalkan b2:** `Enable-ScheduledTask -TaskName "IndodaxDashboard"`.
5. **Batalkan b1:** `Enable-ScheduledTask -TaskName "IndodaxV1Shadow"`.
   Bot hidup lagi pada trigger berikutnya (boot/logon/Event 41), atau segera
   lewat `Start-ScheduledTask -TaskName "IndodaxV1Shadow"`.

Catat setiap langkah yang sudah Anda jalankan (nomor + waktu) supaya urutan
balik tidak tertukar.

---

## Yang TIDAK boleh dilakukan

- Menjalankan `aktifkan_live` atau `set_shadow_day(conn, 7)` untuk "menguji".
- `UPDATE bot_state SET v1_enabled = ...` manual.
- Menghapus task (`Unregister-ScheduledTask`) — cukup `Disable`.
- Menyalin `portfolio.db` ke dalam repo, atau `git add` berkas `.db`.
- Mengubah `.env` (kunci API, `INDODAX_CAN_TRADE`) dari sesi agent.
- Menjalankan tes/skrip yang membuka `portfolio.db` asli. `conftest.py`
  memblokir jaringan, bukan akses DB; pakai `--basetemp` di luar repo.
