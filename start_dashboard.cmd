@echo off
cd C:\Users\KakiTangan\trading-bot
python -m streamlit run dashboard/app.py --server.headless true --server.port 8501 --server.address 0.0.0.0