$py = "C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\pythonw.exe"
$botDir = "C:\Users\KakiTangan\trading-bot"

$action = New-ScheduledTaskAction `
    -Execute "cmd.exe" `
    -Argument "/c `"$py`" monitor.py >> monitor.log 2>&1" `
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
    -TaskName "IndodaxTradeMonitor" `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -Principal $principal `
    -Description "Pemantau transaksi Indodax (trading-bot)" `
    -Force

Write-Output "Task 'IndodaxTradeMonitor' berhasil didaftarkan"
