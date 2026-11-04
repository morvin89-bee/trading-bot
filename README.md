# Trading Bot — Indodax (semi-otomatis)

Bot portofolio **semi-otomatis** untuk Indodax. Data dibaca *read-only*;
bot mengusulkan aksi lewat Telegram, dan **order hanya dikirim setelah kamu
menekan tombol Setuju**. Secara bawaan bot berjalan dalam **mode bayangan**
(shadow) dan tidak mengirim order.

## Fitur

- Pemantauan portofolio dan harga dari Indodax (read-only).
- Usulan DCA, rebalance, dan take-profit lewat Telegram dengan tombol konfirmasi.
- Pengaman: batas order, gerbang spread orderbook, rekonsiliasi saldo,
  deteksi izin API, dan kunci satu instance.
- Mode bayangan minimal 7 hari bersih sebelum sakelar live bisa dinyalakan.
- Dashboard Streamlit dua tab: **Portofolio** dan **Shadow**.
- Pencatatan setoran/pembelian manual lewat CLI.

## Struktur singkat

| Berkas | Peran |
|---|---|
| `main_loop.py` | Denyut utama: rekonsiliasi, polling Telegram, usulan, gerbang shadow |
| `engine.py` | Membuat proposal dan memproses jawaban tombol |
| `rules.py`, `safeguards.py`, `spread.py` | Aturan DCA/rebalance/take-profit dan pengaman eksekusi |
| `executor.py`, `indodax_transport.py` | Jalur order (hanya aktif setelah live) |
| `portfolio.py`, `portfolio_store.py`, `price_feed.py` | Data portofolio, SQLite, dan harga publik |
| `indodax_api.py` | TAPI Indodax (baca saldo & riwayat) |
| `alerts.py`, `telegram_gate.py` | Pengiriman pesan dan tombol Telegram |
| `scheduler.py`, `shadow_log.py`, `reconcile.py`, `dca_window.py` | Snapshot harian, log shadow, rekonsiliasi, jendela DCA |
| `dashboard/app.py` | Dashboard dua tab (read-only) |
| `tests/` | Test suite (tanpa jaringan) |

Subsistem arbitrage mean-reversion sudah dihapus. Alat DCA lama diarsipkan di
`docs/archive/` dan bukan bagian dari bot aktif.

## Persiapan

1. Python 3.11 atau lebih baru (diuji di Python 3.14).
2. Pasang dependensi:

   ```powershell
   pip install -r requirements.txt
   pip install -r requirements-dashboard.txt   # hanya untuk dashboard
   ```

3. Salin `.env.example` menjadi `.env`, lalu isi:

   ```text
   INDODAX_KEY=...          # izin Reading (View Only)
   INDODAX_SECRET=...
   TELEGRAM_TOKEN=...
   TELEGRAM_CHAT_ID=...
   ```

   **Jangan pernah** memberi izin Withdraw pada API key. Biarkan
   `INDODAX_CAN_TRADE` kosong selama masih mode bayangan.

## Menjalankan

```powershell
# Bot (mode bayangan)
python main_loop.py

# Uji cepat: jalankan N siklus lalu keluar
python -m core.main_loop --siklus 3

# Dashboard
buka_dashboard.bat
# atau:
python -m streamlit run dashboard\app.py --server.headless true --server.port 8501

# Snapshot harian + ringkasan Telegram
python scheduler.py --telegram

# Catat setoran / pembelian manual
python portfolio_cli.py deposit 500000 2026-10-01 --note "setoran Okt"
python portfolio_cli.py buy btc 275000 1600000000
python portfolio_cli.py status
```

## Mode bayangan dan go-live

- Bot mulai dalam mode bayangan; setiap penolakan mereset hitungan hari.
- Sakelar live hanya bisa naik setelah `GO_LIVE_SHADOW_DAYS` hari bayangan
  bersih (minimal 7).
- Saat memutuskan live: isi `INDODAX_CAN_TRADE=1` **bersamaan** dengan
  mengaktifkan live. Mengisinya lebih awal membuat bot berhenti di tiap denyut.
- `INDODAX_CAN_WITHDRAW` tidak boleh diisi `1`.

## Test

```powershell
python -m pytest -q
```

Test suite tidak membuka koneksi jaringan (dijaga `conftest.py`).

## Catatan operasional

- `portfolio.db` berisi riwayat portofolio dan **tidak** masuk Git.
- `.env` berisi rahasia dan **tidak** masuk Git.
- Skrip task Windows: `register_support_tasks.ps1`,
  `register_rotation_task.ps1`, `start_dashboard.ps1`.
- Log berputar otomatis lewat task "Rotasi Log" (`errors.log`).
