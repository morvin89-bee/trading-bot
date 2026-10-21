# Rotasi trading-bot\errors.log: salin ke errors.log.1 .. .10, lalu kosongkan
# file aslinya TANPA me-rename. Alasannya: pythonw (main_loop, scanner, monitor)
# memegang errors.log lewat logging.FileHandler -> Windows menolak rename file
# yang sedang dibuka (tanpa FILE_SHARE_DELETE). Copy + truncate in place aman:
# handle python tetap valid, mode append ("a") menulis lagi dari offset 0.
#
# Dijalankan task "Rotasi Log" setiap hari 04:30 (S4U, tanpa login user).
# Ambang: hanya berputar bila file >= 5 MB. ASCII polos (PowerShell 5.1).

$log = "C:\Users\KakiTangan\trading-bot\errors.log"
$keep = 10
$minBytes = 5MB

if (-not (Test-Path $log)) { exit 0 }
if ((Get-Item $log).Length -lt $minBytes) { exit 0 }

# Geser salinan lama: .9 -> .10, .8 -> .9, ... , .1 -> .2
for ($i = $keep - 1; $i -ge 1; $i--) {
    $src = "$log.$i"
    $dst = "$log.$($i + 1)"
    if (Test-Path $src) { Move-Item -Path $src -Destination $dst -Force }
}

# Salin isi file hidup ke .1 (file hidup tak boleh di-rename)
Copy-Item -Path $log -Destination "$log.1" -Force

# Kosongkan file hidup tanpa menutup handle python
$fs = [System.IO.File]::Open($log, [System.IO.FileMode]::Open,
                             [System.IO.FileAccess]::ReadWrite,
                             [System.IO.FileShare]::ReadWrite)
$fs.SetLength(0)
$fs.Flush()
$fs.Close()

exit 0
