# Task "Rotasi Log": rotasi trading-bot\errors.log harian
# (simpan 10 salinan).
#
# Kenapa S4U: task harus jalan tanpa ada sesi login aktif. Principal S4U
# membutuhkan hak admin -> skrip ini WAJIB elevated (PowerShell "Run as
# administrator"). Tanpa admin: "Access is denied".
#
# File ini harus ASCII polos. PowerShell 5.1 membaca .ps1 tanpa BOM sebagai
# ANSI; karakter non-ASCII berubah jadi smart-quote -> parse error.
#
# Prinsip: kalau pendaftaran gagal, tidak ada perubahan pada task lain.
#
# Catatan: skrip yang dijalankan task adalah rotate_errors_log.ps1 (nama
# dipertahankan supaya task lama tetap valid tanpa re-register). Isinya
# merotasi errors.log.

$botDir = "C:\Users\KakiTangan\trading-bot"
$userSid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value

$settings = @"
  <Settings>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <ExecutionTimeLimit>PT5M</ExecutionTimeLimit>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <StartWhenAvailable>true</StartWhenAvailable>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <WakeToRun>false</WakeToRun>
    <UseUnifiedSchedulingEngine>true</UseUnifiedSchedulingEngine>
  </Settings>
"@

$xml = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Rotasi trading-bot\errors.log harian (simpan 10 salinan). S4U: jalan tanpa login.</Description>
    <URI>\Rotasi Log</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-10-22T04:30:00</StartBoundary>
      <Enabled>true</Enabled>
      <ScheduleByDay>
        <DaysInterval>1</DaysInterval>
      </ScheduleByDay>
    </CalendarTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <UserId>$userSid</UserId>
      <LogonType>S4U</LogonType>
    </Principal>
  </Principals>
$settings
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>-NoProfile -ExecutionPolicy Bypass -File "$botDir\rotate_errors_log.ps1"</Arguments>
      <WorkingDirectory>$botDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

$xmlPath = Join-Path $env:TEMP "task_register_rotasi_log.xml"
Set-Content -Path $xmlPath -Value $xml -Encoding Unicode
schtasks /create /tn "Rotasi Log" /xml $xmlPath /f
if ($LASTEXITCODE -ne 0) {
    Remove-Item $xmlPath -ErrorAction SilentlyContinue
    Write-Error "Gagal mendaftarkan task 'Rotasi Log'. Jalankan ulang sebagai administrator."
    exit 1
}
Remove-Item $xmlPath -ErrorAction SilentlyContinue
Write-Output "Task 'Rotasi Log' terdaftar (S4U + harian 04:30)"
