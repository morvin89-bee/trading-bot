# Rencana Eksekusi — Core Sebagai Facade

**Goal:** Memulihkan proyek asli dari Git dan menjadikan `core/` sebagai facade/API stabil, bukan salinan implementasi.

**Architecture:** File asli dikembalikan dari commit HEAD. `core/` hanya berisi shim tipis yang mengimpor API dari modul asli (`engine`, `main_loop`, `dca_window`, `strategy_mean_reversion`). Implementasi produksi tetap di modul asli.

**Tech Stack:** Python 3, Git, pytest.

**Spec:** `LAPORAN_ANALISIS.md`

## Global Constraints
- Jangan menyentuh `portfolio.db`.
- Jangan commit `graphify-out/` atau database.
- `core/` harus bisa diimpor dan `python -m core.main_loop` harus berjalan.
- Perilaku produksi tidak boleh diduplikasi di `core/`.

## Review Focus
- Import melingkar antara `core` dan modul asli.
- Perbedaan signature `main`/`on_answer`/`mark_pending`.
- `python -m core.main_loop` vs `python core/main_loop.py`.
- Test asli yang bergantung pada path modul.
- File yang tidak sengaja terhapus/tertimpa saat restore.

---

### Task 1: Pulihkan proyek dari Git

**Files:**
- Restore: semua file tracked yang berstatus `D`
- Branch: `refactor/core-standalone`

- [x] Buat branch baru.
- [x] Jalankan `git restore .`.
- [x] Verifikasi `git status` bersih untuk file tracked.
- [x] Pastikan `core/`, `portfolio.db`, `graphify-out/`, dan laporan tetap ada.

### Task 2: Ganti isi `core/` menjadi facade

**Files:**
- Modify: `core/__init__.py`
- Modify: `core/engine.py`
- Modify: `core/main_loop.py`
- Modify: `core/dca_window.py`
- Modify: `core/strategy.py`
- Keep: `core/utils.py`

**Interfaces:**
- `core.MeanReversionStrategy` -> `strategy_mean_reversion.MeanReversionStrategy`
- `core.on_answer` -> `engine.on_answer`
- `core.mark_pending` -> `dca_window.mark_pending`
- `core.main` -> lazy import dari `main_loop.main`

- [x] Tulis ulang shim.
- [x] Pastikan tidak ada import `main_loop` di `__init__.py` saat import awal.
- [x] Verifikasi `import core`.
- [x] Verifikasi `python -m core.main_loop`.

### Task 3: Verifikasi

- [x] `python -m compileall -q core`
- [x] `python -m pytest -q` (atau subset yang relevan bila ada test yang butuh jaringan)
- [x] `git diff --stat`
- [x] Catat hasil dan risiko.

### Task 4: Commit

- [x] `git add core/ .gitignore LAPORAN_ANALISIS.md` (sesuai kebutuhan)
- [x] Commit dengan pesan `refactor: make core a facade over production modules`.
