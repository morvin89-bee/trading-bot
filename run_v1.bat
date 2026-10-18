@echo off
REM Bot semi-otomatis mode bayangan (periode shadow) — read-only, tanpa order.
REM Jalur MANUAL (dobel-klik). Task Scheduler memakai run_v1_hidden.vbs
REM (tanpa jendela + ulangan otomatis). Bila bot sudah jalan, berkas ini
REM langsung ditolak kunci instance tunggal dan keluar dengan pesan jujur.
cd /d "C:\Users\KakiTangan\trading-bot"
"C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\pythonw.exe" -u main_loop.py >> "C:\Users\KakiTangan\trading-bot\v1_loop.log" 2>&1
