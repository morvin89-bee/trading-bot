# Task pendukung trading-bot:
#   - "IndodaxDashboard" : dashboard streamlit (port 8501)
#   - "Portofolio Snapshot" : snapshot harian 21:00, diubah ke S4U + StartWhenAvailable
#
# Kenapa S4U: "Interactive only" = task hanya jalan bila ada sesi login aktif.
# Kejadian nyata 2026-10-21: mesin mati 19:20, nyalakan 23:31 -> snapshot
# 21:00 terlewat, bot 4 jam 11 menit mati. S4U = jalan tanpa login, tanpa
# menyimpan password. Probe di mesin ini membuktikan S4U jalan.
#
# PENTING: skrip ini WAJIB elevated (PowerShell "Run as administrator").
# Tanpa hak admin, Principal S4U ditolak: "Access is denied".
#
# CATATAN: file ini harus ASCII polos. PowerShell 5.1 membaca .ps1 tanpa BOM
# sebagai ANSI; karakter non-ASCII (mis. em-dash UTF-8) berubah jadi
# smart-quote yang menutup string lebih awal -> parse error, skrip tak jalan.

$botDir = "C:\Users\KakiTangan\trading-bot"
$userSid = ([Security.Principal.WindowsIdentity]::GetCurrent()).User.Value

$settings = @"
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
"@

# Helper: buat XML task S4U dengan trigger startup + logon.
function New-S4UTaskXml($nama, $deskripsi, $action, $arguments) {
@"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>$deskripsi</Description>
    <URI>\$nama</URI>
  </RegistrationInfo>
  <Triggers>
    <BootTrigger>
      <Enabled>true</Enabled>
      <Delay>PT1M</Delay>
    </BootTrigger>
    <LogonTrigger>
      <Enabled>true</Enabled>
      <Delay>PT30S</Delay>
    </LogonTrigger>
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
      <Command>$action</Command>
      <Arguments>$arguments</Arguments>
      <WorkingDirectory>$botDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@
}

function Register-S4UTask($nama, $xml) {
    $xmlPath = Join-Path $env:TEMP "task_register_$($nama -replace '[^A-Za-z0-9]','_').xml"
    Set-Content -Path $xmlPath -Value $xml -Encoding Unicode
    schtasks /create /tn $nama /xml $xmlPath /f
    if ($LASTEXITCODE -ne 0) {
        Write-Error "Gagal mendaftarkan task '$nama'. Jalankan ulang sebagai administrator."
        exit 1
    }
    Remove-Item $xmlPath -ErrorAction SilentlyContinue
    Write-Output "Task '$nama' terdaftar (S4U: jalan tanpa login)"
}

# ---------- 1. IndodaxDashboard ----------
$xmlDash = New-S4UTaskXml `
    "IndodaxDashboard" `
    "Dashboard trading (streamlit 8501). S4U: jalan tanpa login." `
    "powershell.exe" `
    "-NoProfile -ExecutionPolicy Bypass -File `"$botDir\start_dashboard.ps1`""
Register-S4UTask "IndodaxDashboard" $xmlDash

# ---------- 2. Portofolio Snapshot -> S4U + StartWhenAvailable ----------
# Backup XML lama dulu; kalau pendaftaran gagal, pulihkan.
$backup = Join-Path $env:TEMP "snapshot_backup.xml"
schtasks /query /tn "Portofolio Snapshot" /xml 2>$null | Out-File -FilePath $backup -Encoding Unicode
if (-not (Test-Path $backup) -or (Get-Item $backup).Length -lt 100) {
    Write-Error "Gagal backup XML task 'Portofolio Snapshot'. Tidak ada perubahan."
    exit 1
}

$xmlSnap = @"
<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.3" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Snapshot portofolio harian 21:00 (read-only + ringkasan Telegram). S4U: jalan tanpa login. StartWhenAvailable: kalau 21:00 terlewat, jalan begitu mesin nyala.</Description>
    <URI>\Portofolio Snapshot</URI>
  </RegistrationInfo>
  <Triggers>
    <CalendarTrigger>
      <StartBoundary>2026-10-14T21:00:00</StartBoundary>
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
      <Command>$botDir\run_snapshot.bat</Command>
      <WorkingDirectory>$botDir</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"@

$xmlPath = Join-Path $env:TEMP "task_register_snapshot.xml"
Set-Content -Path $xmlPath -Value $xmlSnap -Encoding Unicode
schtasks /create /tn "Portofolio Snapshot" /xml $xmlPath /f
if ($LASTEXITCODE -ne 0) {
    Write-Error "Gagal mendaftarkan 'Portofolio Snapshot'. Memulihkan XML lama."
    schtasks /create /tn "Portofolio Snapshot" /xml $backup /f | Out-Null
    exit 1
}
Remove-Item $xmlPath -ErrorAction SilentlyContinue
Write-Output "Task 'Portofolio Snapshot' terdaftar (S4U + StartWhenAvailable)"
