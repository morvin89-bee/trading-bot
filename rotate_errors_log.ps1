# Rotasi trading-bot\errors.log + monitor.log: salin ke .1 .. .10, lalu
# kosongkan file aslinya TANPA me-rename. Alasan: pythonw (main_loop, scanner,
# monitor) dan cmd (monitor.log lewat redirect >>) memegang file lewat handle
# -> Windows menolak rename file yang sedang dibuka (tanpa FILE_SHARE_DELETE).
# Copy + truncate in place aman: handle python/cmd tetap valid, mode append
# ("a") menulis lagi dari offset 0.
#
# Dijalankan task "Rotasi Log" setiap hari 04:30 (S4U, tanpa login user).
# Ambang: hanya berputar bila file >= 5 MB. ASCII polos (PowerShell 5.1).
# Nama file dipertahankan meski kini juga merotasi monitor.log, karena task
# "Rotasi Log" mengarah ke path ini (mengganti nama butuh re-register task
# elevated). Jangan tambahkan karakter non-ASCII di skrip ini.

param(
    [string]$Log = "",
    [int64]$MinBytes = 0
)

$botDir = "C:\Users\KakiTangan\trading-bot"
$keep = 10
$defaultMin = 5MB

$targets = @()
if ($Log) {
    $m = $defaultMin
    if ($MinBytes -gt 0) { $m = $MinBytes }
    $targets += ,@{ path = $Log; min = $m }
} else {
    $targets += ,@{ path = "$botDir\errors.log";  min = $defaultMin }
    $targets += ,@{ path = "$botDir\monitor.log"; min = $defaultMin }
}

foreach ($t in $targets) {
    $log = $t.path
    if (-not (Test-Path $log)) { continue }
    if ((Get-Item $log).Length -lt $t.min) { continue }

    # Geser salinan lama: .9 -> .10, .8 -> .9, ... , .1 -> .2
    for ($i = $keep - 1; $i -ge 1; $i--) {
        $src = "$log.$i"
        $dst = "$log.$($i + 1)"
        if (Test-Path $src) { Move-Item -Path $src -Destination $dst -Force }
    }

    # Salin isi file hidup ke .1 (file hidup tak boleh di-rename)
    Copy-Item -Path $log -Destination "$log.1" -Force

    # Kosongkan file hidup tanpa menutup handle python/cmd
    $fs = [System.IO.File]::Open($log, [System.IO.FileMode]::Open,
                                 [System.IO.FileAccess]::ReadWrite,
                                 [System.IO.FileShare]::ReadWrite)
    $fs.SetLength(0)
    $fs.Flush()
    $fs.Close()
}

exit 0