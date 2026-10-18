$botDir = "C:\Users\KakiTangan\trading-bot"

# Peluncur wscript + VBS tersembunyi: cmd.exe langsung membuat jendela
# kosong yang membingungkan ("boleh ditutup atau tidak?"). wscript berjalan
# tanpa jendela dan MENUNGGU bot selesai (VBS pakai wait=True), jadi task
# tetap tercatat berjalan — IgnoreNew tetap menjaga satu pemilik getUpdates.
$action = New-ScheduledTaskAction `
    -Execute "wscript.exe" `
    -Argument "`"$botDir\run_v1_hidden.vbs`"" `
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
