# Monitor pengisi trades.db - dijalankan task "IndodaxMonitor".
#
# S4U (tanpa login). Foreground: task tetap "Running" selama monitor hidup,
# jadi RestartOnFailure (3x / 1 mnt) bisa menghidupkan ulang kalau tumbang.
#
# CATATAN: file ini HARUS ASCII polos (PowerShell 5.1 baca .ps1 tanpa BOM
# sebagai ANSI; karakter non-ASCII merusak string).

$ErrorActionPreference = "Continue"
$dir = "C:\Users\KakiTangan\trading-bot"
$py  = "C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\python.exe"

Set-Location $dir

# Bersihkan instance yatim monitor.py: dua monitor menulis trades.db bersamaan
# (INSERT OR IGNORE menjaga tabel trades, tapi depth_snapshotsdoUBLE baris).
$lama = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object { $_.CommandLine -like "*monitor.py*" })
foreach ($p in $lama) {
    taskkill /F /T /PID $p.ProcessId 2>&1 | Out-Null
}
if ($lama.Count -gt 0) { Start-Sleep -Seconds 2 }

& cmd.exe /c "`"$py`" -u monitor.py >> `"$dir\monitor.log`" 2>&1"
exit $LASTEXITCODE
