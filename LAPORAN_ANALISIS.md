# Laporan Analisis — Trading Bot Core Standalone

Tanggal: 4 November 2026  
Cakupan: `C:\Users\KakiTangan\trading-bot` setelah pembersihan dan pembuatan `core/`  
Metode: pembacaan kode, uji impor/eksekusi, uji database salinan, dan pembaruan graf kode dengan Graphify (code-only, tanpa API key).

---

## 0. Status Eksekusi (4 November 2026)

Rekomendasi utama sudah dieksekusi:

- Branch baru: `refactor/core-standalone`.
- Semua file tracked yang sebelumnya terhapus dipulihkan dengan `git restore .`.
- `core/` sekarang menjadi **facade tipis** ke modul produksi:
  - `MeanReversionStrategy` → `strategy_mean_reversion`
  - `on_answer` → `engine`
  - `mark_pending` → `dca_window`
  - `main` → `main_loop` (lazy import)
- `requirements.txt` ditambah `PyYAML>=6.0`.
- Test suite: **347 passed**.
- `python -m core.main_loop --help` dan `python core/main_loop.py --help` berjalan.
- `python -m core.main_loop --siklus 0` berhenti dengan kode 1 karena Telegram belum diset — perilaku ini memang disengaja.

**Sisa risiko:** `core/` hanya facade. Perilaku produksi sepenuhnya bergantung pada modul asli. `portfolio.db` dan `graphify-out/` tetap tidak boleh di-commit.

Bagian 1–6 di bawah adalah analisis kondisi **sebelum** eksekusi, yaitu saat `core/` masih berisi salinan/demo. Bagian tersebut dipertahankan sebagai catatan audit.

---

## 1. Ringkasan Eksekutif (kondisi sebelum eksekusi)

`core/` sebelumnya **bisa diimpor** dan `python -m core.main_loop` bisa mencetak hasil demo. Namun `core/` saat itu **belum layak disebut bot trading yang runnable**:

- `core/main_loop.py` hanya menjalankan satu denyut simulasi dengan harga hardcoded.
- `core/engine.py` mengembalikan `"sent"` tanpa benar-benar mengirim order.
- `core/engine.py` tidak kompatibel dengan skema tabel `proposals` yang ada di `portfolio.db`.
- `core/strategy.py` gagal dibuat karena `config_strategy.yaml` sudah dihapus.
- `requirements.txt` tidak mencantumkan `PyYAML`, padahal strategi membutuhkannya.

Selain itu, file-file lama yang dihapus **masih tersimpan di Git** (`git status` menunjukkan status `D`). Artinya proyek asli bisa dipulihkan, dan refactor bisa dilakukan tanpa kehilangan kode.

**Kesimpulan saat itu:** kondisi tersebut adalah **prototipe/demo core**, bukan hasil refactor produksi.

---

## 2. Kondisi Saat Itu (sebelum eksekusi)

### Struktur

```text
trading-bot/
├── core/
│   ├── __init__.py
│   ├── dca_window.py
│   ├── engine.py
│   ├── main_loop.py
│   ├── strategy.py
│   └── utils.py
├── graphify-out/
├── portfolio.db
└── requirements.txt
```

### Graf kode saat itu

- 107 node, 172 edge, 17 komunitas.
- God node utama: `MeanReversionStrategy` (25 edge), `on_answer()` (10), `_bulan()` (10), `_baris()` (9), `main()` (8).
- Komunitas kecil dengan cohesion rendah: `__init__.py` 0.16, `main_loop.py` 0.25, `dca_window.py` 0.31.
- Tidak ada import cycle.

Graf saat itu menunjukkan `MeanReversionStrategy` sebagai pusat logika, tetapi `main_loop` dan `engine` terhubung secara artifisial lewat demo, bukan alur produksi.

---

## 3. Temuan Kritis (P0) — kondisi sebelum eksekusi

> Catatan: temuan di bagian ini berlaku untuk `core/` salinan/demo sebelum eksekusi. Setelah `core/` diubah menjadi facade, implementasi yang dipakai adalah modul produksi asli, sehingga masalah skema database dan `"sent"` palsu tidak lagi berasal dari `core/`.

### P0-1 — Engine tidak kompatibel dengan database nyata

**Lokasi:** `core/engine.py:29-32`

```python
CREATE TABLE IF NOT EXISTS proposals (id TEXT PRIMARY KEY, answer TEXT, note TEXT)
INSERT OR REPLACE INTO proposals VALUES (?, ?, ?)
```

Tabel `proposals` di `portfolio.db` memiliki 10 kolom:

```text
id, ts, day, kind, pair, amount_idr, price_ref, rule, outcome, note
```

Karena tabel sudah ada, `CREATE TABLE IF NOT EXISTS` tidak mengubah skema. Hasil uji pada salinan `portfolio.db`:

```text
OperationalError: table proposals has 10 columns but 3 values were supplied
```

**Dampak:** setiap jawaban yang mencoba mencatat hasil akan gagal. Engine tidak bisa dipakai dengan database proyek.

**Perbaikan:** gunakan skema `proposals` yang ada, atau buat fungsi migrasi/penulisan khusus. Jangan membuat tabel dengan skema berbeda.

---

### P0-2 — Mode fisik mengembalikan `"sent"` tanpa mengirim order

**Lokasi:** `core/engine.py:56-60`

```python
if shadow:
    _catat_hasil(...)
    return "sent"

return "sent"
```

Parameter `transport` diterima tetapi tidak pernah dipakai. Pada mode non-shadow, fungsi langsung mengembalikan `"sent"`.

**Dampak:** UI/loop bisa mengira order berhasil dikirim padahal tidak ada order apa pun. Ini berbahaya untuk keputusan trading.

**Perbaikan:** implementasikan eksekusi lewat `transport`, atau kembalikan status eksplisit seperti `"not_implemented"`/`"halted"` sampai eksekusi benar-benar ada. Jangan pernah memakai `"sent"` untuk order yang tidak dikirim.

---

### P0-3 — `main_loop` masih demo, bukan loop produksi

**Lokasi:** `core/main_loop.py:8-29`

- Hanya satu denyut simulasi.
- Harga `btc_idr` hardcoded `500000000.0`.
- `db_path`, `conn`, dan `telegram` tidak dipakai sebagaimana mestinya.
- `MeanReversionStrategy()` dibungkus `except Exception` lalu `strat = None`; kegagalan konfigurasi tidak terlihat.
- Selalu mengembalikan `0`.

**Dampak:** menjalankan `python -m core.main_loop` terlihat “berhasil”, tetapi tidak ada pemantauan pasar, tidak ada siklus, dan tidak ada keputusan nyata.

**Perbaikan:** pulihkan loop asli dari Git, atau pisahkan demo ini menjadi `core/demo.py` dan jangan sebut sebagai `main_loop`.

---

### P0-4 — Strategi tidak bisa dibuat karena konfigurasi hilang

**Lokasi:** `core/strategy.py:16-18`

```python
def __init__(self, config_path="config_strategy.yaml"):
    with open(config_path, encoding="utf-8") as f:
        self.cfg = yaml.safe_load(f)
```

`config_strategy.yaml` sudah dihapus. Hasil uji:

```text
FileNotFoundError: [Errno 2] No such file or directory: 'config_strategy.yaml'
```

Selain itu, `requirements.txt` hanya berisi:

```text
requests==2.32.3
python-dotenv==1.0.1
```

`PyYAML` tidak tercantum, sehingga instalasi bersih juga akan gagal.

**Perbaikan:** pulihkan `config_strategy.yaml`, tambahkan `PyYAML` ke requirements, dan sediakan nilai default yang aman bila config tidak ada.

---

## 4. Temuan Penting (P1)

### P1-1 — Import package memicu peringatan runpy

`core/__init__.py` mengimpor `main` dari `core.main_loop`. Saat menjalankan `python -m core.main_loop`, muncul:

```text
RuntimeWarning: 'core.main_loop' found in sys.modules after import of package 'core'
```

**Dampak:** perilaku eksekusi modul bisa tidak terduga.  
**Perbaikan:** gunakan lazy import atau keluarkan `main` dari `__init__.py`.

### P1-2 — Menjalankan file langsung gagal

```text
python core/main_loop.py
ImportError: attempted relative import with no known parent package
```

**Dampak:** entrypoint tidak ramah operator.  
**Perbaikan:** sediakan `python -m core.main_loop` sebagai satu-satunya cara, atau tambahkan bootstrap `if __package__ is None`.

### P1-3 — Audit DCA hilang

`core/dca_window.py` masih membangun objek `penanda`, tetapi pemanggilan `shadow_log.record(...)` diganti `pass`. Artinya kejadian `dca_missed` tidak lagi tercatat di audit log.

**Dampak:** kehilangan jejak audit untuk DCA yang terlewat.  
**Perbaikan:** pulihkan pencatatan, atau dokumentasikan bahwa audit memang tidak didukung di mode standalone.

### P1-4 — Position sizing dan daily limit belum benar

**Lokasi:** `core/strategy.py:264-291`

- `_calc_position_size()` mengembalikan `base_size_pct` (persentase), bukan nominal IDR.
- `check_daily_limit()` mengabaikan parameter `current_pnl_idr` dan memakai asumsi modal `2_000_000`.
- Komentar menyebut `placeholder count`.

**Dampak:** ukuran posisi dan batas kerugian harian bisa salah secara material.  
**Perbaikan:** definisikan modal aktual, konversi persentase ke IDR, dan gunakan P&L yang benar.

### P1-5 — Validasi harga kurang

**Lokasi:** `core/engine.py:51`

```python
abs(harga_sekarang - p.price_ref) / p.price_ref
```

Jika `price_ref = 0`, muncul `ZeroDivisionError`. Harga negatif/NaN juga tidak ditolak.

**Perbaikan:** validasi `price_ref > 0` dan harga finite sebelum dipakai.

### P1-6 — Guard database tidak lengkap

`_sudah_dijawab()` langsung menjalankan `SELECT` dari `proposals`. Pada database baru/in-memory tanpa tabel, hasilnya:

```text
OperationalError: no such table: proposals
```

**Perbaikan:** pastikan skema dibuat/dimigrasikan sebelum engine dipakai, atau tangani error dengan status `"halted"`.

---

## 5. Temuan Sedang (P2)

1. **Tidak ada test otomatis** untuk core. Yang ada hanya uji manual.
2. **Tidak ada CI** yang menjalankan compile/import/test.
3. **`requirements.txt` tidak lengkap** (`PyYAML`, `pytest`, dan dependensi lain yang pernah dipakai).
4. **`portfolio.db` tidak terlacak Git** (`?? portfolio.db`), sehingga rawan tidak sengaja ter-commit atau hilang.
5. **`graphify-out/` tidak masuk `.gitignore`** dan berpotensi ikut ter-commit.
6. **Cohesion komunitas rendah** (0.16–0.33), menandakan batas modul belum tegas. Namun karena korpus sekarang sangat kecil, ini belum menjadi prioritas utama.

---

## 6. Verifikasi yang Sudah Dilakukan

| Uji | Hasil |
|---|---|
| `python -m compileall -q core` | OK |
| `import core` | OK |
| `python -m core.main_loop` | Demo berjalan, mencetak `sent`, tetapi ada `RuntimeWarning` |
| `python core/main_loop.py` | Gagal: relative import |
| `MeanReversionStrategy()` | Gagal: `config_strategy.yaml` tidak ada |
| Engine dengan salinan `portfolio.db` | Gagal: skema `proposals` tidak cocok |
| Engine mode fisik | Mengembalikan `sent` tanpa transport |
| `price_ref = 0` | `ZeroDivisionError` |
| `dca_window.mark_pending()` + `dca_state()` | Berhasil (`awaiting`) |
| `dca_window.mark_missed()` | Berhasil mengubah status ke `missed` |
| Graphify update | 107 node, 172 edge, 17 komunitas |

---

## 7. Rekomendasi

### Prioritas 0 — hentikan risiko sekarang

1. **Jangan jalankan `core/` dengan uang nyata.** Anggap sebagai demo.
2. **Pulihkan proyek asli dari Git** di branch baru sebelum refactor:
   ```powershell
   git switch -c refactor/core-standalone
   git restore .
   ```
   Ini mengembalikan file yang terhapus tanpa menghapus folder `core/` yang belum terlacak.
3. **Perbaiki engine database** agar memakai skema `proposals` yang ada. Uji dengan salinan `portfolio.db`, bukan database in-memory saja.
4. **Hapus klaim `"sent"` palsu.** Ganti dengan status `"not_implemented"` atau implementasikan transport yang benar.

### Prioritas 1 — buat core benar-benar runnable

5. **Pulihkan atau buat `config_strategy.yaml`** dan tambahkan `PyYAML` ke `requirements.txt`.
6. **Ganti `main_loop` demo** dengan loop asli, atau pisahkan menjadi `demo.py`.
7. **Perbaiki entrypoint**: gunakan `python -m core.main_loop`, dan hindari import `main` di `__init__.py`.
8. **Pulihkan audit DCA** atau dokumentasikan batasannya.
9. **Perbaiki position sizing dan daily limit** sebelum strategi dipakai.

### Prioritas 2 — kualitas dan keamanan

10. **Tambahkan test minimal**:
    - engine menulis ke skema `proposals` nyata;
    - engine menolak `price_ref <= 0`;
    - mode fisik tidak pernah mengembalikan `sent` tanpa transport;
    - `MeanReversionStrategy` bisa dibuat dari config;
    - transisi DCA `awaiting → confirmed/missed`.
11. **Tambahkan CI**: `compileall`, import smoke test, dan `pytest`.
12. **Masukkan `portfolio.db` dan `graphify-out/` ke `.gitignore`** atau kelola secara eksplisit.

---

## 8. Rekomendasi Arsitektur

Jangan mempertahankan `core/` sebagai salinan terpisah dari modul asli. Itu menyebabkan perilaku menyimpang dan duplikasi.

Pilihan yang disarankan:

1. **Pulihkan modul asli** dari Git.
2. Buat `core/` sebagai **facade** yang hanya mengekspor API stabil:
   ```python
   from engine import on_answer
   from strategy_mean_reversion import MeanReversionStrategy
   ```
3. Refactor internal secara bertahap, dengan test yang menjaga perilaku.
4. Baru setelah test hijau, pindahkan implementasi ke `core/` dan hapus modul lama.

Dengan cara ini, “core berdiri sendiri” berarti **API yang stabil**, bukan **salinan kode yang kehilangan dependensi**.

---

## 9. Keputusan yang Disarankan

| Pilihan | Cocok bila | Konsekuensi |
|---|---|---|
| **A. Pulihkan dari Git lalu refactor bertahap** (disarankan) | Ingin bot tetap berfungsi | Perlu waktu, tetapi risiko paling rendah |
| **B. Lanjutkan `core/` sebagai proyek baru** | Hanya butuh prototipe/demo | Engine, loop, config, dan test harus ditulis ulang |
| **C. Arsipkan `core/`** | Tidak ada rencana menjalankan bot | Paling aman, tetapi kehilangan pekerjaan |

**Rekomendasi akhir:** pilih **A**. File asli masih ada di Git, jadi tidak ada alasan mempertahankan `core/` yang sudah kehilangan perilaku produksi.

---

## 10. Kondisi Setelah Eksekusi

- `core/` sekarang hanya facade: tidak ada salinan implementasi produksi.
- `core/__init__.py` memakai lazy import untuk `main`, sehingga `python -m core.main_loop` tidak memicu peringatan runpy.
- `core/main_loop.py` mendukung dua cara jalan:
  - `python -m core.main_loop --help`
  - `python core/main_loop.py --help`
- `requirements.txt` sudah memuat `PyYAML>=6.0`.
- Test suite: **347 passed**.
- Graf kode terbaru: **1742 node, 4306 edge, 85 komunitas** (89% EXTRACTED, 11% INFERRED).
- God node teratas: `_fresh_db()`, `_ts()`, `_TelegramPerekam`, `mark_pending()`, `on_answer()`, `MeanReversionStrategy`, `main()`.

**Status:** rekomendasi utama selesai. Pekerjaan berikutnya adalah refactor internal bertahap dengan test yang sudah ada sebagai jaring pengaman.
