# Arsip

Berkas di folder ini **tidak lagi menjadi bagian dari bot aktif**. Disimpan
untuk riwayat dan kemungkinan pemulihan manual.

| Berkas | Alasan diarsipkan |
|---|---|
| `dca_tracker.py` | Alat DCA manual terpisah; bot sudah punya DCA di `dca_window.py`/`rules.py`. `dca.db` tidak ada. |
| `migrate_dca.py` | Migrasi satu kali `dca.db` → `portfolio.db`; tidak diperlukan lagi. |
| `test_migrate_dca.py` | Test untuk `migrate_dca.py`; tidak dikoleksi pytest karena berada di luar `tests/`. |

Catatan:

- `dca_tracker.py` sudah diarahkan ke `price_feed.indodax_price()`; jika
  dipulihkan, jalankan dari root proyek agar import `price_feed` dan
  `indodax_api` tersedia.
- Modul arbitrage mean-reversion (`scanner.py`, `strategy_mean_reversion.py`,
  `paper_trader.py`, `backtest_mr.py`, `monitor.py`, `analyzer.py`,
  `trades_store.py`, `report.py`) **dihapus permanen** sesuai keputusan,
  bukan diarsipkan di sini. Riwayatnya tetap tersedia di Git.
