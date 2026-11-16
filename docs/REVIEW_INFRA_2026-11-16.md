# Tinjauan infrastruktur — 2026-11-16

Tiga butir (5–7) bersifat **laporan baca-saja**. Tidak ada perubahan pada
Task Scheduler, `portfolio.db`, `.env`, atau kode bot di luar empat perbaikan
butir 1–4 (commit `45be9e8`, `ea5eece`, `7368ba0`, `f09372e`).

---

## Butir 5 — Seberapa sering "Bot siap" dan apakah VBS mengulang terus?

### Yang bisa dibuktikan

| Bukti | Nilai |
|---|---|
| `v1_loop.log` baris "Bot siap" | **5** |
| `v1_loop.log` baris "Pesan sapa awal tidak terkirim" | **5** |
| `v1_loop.log` baris "Bot mati (kode N) - ulangan ke-K" | **0** |
| `v1_loop.log` baris "Eksekusi belum dinyalakan: berjalan dalam mode bayangan" | **1** |
| Task `IndodaxV1Shadow` Last Run Time | 16/11/2026 01:11:40 |
| Task `IndodaxV1Shadow` Last Result | `267009` = `0x41301` = "task sedang berjalan" |
| Proses `pythonw.exe` PID 7400 CreationDate | 16/11/2026 01:11:41 (≈10,8 jam hidup) |
| Status task | `Running` |

### Kesimpulan

- **Hari ini bot TIDAK mengulang-ulang.** Satu proses sejak 01:11:41, tanpa
  satu pun baris ulangan VBS. Rantai ulangan `run_v1_hidden.vbs` (maks 3
  ulangan, jeda 60 dtk) belum pernah menyala pada isi log sekarang.
- **Jumlah 5 itu bukan sensus restart yang sah.** Log tanpa cap waktu (cacat
  yang diperbaiki butir 2), dan isinya tidak konsisten: 5 baris "Bot siap"
  tetapi hanya 1 baris "Eksekusi belum dinyalakan" — padahal keduanya dicetak
  sekali per proses pada versi kode yang sama. Berkas ini pernah dipangkas
  atau ditimpa sebelum ada rotasi.
- **Temuan tambahan yang perlu keputusanmu:** kelima baris "Bot siap" berbunyi
  *"eksekusi menyala — pantau dengan teliti"*, artinya `v1_enabled` bernilai
  **true** pada lima proses itu. Proses yang berjalan sekarang mencetak mode
  bayangan, jadi sakelar saat ini mati. Riwayat ini tidak bisa dipertanggung-
  jawabkan tanpa cap waktu — alasan lain butir 2 penting.

### Temuan tambahan: token Telegram mati sejak 07:00 hari ini

```
2026-11-16 07:00:14,151 WARNING Denyut penuh gagal - dilewati, dicoba lagi:
  401 Client Error: Unauthorized for url:
  https://api.telegram.org/bot<disamarkan>/getUpdates?offset=0&timeout=0
```

- 51 kemunculan `401 Client Error` di `errors.log` antara 07:00 dan 11:57,
  kira-kira tiap 5–6 menit; tidak ada 401 sebelum 07:00.
- `scheduler.log` memuat 12 baris "Telegram gagal." (rekap harian 21:00).
- Artinya: bot **tidak buta total** terhadap bursa, tetapi tidak bisa
  menyapa/meminta persetujuan lewat Telegram. Setiap usulan yang butuh
  persetujuan akan gagal-tertutup (tidak ada order) — benar secara
  keselamatan, tetapi bot praktis tidak bisa dipakai sampai token diganti.
- Baris preflight baru (butir 4) akan melaporkan ini sebagai `[GAGAL]
  Telegram: Telegram menolak: 401 Unauthorized` sebelum bot dijalankan.

---

## Butir 6 — Dashboard mendengarkan `0.0.0.0` tanpa autentikasi

### Lokasi persis

| Berkas | Baris | Isi |
|---|---|---|
| `start_dashboard.ps1` | 3 | komentar: "streamlit headless, listen 0.0.0.0:8501, diakses via ..." |
| `start_dashboard.ps1` | 43 | `... --server.address 0.0.0.0 --server.port 8501 --server.headless true ...` |
| `start_dashboard.cmd` | 3 | `python -m streamlit run dashboard/app.py ... --server.address 0.0.0.0` |
| `.streamlit/config.toml` | 2 | `address = "0.0.0.0"` |
| `.streamlit/config.toml` | 5–8 | `enableCORS = true`, `enableXsrfProtection = false`, `enableStaticServing = true`, `maxUploadSize = 200` |
| `.streamlit/config.toml` | 11–12 | `[browser] serverAddress = "100.99.49.65"` (alamat Tailscale) |

Task `IndodaxDashboard` menjalankan
`powershell.exe -NoProfile -ExecutionPolicy Bypass -File "C:\Users\KakiTangan\trading-bot\start_dashboard.ps1"`.
Status task: **Disabled**, tetapi proses `python.exe` PID 7160 masih hidup
sejak 12/11/2026 08:56:34 (Last Result `0x800710E0`). Artinya dashboard hidup
sekarang, tetapi **tidak akan hidup lagi setelah reboot** karena task-nya
dimatikan.

### Yang diekspos (tanpa login apa pun)

Tidak ada `password`, `login`, `st.secrets`, atau `auth` di `dashboard/*.py`.
Siapa pun yang bisa menjangkau port 8501 melihat:

- **Tab Portofolio:** saldo tiap aset dan nilai total (Rp), pertumbuhan dan
  take-profit per aset, setoran bulan ini vs target.
- **Tab Shadow:** `shadow_day` (hari ke-N dari 7), **status sakelar
  eksekusi** (`v1_enabled`), status DCA bulan ini, rekap usulan hari ini,
  20 usulan terakhir (waktu, pair, nilai Rp, hasil, catatan), dan commit hash.

`0.0.0.0` = semua antarmuka (LAN + Tailscale). Satu-satunya penghalang adalah
Windows Firewall; `enableXsrfProtection = false` dan `enableCORS = true`
mematikan dua perlindungan bawaan Streamlit.

### Usulan (belum diterapkan — menunggu keputusanmu)

```diff
--- a/.streamlit/config.toml
+++ b/.streamlit/config.toml
@@
-address = "0.0.0.0"
+address = "127.0.0.1"
@@
-enableCORS = true
-enableXsrfProtection = false
+enableCORS = false
+enableXsrfProtection = true
```

```diff
--- a/start_dashboard.ps1
+++ b/start_dashboard.ps1
@@
-& cmd.exe /c "`"$py`" -m streamlit run dashboard\app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true >> `"$dir\dashboard.log`" 2>&1"
+& cmd.exe /c "`"$py`" -m streamlit run dashboard\app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true >> `"$dir\dashboard.log`" 2>&1"
```

```diff
--- a/start_dashboard.cmd
+++ b/start_dashboard.cmd
@@
-python -m streamlit run dashboard/app.py --server.headless true --server.port 8501 --server.address 0.0.0.0
+python -m streamlit run dashboard/app.py --server.headless true --server.port 8501 --server.address 127.0.0.1
```

Langkah manual setelahnya (tidak dijalankan di sesi ini):

1. Akses dari HP lewat Tailscale: `tailscale serve --bg 8501` (proxy
   Tailscale-only ke `127.0.0.1:8501`), lalu buka `https://<nama-mesin>.<tailnet>.ts.net`.
2. Hentikan proses dashboard lama (PID 7160) dan nyalakan ulang lewat
   `start_dashboard.ps1` agar konfigurasi baru terbaca.
3. Putuskan nasib task `IndodaxDashboard` yang sekarang `Disabled`: aktifkan
   kembali (`schtasks /change /tn IndodaxDashboard /enable`) atau hapus, supaya
   tidak ada dashboard "hidup tapi tak akan kembali setelah reboot".

---

## Butir 7 — "Start only if network available" pada `IndodaxV1Shadow`

### Status terdaftar (dibaca dari XML task, bukan dari skrip)

`schtasks /query /tn IndodaxV1Shadow /xml` **tidak memuat elemen
`RunOnlyIfNetworkAvailable`**. Nilai bawaannya **false** — opsi "Start only if
network available" **MATI**. `register_v1_task.ps1` juga menulisnya `false`
secara eksplisit.

Setelan lain yang relevan:

| Setelan | Nilai |
|---|---|
| `BootTrigger` Delay | `PT1M` (1 menit) |
| `EventTrigger` (Kernel-Power 41) Delay | `PT1M` |
| `LogonTrigger` Delay | `PT30S` |
| `StartWhenAvailable` | `true` |
| `RestartOnFailure` | 3 × tiap `PT1M` |
| `MultipleInstancesPolicy` | `IgnoreNew` |
| `ExecutionTimeLimit` | `PT0S` (tanpa batas) |
| `IdleSettings.StopOnIdleEnd` | `true` |

### Penilaian

Menyalakan `RunOnlyIfNetworkAvailable` **tidak dianjurkan**: Windows memakai
Network Location Awareness (NLA), dan pada mesin ini NLA sering baru "online"
beberapa menit setelah boot; bila NLA tidak pernah melaporkan online, task
tidak jalan sama sekali — kegagalan senyap, lebih buruk daripada gagal
berisik. Bot sudah menangani jaringan putus dengan baik (butir 1: pesan sebab
asli, denyut dilewati, tidak mati).

Usulan lebih aman: **memperpanjang jeda BootTrigger** dari `PT1M` menjadi
`PT2M`–`PT3M`, sehingga DHCP/DNS sempat siap sebelum proses pertama. Itu satu
baris di `register_v1_task.ps1`:

```diff
     <BootTrigger>
       <Enabled>true</Enabled>
-      <Delay>PT1M</Delay>
+      <Delay>PT3M</Delay>
     </BootTrigger>
```

Perubahan itu baru berlaku setelah `register_v1_task.ps1` dijalankan elevated
(menimpa task terdaftar) — **tidak dijalankan di sesi ini**.

Catatan terpisah: `IdleSettings.StopOnIdleEnd = true` tidak berbahaya di sini
karena `IdleSettings` hanya berlaku bila ada `IdleTrigger`; task ini tidak
punya. Dibiarkan apa adanya.
