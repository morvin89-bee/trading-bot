@echo off
REM Ambil snapshot portofolio harian (read-only) dan kirim ringkasan ke Telegram.
REM Dipanggil oleh Windows Task Scheduler "Portofolio Snapshot".
cd /d "C:\Users\KakiTangan\trading-bot"
"C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\python.exe" scheduler.py --telegram >> "C:\Users\KakiTangan\trading-bot\scheduler.log" 2>&1
