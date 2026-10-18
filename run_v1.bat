@echo off
REM Bot semi-otomatis mode bayangan (periode shadow) — read-only, tanpa order.
REM Dipanggil oleh Windows Task Scheduler "IndodaxV1Shadow".
cd /d "C:\Users\KakiTangan\trading-bot"
"C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\pythonw.exe" -u main_loop.py >> "C:\Users\KakiTangan\trading-bot\v1_loop.log" 2>&1
