$py = "C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\pythonw.exe"
$botDir = "C:\Users\KakiTangan\trading-bot"

# Pola identik dengan register_task.ps1 (IndodaxTradeMonitor) yang sudah
# terbukti: AtLogOn, tanpa batas waktu eksekusi, MultipleInstances IgnoreNew
# (SATU pemilik getUpdates — dua proses berebut Telegram = konfirmasi hilang),
# restart 3x per menit bila proses mati.
$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$py`" -u main_loop.py >> v1_loop.log 2>&1" `
    -WorkingDirectory $botDir

$trigger = New-ScheduledTaskTrigger -AtLogOn

$settings = New-ScheduledTaskSettingsSet `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -ExecutionTimeLimit ([TimeSpan]::Zero)

$principal = New-ScheduledTaskPrincipal `
    -UserId "$env:USERNAME" `
    -LogonType Interactive `
    -RunLevel Limited

Register-ScheduledTask `
    -TaskName "IndodaxV1Shadow" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Bot semi-otomatis mode bayangan / periode shadow (trading-bot)" `
    -Force

Write-Output "Task 'IndodaxV1Shadow' berhasil didaftarkan"
