# Dashboard trading (streamlit) - dijalankan task "IndodaxDashboard".
#
# S4U (tanpa login): streamlit headless, listen 0.0.0.0:8501, diakses via
# Tailscale http://100.99.49.65:8501 (config di .streamlit\config.toml).
#
# Foreground: task tetap "Running" selama streamlit hidup, jadi RestartOnFailure
# (3x / 1 mnt) bisa menghidupkan ulang kalau streamlit tumbang.
#
# CATATAN: file ini HARUS ASCII polos. PowerShell 5.1 membaca .ps1 tanpa BOM
# sebagai ANSI; karakter non-ASCII (mis. em-dash UTF-8) berubah jadi
# smart-quote yang menutup string lebih awal -> parse error, skrip tak jalan.

$ErrorActionPreference = "Continue"
$dir = "C:\Users\KakiTangan\trading-bot"
$py  = "C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\python.exe"

Set-Location $dir

# Bersihkan instance yatim: proses streamlit lama yang masih memegang port
# 8501 (mis. task di-/end tanpa=row tree mati) membuat instance baru gagal
# bind. Task ini satu-satunya pemilik yang sah, jadi lethalkan +
# taskkill /T (tree) supaya anak cmd/python ikut mati.
$kreis = Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue
"guard: port 8501 listener(s) [pid: $($kreis.OwningProcess -join ',')]" | Out-File -FilePath "$dir\dashboard_guard.log" -Append -Encoding utf8
foreach ($k in $kreis) {
    if ($k.OwningProcess -gt 0) {
        taskkill /F /T /PID $k.OwningProcess 2>&1 | Out-Null
        Start-Sleep -Seconds 2
    }
}

# Port masih terikat (mis. proses lain): jangan crash-loop, catat lalu keluar
# dengan kode 0 -> Task Scheduler tak memicu RestartOnFailure.
if (Get-NetTCPConnection -LocalPort 8501 -State Listen -ErrorAction SilentlyContinue) {
    "Port 8501 masih dipakai proses lain; dashboard tak dijalankan ulang." | Out-File -FilePath "$dir\dashboard.log" -Append -Encoding utf8
    exit 0
}

# Redirection lewat cmd.exe: stdout+stderr masuk log apa adanya (PowerShell
# `*>>` membungkus stderr jadi ErrorRecord + noise "NativeCommandError").
# cmd tetap di depan -> script ini blocking -> task "Running" selama server
# hidup -> RestartOnFailure tetap berlaku.
& cmd.exe /c "`"$py`" -m streamlit run dashboard\app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true >> `"$dir\dashboard.log`" 2>&1"
exit $LASTEXITCODE
