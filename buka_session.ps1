# buka_session.ps1 - Buka session OpenCode "Shadow runner - pantau G5 &
# perbaikan boot" secara otomatis dengan satu klik.
#
# Dibuat 2026-10-18. Session ini memang berdomisili di C:\Users\KakiTangan
# (bukan folder trading-bot), jadi direktorinya dipertahankan persis seperti
# saat ini supaya kelanjutan kerja tidak ada yang bergeser.
# Cara pakai: double-click buka_session.bat, atau dari PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\buka_session.ps1

$sessionId = "ses_ecc5fc9e5ffeoovpKETzwpqPN7"
$folderSession = "C:\Users\KakiTangan"

if (-not (Get-Command opencode -ErrorAction SilentlyContinue)) {
    Write-Host "opencode tidak ditemukan di PATH - instal dulu OpenCode." -ForegroundColor Red
    Read-Host "Tekan Enter untuk menutup"
    exit 1
}

Write-Host "Membuka session: Shadow runner - pantau G5 & perbaikan boot" -ForegroundColor Cyan
Write-Host "Session ID     : $sessionId"
Write-Host "Direktori      : $folderSession"
Write-Host ""

Set-Location $folderSession
& opencode --session $sessionId $folderSession

# Sampai di sini berarti jendela OpenCode sudah ditutup normal.
Write-Host ""
Write-Host "Session sudah ditutup." -ForegroundColor DarkGray
