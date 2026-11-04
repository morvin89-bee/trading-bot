# Laporan Penghapusan Subsistem Arbitrage Mean-Reversion

Tanggal: 4 November 2026  
Cakupan: `C:\Users\KakiTangan\trading-bot`  
Branch: `refactor/core-standalone`  
Commit baseline: `145e2f3 refactor: make core a facade over production modules`  
Baseline test: **347 passed**

Metode: pembacaan kode, penelusuran import dengan `grep`, analisis graf Graphify, dan koleksi test dengan `pytest --collect-only`.

---

## 1. Ringkasan Eksekutif

Subsistem **arbitrage mean-reversion** adalah subsistem riset terpisah dari **bot portofolio semi-otomatis**. Menghapusnya **layak dan aman**, asalkan empat titik kopling ditangani lebih dulu:

1. **`portfolio.fetch_price()` memakai `scanner.indodax_price()`** — bot semi-otomatis kehilangan harga jika `scanner.py` dihapus begitu saja.
2. **`dashboard/app.py` adalah dashboard mean-reversion**, tetapi juga menampung tab **Portofolio** dan **Shadow** milik bot semi-otomatis.
3. **`core/strategy.py` dan `core/__init__.py` mengekspor `MeanReversionStrategy`** — facade harus ikut diubah.
4. **Windows Task Scheduler `IndodaxMonitor` menjalankan `monitor.py`** — task harus dilepas sebelum file dihapus.

Rekomendasi: **hapus bertahap**, bukan satu kali `git rm`. Mulai dengan memisahkan price feed, lalu hapus kode MR, lalu rapikan dashboard, test, dan task scheduler.

Dampak ukuran:

| Ukuran | Sebelum | Sesudah (perkiraan) |
|---|---:|---:|
| Baris kode MR-specific | ~3.800 | 0 |
| Test pytest | 347 | ~326 + 2 test price feed = **~328** |
| File MR-specific | ~30 | 0 |
| Dashboard | 755 baris (MR) | ~20 baris (2 tab semi-otomatis) |

---

## 2. Definisi Lingkup

### 2.1 Yang termasuk arbitrage mean-reversion (dihapus)

| Kelompok | File |
|---|---|
| Scanner cross-exchange | `scanner.py` |
| Strategi z-score | `strategy_mean_reversion.py` |
| Paper trading / backtest | `paper_trader.py`, `backtest_mr.py` |
| Koleksi data pasar | `monitor.py`, `analyzer.py`, `trades_store.py` |
| Laporan paper trading | `report.py`, `signals.csv`, `demo_signals.csv`, `laporan.html` |
| Konfigurasi strategi | `config_strategy.yaml` |
| Dashboard MR | `dashboard/app.py` (isi MR), `dashboard/utils/data_loader.py`, `dashboard/utils/background_updater.py`, `dashboard/utils/shared_state.py`, `dashboard/utils/strategy_wrapper.py`, `dashboard/config/dashboard.yaml` |
| Test MR | `tests/test_rate_limit.py`, `tests/test_trades_store.py`, `tests/test_updater_singleton.py`, dan test root-level: `test_time_exit.py`, `test_trailing_stop.py`, `test_partial_close.py`, `test_background_updater.py`, `test_bg_updater2.py`, `test_shared_state.py` |
| Artefak test | `test_bg.json`, `test_bg2.json`, `test_dashboard_state.json`, `test_partial.json` |
| Task monitor | `start_monitor.ps1`, `start_monitor.vbs`, `register_task.ps1` |

### 2.2 Yang wajib dipertahankan (bot semi-otomatis)

`main_loop.py`, `engine.py`, `rules.py`, `safeguards.py`, `spread.py`, `executor.py`, `proposal.py`, `telegram_gate.py`, `portfolio.py`, `portfolio_store.py`, `scheduler.py`, `shadow_log.py`, `reconcile.py`, `dca_window.py`, `alerts.py`, `growth_report.py`, `actions_merge.py`, `indodax_api.py`, `indodax_transport.py`, `dashboard/portfolio_tab.py`, `dashboard/shadow_tab.py`, serta test semi-otomatis.

**Catatan penting:** `spread.py` **bukan** bagian arbitrage. Itu gerbang spread orderbook Indodax untuk eksekusi semi-otomatis. Jangan dihapus.

### 2.3 Perlu keputusan (bukan MR murni, tetapi bergantung pada scanner)

| File | Status | Rekomendasi |
|---|---|---|
| `dca_tracker.py` | Alat DCA manual, bukan MR | Jika masih dipakai: arahkan import ke `price_feed`. Jika tidak: arsipkan. `dca.db` saat ini **tidak ada**. |
| `migrate_dca.py` | Migrasi satu kali `dca.db` → `portfolio.db` | Simpan hanya jika masih ada cadangan `dca.db`; jika tidak, arsipkan bersama `dca_tracker.py`. |
| `tests/test_migrate_dca.py` | Test migrasi DCA | Ikut keputusan `migrate_dca.py`. |

---

## 3. Peta Dependensi

### 3.1 Alur yang hilang jika MR dihapus

```text
scanner.py ──> dashboard/app.py
     │              └──> data_loader / background_updater / strategy_wrapper
     ├──> paper_trader.py ──> signals.csv ──> report.py
     ├──> backtest_mr.py
     ├──> monitor.py ──> trades_store.py ──> analyzer.py
     ├──> dca_tracker.py
     └──> portfolio.fetch_price()  ← SATU-SATUNYA kaitan ke bot produksi

strategy_mean_reversion.py ──> paper_trader / backtest_mr / dashboard
     └──> core/strategy.py ──> core/__init__.py ──> tests/test_core_facade.py
```

### 3.2 Tabel dependensi terperinci

| Modul | Dipakai oleh | Tindakan |
|---|---|---|
| `scanner.py` | `portfolio.py`, `dca_tracker.py`, `monitor.py`, `paper_trader.py`, `backtest_mr.py`, `dashboard/app.py`, `dashboard/utils/background_updater.py`, `tests/test_rate_limit.py` | Ganti dengan `price_feed.py` untuk kebutuhan harga; hapus sisanya |
| `strategy_mean_reversion.py` | `core/strategy.py`, `paper_trader.py`, `backtest_mr.py`, dashboard, test root-level | Hapus |
| `trades_store.py` | `monitor.py`, `analyzer.py`, `backtest_mr.py`, `dashboard/utils/data_loader.py`, `tests/test_trades_store.py` | Hapus |
| `dashboard/app.py` | Launcher `start_dashboard.ps1`, `start_dashboard.cmd`, `buka_dashboard.bat` | Tulis ulang minimal; nama file tetap |
| `dashboard/utils/*` | `dashboard/app.py`, test root-level | Hapus |
| `config_strategy.yaml` | `strategy_mean_reversion.py`, `paper_trader.py`, `backtest_mr.py`, dashboard | Hapus |
| `monitor.py` | Task `IndodaxMonitor`, `start_monitor.*`, `register_task.ps1` | Lepas task, hapus file |
| `report.py` | Manual (`python report.py`) | Hapus |
| `dca_tracker.py` | Manual (`python dca_tracker.py ...`) | Keputusan |

---

## 4. Titik Kopling Kritis

### 4.1 `portfolio.fetch_price()` — kopling paling berbahaya

**Lokasi:** `portfolio.py:197-219`

```python
def fetch_price(pair):
    ...
    import scanner
    res = scanner.indodax_price(pair)
    if res and res[0] is not None:
        harga = res[0]
        _PRICE_CACHE[pair] = (now, harga)
        return harga
    ...
```

Fungsi ini dipakai oleh:

- `main_loop.baca_konteks()` — mengisi `ctx.prices` untuk **semua** pair.
- `main_loop.__main__` — `baca_harga=portfolio.fetch_price` untuk menyegarkan harga saat menjawab proposal.
- `dashboard/portfolio_tab.py` — lewat `portfolio.refresh()`.

Jika `scanner.py` dihapus tanpa pengganti, `baca_konteks()` akan gagal membaca harga → `ValueError` → **denyut dilewati terus-menerus** → bot tidak pernah mengusulkan apa pun.

**Perbaikan wajib:** buat modul kecil `price_feed.py`:

```python
"""Harga publik Indodax untuk bot semi-otomatis (read-only)."""
import time
import requests

from indodax_api import UA

URL = "https://indodax.com/api/ticker/{pair}"
_JEDA_MAKS = 5.0


def indodax_price(pair_id):
    """(bid, ask) atau None bila gagal. Tidak pernah raise."""
    try:
        r = requests.get(URL.format(pair=pair_id),
                         headers={"User-Agent": UA}, timeout=20)
        if r.status_code == 429:
            time.sleep(min(float(r.headers.get("Retry-After", 2)), _JEDA_MAKS))
            return None
        r.raise_for_status()
        t = r.json()["ticker"]
        return float(t["buy"]), float(t["sell"])
    except Exception:
        return None
```

Lalu ubah `portfolio.py`:

```python
from price_feed import indodax_price
...
res = indodax_price(pair)
```

Jika `dca_tracker.py` dipertahankan, ubah juga import-nya:

```python
from price_feed import indodax_price
...
bid, ask = indodax_price(pair)
```

**Test pengganti:** `tests/test_price_feed.py` minimal 2 test:
1. Respons ticker valid → `(bid, ask)` benar.
2. Gagal/429/JSON rusak → `None`, tidak raise.

### 4.2 `dashboard/app.py` — dashboard MR yang juga menampung tab bot

**Lokasi:** `dashboard/app.py:769-793`

```python
tab_names = ["Market", "Pair Detail", "Portfolio", "Portofolio", "Shadow", "History", "Control"]
```

Tab **Market**, **Pair Detail**, **Portfolio**, **History**, **Control** adalah MR/paper trading. Tab **Portofolio** dan **Shadow** memanggil modul terpisah:

- `dashboard/portfolio_tab.py` — portofolio nyata, read-only.
- `dashboard/shadow_tab.py` — status periode shadow.

**Rekomendasi:** ganti seluruh isi `dashboard/app.py` dengan dashboard minimal dua tab. Nama file dipertahankan agar `start_dashboard.ps1`, `start_dashboard.cmd`, dan `buka_dashboard.bat` tidak perlu diubah.

```python
"""Dashboard bot semi-otomatis (read-only)."""
import streamlit as st

st.set_page_config(page_title="Trading Bot", page_icon="📈", layout="wide")
st.sidebar.title("Navigasi")
pilihan = st.sidebar.radio("Pilih Tab", ["Portofolio", "Shadow"])

if pilihan == "Portofolio":
    from dashboard.portfolio_tab import render
    render()
else:
    from dashboard.shadow_tab import render
    render()
```

Dengan ini, `dashboard/utils/data_loader.py`, `background_updater.py`, `shared_state.py`, `strategy_wrapper.py`, dan `dashboard/config/dashboard.yaml` bisa dihapus.

### 4.3 `core/` facade — ekspor `MeanReversionStrategy`

**Lokasi:** `core/__init__.py`, `core/strategy.py`, `tests/test_core_facade.py`

`core/__init__.py` saat ini mengekspor:

```python
from .strategy import MeanReversionStrategy
```

`core/strategy.py` hanya shim ke `strategy_mean_reversion`. Setelah MR dihapus:

1. Hapus `core/strategy.py`.
2. Hapus import/ekspor `MeanReversionStrategy` dari `core/__init__.py`.
3. Perbarui `tests/test_core_facade.py` — hapus assertion `core.MeanReversionStrategy is MeanReversionStrategy`.
4. Test `test_main_is_lazy` tetap dipertahankan.

### 4.4 Windows Task Scheduler — `IndodaxMonitor`

`monitor.py` dijalankan oleh task **IndodaxMonitor** melalui `start_monitor.ps1`. Jika file dihapus tanpa melepas task:

- Task akan gagal berulang setiap boot/login.
- `monitor.log` dan `monitor_guard.log` terus bertambah.
- Tidak ada dampak ke bot semi-otomatis, tetapi operasional menjadi bising.

**Urutan aman:**

```powershell
# jalankan PowerShell sebagai administrator
Unregister-ScheduledTask -TaskName "IndodaxMonitor" -Confirm:$false
```

Setelah itu hapus/ubah:

- `start_monitor.ps1`, `start_monitor.vbs` — hapus.
- `register_task.ps1` — hapus (hanya mendaftarkan monitor).
- `register_support_tasks.ps1` — hapus bagian `IndodaxMonitor` dan perbaiki deskripsi `IndodaxDashboard` (tidak lagi menyebut monitor.py).
- `register_rotation_task.ps1` dan `rotate_errors_log.ps1` — hapus rotasi `monitor.log`, sisakan `errors.log`.
- `start_dashboard.ps1` — hanya perbaiki komentar; isinya tidak menjalankan monitor.

### 4.5 `requirements` dan konfigurasi

| File | Tindakan | Alasan |
|---|---|---|
| `requirements.txt` | Hapus `PyYAML>=6.0` | Tidak ada lagi modul yang memakai YAML setelah MR dihapus |
| `requirements-dashboard.txt` | Hapus `pyyaml`, `numpy`, `ydata-profiling` bila tidak dipakai | Dashboard baru hanya butuh `streamlit`, `plotly`, `pandas`, `watchdog` |
| `config_strategy.yaml` | Hapus | Hanya untuk strategi MR |
| `dashboard/config/dashboard.yaml` | Hapus | Hanya dibaca `data_loader.init_config()` milik MR |
| `pytest.ini` | Tidak berubah | `testpaths = tests` tetap |
| `conftest.py` | Tidak berubah | Guard jaringan tetap berguna |

---

## 5. Rencana Penghapusan Bertahap

### Fase 0 — Baseline dan pengaman

```powershell
cd C:\Users\KakiTangan\trading-bot
git switch -c refactor/remove-mean-reversion
python -m pytest -q --basetemp="$env:TEMP\pytest-baseline"
git tag before-remove-mr
```

Catat hasil baseline: **347 passed**.

### Fase 1 — Pisahkan price feed (wajib lebih dulu)

**File:**
- Tambah: `price_feed.py`
- Ubah: `portfolio.py` (import dan pemanggilan)
- Ubah (opsional): `dca_tracker.py`
- Tambah: `tests/test_price_feed.py`

**Verifikasi:**

```powershell
python -m pytest -q tests/test_price_feed.py tests/test_portfolio_refresh.py
python -c "import portfolio; print('price feed ok')"
```

Jangan lanjut sebelum test price feed hijau.

### Fase 2 — Hapus kode dan data MR

```powershell
git rm scanner.py strategy_mean_reversion.py paper_trader.py backtest_mr.py `
       monitor.py analyzer.py trades_store.py report.py `
       config_strategy.yaml signals.csv demo_signals.csv laporan.html
```

Hapus juga:

```text
dashboard/utils/data_loader.py
dashboard/utils/background_updater.py
dashboard/utils/shared_state.py
dashboard/utils/strategy_wrapper.py
dashboard/config/dashboard.yaml
```

### Fase 3 — Ganti dashboard

- Tulis ulang `dashboard/app.py` menjadi dua tab (lihat §4.2).
- Pertahankan `dashboard/portfolio_tab.py` dan `dashboard/shadow_tab.py`.
- Pastikan launcher tetap menunjuk `dashboard/app.py`.

**Verifikasi:**

```powershell
python -m py_compile dashboard/app.py
python -m pytest -q tests/test_portfolio_tab.py tests/test_shadow_tab.py
```

### Fase 4 — Hapus test dan task monitor

Hapus test:

```text
tests/test_rate_limit.py
tests/test_trades_store.py
tests/test_updater_singleton.py
test_time_exit.py
test_trailing_stop.py
test_partial_close.py
test_background_updater.py
test_bg_updater2.py
test_shared_state.py
test_bg.json
test_bg2.json
test_dashboard_state.json
test_partial.json
```

Hapus task script:

```text
start_monitor.ps1
start_monitor.vbs
register_task.ps1
```

Edit:

```text
register_support_tasks.ps1   # hapus bagian IndodaxMonitor
register_rotation_task.ps1   # hapus monitor.log
rotate_errors_log.ps1        # hapus monitor.log
start_dashboard.ps1          # perbaiki komentar
```

Lepas task Windows:

```powershell
Unregister-ScheduledTask -TaskName "IndodaxMonitor" -Confirm:$false
```

### Fase 5 — Rapikan `core/` dan requirements

- Hapus `core/strategy.py`.
- Edit `core/__init__.py`: hapus `MeanReversionStrategy`.
- Edit `tests/test_core_facade.py`: hapus assertion MR.
- Hapus `PyYAML` dari `requirements.txt`.
- Rapikan `requirements-dashboard.txt`.

### Fase 6 — Verifikasi dan commit

```powershell
python -m compileall -q core
python -m pytest -q --basetemp="$env:TEMP\pytest-final"
python -m core.main_loop --help
python core/main_loop.py --help
python -c "import core; assert not hasattr(core, 'MeanReversionStrategy')"
python -c "import portfolio; print(portfolio.fetch_price)"
graphify update . --force
git diff --stat
git status --short
```

Target: **~328 passed**, tidak ada import MR tersisa, dashboard dua tab bisa dibuka.

---

## 6. Dampak Test

Baseline: **347 test** (hanya `tests/` yang dikoleksi karena `pytest.ini`).

| File test | Jumlah | Tindakan |
|---|---:|---|
| `tests/test_rate_limit.py` | 11 | Hapus; ganti 2 test `price_feed` |
| `tests/test_trades_store.py` | 6 | Hapus |
| `tests/test_updater_singleton.py` | 4 | Hapus |
| **Subtotal dihapus** | **21** | |
| `tests/test_core_facade.py` | 2 | Edit 1 test; tetap 2 |
| **Perkiraan akhir** | **326 + 2 = 328** | |

Test root-level yang **tidak** ikut `pytest` default (tetapi tetap file tracked):

| File | Jumlah test |
|---|---:|
| `test_time_exit.py` | 5 |
| `test_trailing_stop.py` | 4 |
| `test_partial_close.py` | 2 |
| `test_background_updater.py` | 4 |
| `test_shared_state.py` | 7 |
| **Total** | **22** |

Test semi-otomatis yang **wajib tetap hijau**: `test_main_loop.py` (83), `test_engine.py` (34), `test_dca_window.py` (42), `test_safeguards.py` (14), `test_spread.py` (7), `test_portfolio_*`, `test_shadow_tab.py` (10), `test_portfolio_tab.py` (2).

---

## 7. Risiko dan Mitigasi

| Risiko | Dampak | Mitigasi |
|---|---|---|
| `portfolio.fetch_price` kehilangan sumber harga | Bot berhenti mengusulkan | Fase 1 wajib; test price feed; fallback cache tetap |
| Task `IndodaxMonitor` masih hidup | Task gagal berulang; log membengkak | Unregister task sebelum hapus `monitor.py` |
| Dashboard launcher rusak | Dashboard tidak bisa dibuka | Pertahankan nama `dashboard/app.py`; uji `py_compile` dan Streamlit |
| `core` facade masih mengekspor MR | Import error setelah file dihapus | Hapus `core/strategy.py`; update `__init__` dan test |
| `PyYAML` masih diimpor modul lain | `ModuleNotFoundError` | `grep -R "import yaml"` sebelum hapus dependency |
| Data MR masih dibutuhkan | Kehilangan `signals.csv` / `laporan.html` | Arsipkan ke `docs/archive/` bila perlu, bukan hapus permanen |
| `dca_tracker.py` masih dipakai | Alat DCA manual rusak | Arahkan ke `price_feed` atau arsipkan bersama `dca.db` |
| `portfolio.db` tidak sengaja terhapus | Kehilangan riwayat trading | Jangan sertakan `portfolio.db` dalam daftar hapus; sudah di-ignore Git |

---

## 8. Verifikasi Setelah Penghapusan

Checklist:

- [ ] `python -m pytest -q` → ~328 passed.
- [ ] `python -m compileall -q core` → OK.
- [ ] `python -m core.main_loop --help` → OK.
- [ ] `python core/main_loop.py --help` → OK.
- [ ] `python -c "import core"` → OK, tanpa `MeanReversionStrategy`.
- [ ] `python -m py_compile dashboard/app.py` → OK.
- [ ] `grep -R "strategy_mean_reversion\|scanner\|paper_trader\|backtest_mr\|trades_store"` → tidak ada di kode aktif (kecuali dokumentasi historis).
- [ ] `graphify update . --force` → jumlah node turun; tidak ada komunitas MR.
- [ ] Dashboard dua tab bisa dibuka dan menampilkan Portofolio + Shadow.
- [ ] Task `IndodaxMonitor` sudah tidak ada.
- [ ] `portfolio.db` tidak berubah.

---

## 9. Keputusan yang Diperlukan

| Keputusan | Pilihan | Rekomendasi |
|---|---|---|
| Price feed | Modul baru `price_feed.py` vs fungsi di `portfolio.py` | **Modul baru** — dipakai `portfolio.py` dan `dca_tracker.py` bila dipertahankan |
| Dashboard | Tulis ulang `app.py` vs file baru | **Tulis ulang `app.py`** — launcher tidak berubah |
| `dca_tracker.py` | Pertahankan + repoint vs arsipkan | **Arsipkan** jika `dca.db` tidak ada; jika dipakai, repoint ke `price_feed` |
| `migrate_dca.py` | Pertahankan vs arsipkan | Ikut keputusan `dca_tracker.py` |
| Data MR (`signals.csv`, `laporan.html`) | Hapus vs arsip | **Arsipkan** ke `docs/archive/` bila masih ingin menyimpan hasil paper trading |
| Dokumentasi | Perbarui `LAPORAN_ANALISIS.md` vs biarkan historis | Tambahkan catatan bahwa MR sudah dihapus; bagian lama tetap sebagai audit historis |

---

## 10. Rekomendasi Akhir

1. **Jangan hapus `spread.py`** — itu gerbang eksekusi bot semi-otomatis, bukan arbitrage.
2. **Jangan hapus `dashboard/portfolio_tab.py` dan `shadow_tab.py`** — itu dashboard bot yang masih dipakai.
3. **Lakukan Fase 1 lebih dulu** — pisahkan `price_feed.py`, buktikan test hijau, baru hapus `scanner.py`.
4. **Lepas task `IndodaxMonitor` sebelum menghapus `monitor.py`** — urutan ini mencegah task gagal berulang.
5. **Hapus bertahap dengan commit terpisah** — satu commit untuk price feed, satu untuk kode MR, satu untuk dashboard, satu untuk test/task. Lebih mudah di-review dan di-rollback.
6. **Target akhir:** bot semi-otomatis tetap utuh, dashboard hanya dua tab, ~3.800 baris kode MR hilang, dan test tetap hijau di sekitar 328.

---

## 11. Perintah Ringkas untuk Eksekusi

```powershell
cd C:\Users\KakiTangan\trading-bot
git switch -c refactor/remove-mean-reversion
python -m pytest -q --basetemp="$env:TEMP\pytest-baseline"

# Fase 1: price feed
# buat price_feed.py, ubah portfolio.py, tambah tests/test_price_feed.py
python -m pytest -q tests/test_price_feed.py tests/test_portfolio_refresh.py

# Fase 2: hapus kode MR
git rm scanner.py strategy_mean_reversion.py paper_trader.py backtest_mr.py monitor.py analyzer.py trades_store.py report.py config_strategy.yaml signals.csv demo_signals.csv laporan.html
git rm dashboard/utils/data_loader.py dashboard/utils/background_updater.py dashboard/utils/shared_state.py dashboard/utils/strategy_wrapper.py dashboard/config/dashboard.yaml

# Fase 3: tulis ulang dashboard/app.py (dua tab)

# Fase 4: hapus test + task
git rm tests/test_rate_limit.py tests/test_trades_store.py tests/test_updater_singleton.py test_time_exit.py test_trailing_stop.py test_partial_close.py test_background_updater.py test_bg_updater2.py test_shared_state.py test_bg.json test_bg2.json test_dashboard_state.json test_partial.json start_monitor.ps1 start_monitor.vbs register_task.ps1
Unregister-ScheduledTask -TaskName "IndodaxMonitor" -Confirm:$false

# Fase 5: core + requirements
git rm core/strategy.py
# edit core/__init__.py, tests/test_core_facade.py, requirements.txt, requirements-dashboard.txt

# Fase 6: verifikasi
python -m compileall -q core
python -m pytest -q --basetemp="$env:TEMP\pytest-final"
python -m core.main_loop --help
python core/main_loop.py --help
graphify update . --force
```
