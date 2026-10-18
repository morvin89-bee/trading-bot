' Peluncur tanpa jendela untuk bot shadow V1 - dengan ulangan otomatis.
'
' Tiga lapis pengaman dari yang paling dalam:
' 1. main_loop menangkap galat denyut/polling dan TIDAK mati (kode utama).
' 2. ULANGAN DI SINI: bila proses bot tetap mati dengan galat, jeda 60 dtk
'    lalu nyalakan lagi – maksimal 3 ulangan (total 4 percobaan). Mandiri,
'    tidak bergantung Task Scheduler: pada kasus 2026-10-18 RestartOnFailure
'    ternyata TIDAK menyala walau XML task sudah benar, dan bot mati 42
'    menit sampai diperiksa manual.
' 3. Task Scheduler RestartOnFailure tetap terpasang sebagai jaring terakhir;
'    kode keluar galat ikut dibawa lewat WScript.Quit sehingga tugas
'    berstatus GAGAL (bukan sukses semu).
'
' WScript MENUNGGU seluruh rantai selesai, sehingga MultipleInstances
' IgnoreNew tetap menjaga satu-satunya pemilik getUpdates.
' Setiap kematian dan ulangan dicatat jujur ke v1_loop.log.
Const TERMINAL = "C:\Users\KakiTangan\trading-bot"
logfile = TERMINAL & "\v1_loop.log"
cmd = "cmd /c pythonw -u """ & TERMINAL & "\main_loop.py"" >> """ & logfile & """ 2>&1"

Set sh = CreateObject("WScript.Shell")

Sub Catat(teks)
    Set fso = CreateObject("Scripting.FileSystemObject")
    Set f = fso.OpenTextFile(logfile, 8, True)
    f.WriteLine teks
    f.Close
End Sub

k = 0
kode = 0
Do
    kode = sh.Run(cmd, 0, True)
    k = k + 1
    If kode = 0 Then Exit Do   ' keluar bersih: tidak diulang
    If k > 3 Then Exit Do      ' sudah 4 percobaan: berhenti, serahkan ke jaring terakhir
    Catat "Bot mati (kode " & kode & ") - ulangan ke-" & k & " dalam 60 dtk..."
    WScript.Sleep 60000
Loop
WScript.Quit kode
