$botDir = "C:\Users\KakiTangan\trading-bot"

# Task 'IndodaxV1Shadow' - bot semi-otomatis mode bayangan / periode shadow.
#
# PENTING (perubahan 2026-10-22): logon type HARUS S4U, bukan Interactive.
# "Interactive only" = task hanya jalan kalau ada sesi login yang aktif.
# Kejadian nyata 2026-10-21: mesin mati 19:20, tombol power dinyalakan lagi,
# bot baru hidup 23:31 (4 jam 11 menit mati) karena task memicu "At logon"
# dan tak ada yang login. S4U = "jalan apakah user login atau tidak" tanpa
# menyimpan password, dan probe di mesin ini membuktikan S4U jalan.
#
# Trigger:
#   - At startup (delay 1 mnt): bot hidup lagi begitu mesin boot, tanpa login.
#   - Event 41 (Kernel-Power): restart setelah shutdown/crash tak bersih.
#   - At logon (delay 30 dtk): jaring pengaman bila trigger startup terlewat.
#   MultipleInstances IgnoreNew + RestartOnFailure = satu pemilik getUpdates.
#
# PENTING: skrip ini WAJIB dijalankan elevated (PowerShell "Run as
# administrator"). Tanpa hak admin, Principal S4U ditolak: "Access is denied".
# Jalankan dari PowerShell admin:
#   & 'C:\Users\KakiTangan\trading-bot\register_v1_task.ps1'
#
# CATATAN: file ini harus ASCII polos. PowerShell 5.1 membaca .ps1 tanpa BOM
# sebagai ANSI; karakter non-ASCII (mis. em-dash UTF-8) berubah jadi
# smart-quote yang menutup string lebih awal -> parse error, skrip tak jalan.

# ---------- XML langsung, bukan cmdlet ----------
# New-ScheduledTaskTrigger tidak bisa membuat EventTrigger (Event ID 41),
# jadi seluruh definisi task ditulis sebagai XML.

$triggerBoot = @"
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT1M</Delay>
    </BootTrigger>
"@

$triggerEvent = @"
    <EventTrigger>
      <Enabled>true</Enabled>
      <Subscription>&lt;QueryList&gt;&lt;Query Id="0" Path="System"&gt;&lt;Select Path="System"&gt;*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] and (EventID=41)]]&lt;/Select&gt;&lt;/Query&gt;&lt;/QueryList&gt;</Subscription>
      <Delay>PT1M</Delay>
    </EventTrigger>
"@

$triggerLogon = @"
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
    </LogonTrigger>
"@

$userSid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value
$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Bot semi-otomatis mode bayangan / periode shadow (trading-bot). S4U: jalan tanpa login. Trigger: At startup + Event 41 (crash) + Logon.</Description>
    <URI>\IndodaxV1Shadow</URI>
  </RegistrationInfo>
  <Triggers>
$($triggerBoot.Trim())
$($triggerEvent.Trim())
$($triggerLogon.Trim())
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$userSid</UserId>
      <LogonType>S4U</LogonType>
    </Principal>
  </Principals>
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <RestartOnFailure>
      <Count>3</Count>
      <Interval>PT1M</Interval>
    </RestartOnFailure>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <WakeToRun>false</WakeToRun>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>wscript.exe</Command>
      <Arguments>"$botDir\run_v1_hidden.vbs"</Arguments>
      <WorkingDirectory>$botDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

$xmlPath = Join-Path $env:TEMP "task_indodax_s4u_register.xml"
Set-Content -Path $xmlPath -Value $xml -Encoding Unicode

schtasks /create /tn "IndodaxV1Shadow" /xml $xmlPath /f
if ($LASTEXITCODE -ne 0) {
    Write-Error "Gagal mendaftarkan task. Jalankan ulang sebagai administrator."
    exit 1
}

Write-Output "Task 'IndodaxV1Shadow' berhasil didaftarkan (S4U: jalan tanpa login)"
