@echo off
rem ============================================
rem  Dashboard Trading - sekali klik
rem  Double-click berkas ini. Jendela kecil "Dashboard
rem  Trading" akan muncul MINIMAL - jangan ditutup
rem  selama ingin dashboard tetap hidup. Browser
rem  terbuka otomatis begitu server siap (awal kali
rem  bisa 1-3 menit). Ulangi klik kapan saja - aman,
rem  tidak akan membuat server kedua.
rem  Deteksi memakai curl (bawaan Windows 10/11).
rem ============================================
setlocal
cd /d "%~dp0"

rem Server sudah berjalan? Cukup buka browser.
curl -s -o NUL --max-time 2 http://localhost:8501
if not errorlevel 1 (
    echo Dashboard sudah berjalan - membuka browser.
    start "" "http://localhost:8501"
    ping -n 4 127.0.0.1 >nul
    exit /b 0
)

echo Menyalakan dashboard... tunggu 1-3 menit; browser terbuka otomatis.
start "Dashboard Trading" /min cmd /c "python -m streamlit run dashboard\app.py --server.headless true --server.port 8501"

rem Tunggu server siap, maksimal sekitar 5 menit, lalu buka browser.
rem Jeda memakai ping - ping tidak butuh konsol interaktif (timeout butuh).
set /a coba=0
:ulang
curl -s -o NUL --max-time 2 http://localhost:8501
if not errorlevel 1 (
    start "" "http://localhost:8501"
    exit /b 0
)
set /a coba+=1
if %coba% lss 150 (
    ping -n 3 127.0.0.1 >nul
    goto ulang
)

echo Server belum siap dalam 5 menit. Periksa apakah Python terpasang.
pause
