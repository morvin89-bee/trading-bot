# Rencana Implementasi DCA Bulanan (Task 15)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Menutup F45/F46: bot benar-benar mengusulkan DCA bulanan Rp 500.000 sesuai keputusan pengguna 2026-10-17, membuka jendela 24 jam hanya saat pesan terkirim, dan membatasi tombol Setuju/Batal hanya untuk usulan DCA.

**Architecture:** Satu sumber aksi baru di `rules.py` (murni, tanpa jaringan), didaftarkan di `engine.collect_actions` dengan provenance (F32) dan jendela `DCA_WINDOW_S`; kapan DCA boleh diusulkan diputuskan `dca_window.boleh_usul_dca` (punya `conn`), disuntikkan ke engine lewat `Context.dca_enabled`; penanda bulan ditulis `mark_pending`/`mark_skip` **setelah** pesan benar-benar dikirim atau gagal total.

**Tech Stack:** Python 3.14, sqlite3, pytest. Repo: `C:\Users\KakiTangan\trading-bot`, cabang `feat/semi-auto-execution`.

**Spec:** `docs/superpowers/specs/2026-10-17-dca-bulanan-task15-design.md` (addendum) + `docs/superpowers/specs/2026-10-15-semi-auto-execution-design.md` (utama). Baca addendum sebelum mengerjakan; rencana ini berargumen darinya.

## Global Constraints

- Tidak ada jaringan dari tes mana pun (guard `conftest.JaringanDibuka` — jangan dilemahkan).
- Tidak ada kunci dagang, penarikan, atau penempatan order baru di tugas ini; `executor` tidak disentuh.
- Order hanya `limit`; drift harga **0,5% tetap berlaku penuh untuk DCA** (`proposal.price_still_valid` tidak diubah).
- Jendela: reaktif 90 dtk (`REACTIVE_WINDOW_S`), DCA 24 jam (`DCA_WINDOW_S = 86400` — sudah ada di `proposal.py:18`).
- Atap per order `safeguards.MAX_ORDER_IDR = 275000`; pagu 3 order/aset/hari; jeda 1800 dtk; `MAX_SPREAD_BPS = 30`; `recvWindow 15000`; query status ≤3× backoff `[2,5,10]`; `canWithdraw==True` → halt.
- `ValueError` provenance → halt senyap (kontrak lama — jangan dilonggarkan).
- `time.localtime` saja. Bahasa Indonesia, bahasa sehari-hari, untuk alasan aksi dan pesan.
- 46 tes asli (root `test_*.py`) tak boleh tersentuh; baseline diukur ulang, bukan dihafal.
- Selalu jalankan tes dengan `--basetemp=.ptmp` (folder temp bawaan terkunci di Windows ini).
- Tes mutasi bukti wajib tiap task; kegagalan TDD harus benar-benar merah dulu.
- Pemanggilan: `python -m pytest -q --basetemp=.ptmp` dari `C:\Users\KakiTangan\trading-bot`.

## Review Focus

Lima hal paling mungkin menggigit pengguna, dan tes yang menguncinya:

1. **Harga aset hilang saat jam usul** → aksi tidak dibuat, uang tetap cadangan; dilarang mengarang `price_ref` (konfirmasi pasti hangus). Dikunci: `test_dca_actions_melewati_aset_tanpa_harga`.
2. **Percobaan DCA ditolak gerbang/spread** → bulan harus TUTUP sekali (`mark_skip`), bukan retry tiap 5 menit yang meracuni `shadow_day` selamanya, dan bukan `awaiting` bohong tanpa pesan terkirim. Dikunci: `test_dca_gagal_di gerbang_menutup_bulan_sekali` dan `test_mark_skip_tidak_menulis_dca_sent_ts`.
3. **Tabrakan `(kind, pair)` DCA vs reserve buy** → tanpa penyaringan, `ValueError` menghentikan SELURUH tick termasuk take-profit (halt senyap). Dikunci: `test_dca_mengalahkan_reserve_buy_pada_pasangan_sama`.
4. **Jendela melintasi batas bulan** → pengusulan tanggal 31 sore membuat jendela yang tak tercatat (pengingat mati, `dca_missed` tak pernah tertulis). Dikunci: `test_boleh_usul_menolak_jendela_lintas_bulan`.
5. **F46: tombol bersama** → Setuju/Batal pada usulan take-profit/reserve buy tidak boleh menyentuh jendela DCA. Dikunci: `test_setuju_take_profit_tidak_menutup_jendela_dca` + `test_batal_pada_usulan_bukan_dca_tidak_menyentuh_jendela`.

## Peta File

| File | Perubahan |
|---|---|
| `rules.py` | **Modifikasi**: tambah `MONTHLY_IDR`, `RULE_DCA`, fungsi `dca_actions`; default `reserve_buy_actions(monthly=...)` menunjuk konstanta |
| `dashboard/portfolio_tab.py:20` | **Modifikasi 1 baris**: `MONTHLY_IDR` menunjuk `rules.MONTHLY_IDR` (satu sumber angka) |
| `dca_window.py` | **Modifikasi**: tambah `boleh_usul_dca` dan `mark_skip` (+ komentar `STATUS_DCA`) |
| `engine.py` | **Modifikasi**: `Context.dca_enabled`, sumber DCA di `collect_actions`, cabang jendela DCA di `tick` |
| `main_loop.py` | **Modifikasi**: set `ctx.dca_enabled` di `tick_cycle`, `_catat_dca`, pembatasan F46 di `_jawab`, rekaman di `apply_ulang` |
| `tests/test_dca_actions.py` | **Baru**: tes `rules.dca_actions` + konstanta |
| `tests/test_engine_dca.py` | **Baru**: tes registrasi engine + jendela + tabrakan |
| `tests/test_dca_window.py` | **Modifikasi (tambahan)**: tes `boleh_usul_dca` + `mark_skip` |
| `tests/test_main_loop.py` | **Modifikasi**: 8 tes baru + **perbarui 2 tes lama** (baris 612 & 626) yang memakai id usulan tak dikenal |

---

### Task 15.1: Sumber aksi DCA di `rules.py`

**Files:**
- Modify: `rules.py` (tambah di akhir file, setelah `reserve_buy_actions`)
- Modify: `dashboard/portfolio_tab.py:20`
- Test: `tests/test_dca_actions.py` (baru)

**Interfaces:**
- Consumes: `rules.dca_split(amount_idr, holdings_value, target, min_order)` (sudah ada, `rules.py:18`), `rules.Action` (`rules.py:66`), `rules.DEFAULT_TARGET` (`rules.py:9`)
- Produces:
  - `rules.MONTHLY_IDR: int = 500000` — satu sumber angka setoran bulanan
  - `rules.RULE_DCA: str = "dca_bulanan"` — nama aturan untuk provenance (F32) dan gerbang F46
  - `rules.dca_actions(holdings, idr_free, prices, *, max_order, monthly=MONTHLY_IDR, target=None, min_order=10000) -> list[Action]` — daftar `Action(kind="BUY", ...)`; `max_order` **wajib** (pemanggil mengisi `safeguards.MAX_ORDER_IDR` — `rules` tidak boleh mengimpor `safeguards`, itu berputar)

- [ ] **Step 1: Tulis tes yang gagal**

Buat `tests/test_dca_actions.py`:

```python
import time

import portfolio
import rules


def test_konstanta_bulanan_satu_sumber():
    import dashboard.portfolio_tab as tab   # pola tests/test_portfolio_tab.py
    assert rules.MONTHLY_IDR == 500000
    assert tab.MONTHLY_IDR == rules.MONTHLY_IDR  # dashboard menyalin dari rules
    assert rules.RULE_DCA == "dca_bulanan"


def test_dca_actions_membagi_setoran_sesuai_bobot():
    aksi = rules.dca_actions(
        [], 0.0,
        {"btc_idr": 1_000_000.0, "eth_idr": 500_000.0, "sol_idr": 100_000.0},
        max_order=275_000)
    assert [(a.pair, a.amount_idr) for a in aksi] == [
        ("btc_idr", 275_000.0), ("eth_idr", 125_000.0), ("sol_idr", 50_000.0)]
    assert [a.qty for a in aksi] == [0.275, 0.25, 0.5]
    assert all(a.kind == "BUY" and a.executable for a in aksi)
    assert all(a.price_ref > 0 for a in aksi)


def test_dca_actions_memotong_di_batas_tanpa_membagi_sisa():
    # Portofolio miring berat ke ETH+SOL -> `dca_split` menggeser porsi ke
    # BTC sampai ~423.000; atap 275.000 memotongnya, dan sisa TIDAK berpindah.
    holdings = [
        portfolio.Holding("eth_idr", "eth", 4.0, 400_000.0, 500_000.0,
                          2_000_000.0, 1_600_000.0, 400_000.0, True),
        portfolio.Holding("sol_idr", "sol", 3.0, 300_000.0, 400_000.0,
                          1_200_000.0, 900_000.0, 300_000.0, True),
    ]
    aksi = rules.dca_actions(
        holdings, 0.0,
        {"btc_idr": 1_000_000.0, "eth_idr": 500_000.0, "sol_idr": 100_000.0},
        max_order=275_000)
    assert [(a.pair, a.amount_idr) for a in aksi] == [("btc_idr", 275_000.0)]


def test_dca_actions_melewati_aset_tanpa_harga():
    aksi = rules.dca_actions([], 0.0, {"btc_idr": 1_000_000.0},
                             max_order=275_000)
    assert [a.pair for a in aksi] == ["btc_idr"]   # ETH & SOL tanpa harga: dilewati


def test_dca_actions_kosong_bila_semua_ke_cadangan():
    assert rules.dca_actions([], 0.0, {}, monthly=15_000,
                             max_order=275_000) == []


def test_dca_actions_alasan_indonesia_menyebut_dca():
    aksi = rules.dca_actions([], 0.0, {"btc_idr": 1_000_000.0,
                                       "eth_idr": 500_000.0,
                                       "sol_idr": 100_000.0},
                             max_order=100_000)  # memaksa pemotongan
    # Ketiga aset punya harga -> TIGA aksi keluar (bukan satu).
    assert len(aksi) == 3
    for a in aksi:
        assert "DCA" in a.reason and a.executable
    a = aksi[0]                              # BTC 275.000 -> terpotong 100.000
    assert a.pair == "btc_idr" and a.amount_idr == 100_000.0
    assert "cadangan" in a.reason            # hanya yang terpotong menyebut sisa
    assert "cadangan" not in aksi[2].reason   # SOL 50.000 < 100.000, tak terpotong
```

Catatan `portfolio.Holding` adalah positional `(pair, coin, qty, avg_cost, price, value_idr, cost_basis_idr, unrealized_idr, basis_known)` — sesuaikan urutannya dengan `portfolio.py:71-80` saat menulis (periksa dulu, jangan tebak).

- [ ] **Step 2: Jalankan, pastikan merah**

Run: `python -m pytest -q --basetemp=.ptmp tests/test_dca_actions.py`
Expected: FAIL — `AttributeError: module 'rules' has no attribute 'MONTHLY_IDR'` (dan tes lainnya ikut merah karena fungsi belum ada).

- [ ] **Step 3: Implementasi**

Di `rules.py`:
- Tambah `MONTHLY_IDR = 500000` dan `RULE_DCA = "dca_bulanan"` dekat `DEFAULT_TARGET`, dengan komentar: ini satu-satunya rumah angka bulanan; `dashboard` menyalin dari sini.
- Ubah default `reserve_buy_actions(..., monthly=500000, ...)` menjadi `monthly=MONTHLY_IDR` (nilai identik, nol perubahan perilaku).
- Tambah `dca_actions` sesuai Interfaces. Tubuh inti:
  1. `nilai = {h.pair: h.value_idr for h in holdings}`; `nilai["IDR"] = idr_free`.
  2. `alokasi = dca_split(monthly, nilai, target, min_order=min_order)`.
  3. Untuk tiap `pair` di `target` selain `"IDR"` dengan `alokasi > 0`: ambil `harga = prices.get(pair)`; **lewati bila harga kosong/nol** (tanpa harga yang jujur, `price_ref` tidak bisa diisi — konfirmasi pasti hangus); `jumlah = min(alokasi, max_order)`; buat `Action(kind="BUY", pair, jumlah, qty=jumlah/harga, price_ref=harga, reason, executable=True)`.
  4. Alasan berbahasa Indonesia: `"DCA bulanan: beli sesuai bobot target"`; bila terpotong, tambahkan `" (dipotong pada batas per order, sisa tetap cadangan)"`.

Di `dashboard/portfolio_tab.py:20`: ganti literal `MONTHLY_IDR = 500000` menjadi `MONTHLY_IDR = rules.MONTHLY_IDR` (modul sudah mengimpor `rules`).

- [ ] **Step 4: Jalankan, pastikan hijau + regresi**

Run: `python -m pytest -q --basetemp=.ptmp tests/test_dca_actions.py tests/test_rules_dca.py tests/test_rules_takeprofit.py tests/test_rules_rebalance.py tests/test_portfolio_tab.py`
Expected: semua PASS (tes lama `dca_split`/dashboard tidak boleh berubah hasil).

- [ ] **Step 5: Bukti mutasi**

Ubah sementara, jalankan tes, pastikan TERTANGKAP, kembalikan:
1. `jumlah = min(nominal, max_order)` → `jumlah = nominal` → `test_..._memotong_di_batas...` gagal.
2. Hapus syarat harga (`harga = prices.get(pair) or 1.0`) → `test_..._melewati_aset_tanpa_harga` gagal.
3. `MONTHLY_IDR = 600000` → `test_konstanta_bulanan_satu_sumber` + `test_..._membagi_setoran...` gagal.

Catat hasil di laporan (nama mutasi, tes yang menangkap).

- [ ] **Step 6: Commit**

```bash
git add rules.py dashboard/portfolio_tab.py tests/test_dca_actions.py
git commit -m "feat(dca): sumber aksi DCA bulanan di rules.py (F45 bagian A)"
```

---

### Task 15.2: Predikat jadwal dan penanda "bulan dilewati" di `dca_window.py`

**Files:**
- Modify: `dca_window.py` (tambah dua fungsi publik + perbarui komentar `STATUS_DCA` di sekitar baris 30-35)
- Test: `tests/test_dca_window.py` (tambahan di akhir file)

**Interfaces:**
- Consumes: `_baris(conn)`, `_bulan(ts)`, `DCA_SEND_HOUR`, `DCA_WINDOW_H`, `mark_pending`, `dca_state`, `should_remind` (semua sudah ada)
- Produces:
  - `dca_window.boleh_usul_dca(conn, now_ts) -> bool` — dipakai `main_loop.tick_cycle`
  - `dca_window.mark_skip(conn, ts) -> bool` — "percobaan bulan ini gagal diusulkan; bulan ditutup" (menulis `last_dca_date` + `dca_status='batal'`, **tanpa** `dca_sent_ts`)

- [ ] **Step 1: Tulis tes yang gagal**

Tambahkan di `tests/test_dca_window.py` (pakai helper `_ts`/`_fresh_db` pola modul ini; untuk lintas bulan, bangun timestamp eksplisit — helper `_ts` memakai hari 1-28):

```python
def test_boleh_usul_menunggu_jam_kirim_di_tanggal_1():
    conn = _fresh_db(tmp_path)
    assert dca_window.boleh_usul_dca(conn, _ts(day=1, hour=8, minute=59)) is False
    assert dca_window.boleh_usul_dca(conn, _ts(day=1, hour=9)) is True


def test_boleh_usul_tutup_setelah_bulan_ditandai(tmp_path):
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(day=1, hour=9))
    assert dca_window.boleh_usul_dca(conn, _ts(day=1, hour=9, minute=5)) is False


def test_boleh_usul_tutup_setelah_dikonfirmasi_walaupun_order_gagal(tmp_path):
    """Keputusan pengguna 2026-10-17: Setuju ditekan, order gagal keluar
    sekalipun, bulan ini SELESAI — tidak pernah ditawarkan ulang."""
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(day=1, hour=9))
    dca_window.mark_confirmed(conn, _ts(day=1, hour=9, minute=10))
    assert dca_window.boleh_usul_dca(conn, _ts(day=1, hour=20)) is False


def test_boleh_usul_telat_di_tengah_bulan_diautukan(tmp_path):
    """Bot mati di tanggal 1 -> begitu hidup lagi tanggal 5, tetap boleh."""
    conn = _fresh_db(tmp_path)
    assert dca_window.boleh_usul_dca(conn, _ts(day=5, hour=10)) is True


def test_boleh_usul_menolak_jendela_lintas_bulan(tmp_path):
    conn = _fresh_db(tmp_path)
    des_sore = time.mktime(time.struct_time(
        (2026, 12, 30, 12, 0, 0, 0, 0, -1)))   # +24 jam = 31 Des, masih Des
    des_akhir = time.mktime(time.struct_time(
        (2026, 12, 31, 12, 0, 0, 0, 0, -1)))   # +24 jam = 1 Januari
    assert dca_window.boleh_usul_dca(conn, des_sore) is True
    assert dca_window.boleh_usul_dca(conn, des_akhir) is False


def test_mark_skip_menutup_bulan_tanpa_membuka_jendela(tmp_path):
    conn = _fresh_db(tmp_path)
    now = _ts(day=1, hour=9, minute=5)
    assert dca_window.mark_skip(conn, now) is True
    assert dca_window.dca_state(conn, now) == ("none", 0.0)
    # Tidak ada jendela -> pengingat 15.00 tidak menyala, +24 jam tidak
    # mencatat dca_missed palsu.
    sore = _ts(day=1, hour=15)
    assert dca_window.should_remind(conn, sore) is False
    assert dca_window.dca_state(conn, sore) == ("none", 0.0)


def test_mark_skip_menolak_bila_bulan_sudah_ada(tmp_path):
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(day=1, hour=9))
    assert dca_window.mark_skip(conn, _ts(day=1, hour=9, minute=5)) is False
    assert dca_window.dca_state(conn, _ts(day=1, hour=10))[0] == "awaiting"


def test_mark_skip_tidak_mengarang_waktu_kirim(tmp_path):
    """`dca_sent_ts` harus tetap NULL — tidak ada pesan yang pernah terkirim."""
    conn = _fresh_db(tmp_path)
    dca_window.mark_skip(conn, _ts(day=1, hour=9, minute=5))
    baris = conn.execute(
        "SELECT dca_sent_ts FROM bot_state WHERE id = 1").fetchone()
    assert baris[0] is None
```

- [ ] **Step 2: Jalankan, pastikan merah**

Run: `python -m pytest -q --basetemp=.ptmp tests/test_dca_window.py`
Expected: FAIL — `AttributeError: ... no attribute 'boleh_usul_dca'` / `'mark_skip'`.

- [ ] **Step 3: Implementasi**

Di `dca_window.py`, tambah dua fungsi publik:

- `boleh_usul_dca(conn, now_ts) -> bool`, empat syarat semuanya wajib:
  1. `baris[0] != _bulan(now_ts)` — bulan ini belum tersentuh (jendela terkirim/batal/terkonfirmasi/terlewat semuanya menutup peluang);
  2. tanggal 1 menunggu `DCA_SEND_HOUR` (`tm_mday == 1 and tm_hour < DCA_SEND_HOUR` → False);
  3. `_bulan(now_ts + DCA_WINDOW_H * 3600) == _bulan(now_ts)` — jendela 24 jam dari sekarang harus berakhir di bulan yang sama (kabel T-13: lintas-bulan tidak tercatat);
  4. DND tidak perlu diperiksa di sini — `tick_cycle` hanya berjalan jam aktif.
- `mark_skip(conn, ts) -> bool`: NO-OP + False bila `baris[0] == _bulan(ts)` (bulan sudah ada — jangan menimpa jendela yang sedang awaiting); selain itu tulis `last_dca_date = _bulan(ts)`, `dca_status = 'batal'`, commit, True. **Jangan sentuh `dca_sent_ts`.** Dokumentasikan di docstring: inilah "bulan dilewati" untuk percobaan yang gagal diusulkan — `dca_state` memperlakukannya sebagai "none" (tanpa jendela, tanpa pengingat, tanpa catatan missed), dan alasan `mark_batal` biasa tidak bisa dipakai di sini (ia menolak baris yang memegang bulan lalu).
- Perbarui komentar `STATUS_DCA` (baris 30-35): daftar penulis jadi lima — `mark_pending / mark_confirmed / mark_missed / mark_batal / mark_skip`.

- [ ] **Step 4: Jalankan, pastikan hijau + regresi**

Run: `python -m pytest -q --basetemp=.ptmp tests/test_dca_window.py`
Expected: semua PASS, termasuk tes lama Task 13 (gerbang T-13.2…T-13.7 tidak berubah).

- [ ] **Step 5: Bukti mutasi**

1. Hapus syarat (1) di `boleh_usul_dca` → `test_boleh_usul_tutup_setelah_bulan_ditandai` gagal.
2. Hapus syarat (2) → `test_boleh_usul_menunggu_jam_kirim_di_tanggal_1` gagal.
3. Hapus syarat (3) → `test_boleh_usul_menolak_jendela_lintas_bulan` gagal.
4. Di `mark_skip`, tambahkan `dca_sent_ts = ts` → `test_mark_skip_tidak_mengarang_waktu_kirim` gagal.
5. Di `mark_skip`, hapus penolakan `baris[0] == bulan` → `test_mark_skip_menolak_bila_bulan_sudah_ada` gagal.

- [ ] **Step 6: Commit**

```bash
git add dca_window.py tests/test_dca_window.py
git commit -m "feat(dca): boleh_usul_dca dan mark_skip — jadwal sekali per bulan"
```

---

### Task 15.3: DCA masuk `engine` — provenance, tabrakan, dan jendela 24 jam

**Files:**
- Modify: `engine.py` (`Context` baris 96-107; `collect_actions` baris 110-143; `tick` baris 303-309)
- Test: `tests/test_engine_dca.py` (baru)

**Interfaces:**
- Consumes: `rules.dca_actions`, `rules.RULE_DCA` (Task 15.1), `safeguards.MAX_ORDER_IDR`, `proposal.DCA_WINDOW_S`
- Produces:
  - `engine.Context` bertambah field **terakhir**: `dca_enabled: bool = False` (default False menjaga semua tes lama tetap hijau)
  - `collect_actions(ctx)`: bila `ctx.dca_enabled`, sumber DCA ikut dihitung dan didaftarkan; kaki `reserve_buy` untuk `(kind, pair)` yang sudah diklaim DCA **dilewati**
  - `tick(...)`: `window_s = proposal.DCA_WINDOW_S if rule == rules.RULE_DCA else proposal.REACTIVE_WINDOW_S`

- [ ] **Step 1: Tulis tes yang gagal**

Buat `tests/test_engine_dca.py`. Pola holding/ctx ikuti `tests/test_engine.py` (helper di sana — jangan menyalin literal yang mustahil). Inti tes:

```python
def test_collect_actions_tanpa_sakelar_tidak_menghasilkan_dca():
    # ctx baku (dca_enabled False) -> tidak ada satu pun aksi "dca_bulanan"
    # meski harga dan saldo tersedia. Regresi perilaku lama.

def test_collect_actions_menghasilkan_dca_dengan_provenance():
    # ctx.dca_enabled=True, holdings=[], prices terisi, free_idr=600000
    # -> aksi BUY btc/eth/sol ada; engine.rule_name(aksi) == "dca_bulanan"
    #    (bukan ValueError — inilah bukti provenance F32).

def test_dca_mengalahkan_reserve_buy_pada_pasangan_sama():
    # Holding btc jatuh >=15% dari rata-rata (reserve buy ikut berebut
    # BUY btc_idr) + ctx.dca_enabled=True -> TANPA ValueError, tepat SATU
    # aksi BUY btc_idr keluar, _nama_aturan == rules.RULE_DCA.

def test_take_profit_tetap_paling_depan():
    # DCA aktif + ada SELL take-profit -> merged[0].kind == "SELL"
    # (kontrak urutan merge_actions).

def test_tick_memberi_jendela_24_jam_untuk_dca():
    # jalankan engine.tick dengan ctx.dca_enabled=True dan telegram perekam
    # -> usulan ber-rule "dca_bulanan" punya window_s == 86400;
    #    usulan rule lain tetap 90.
```

- [ ] **Step 2: Jalankan, pastikan merah**

Run: `python -m pytest -q --basetemp=.ptmp tests/test_engine_dca.py`
Expected: FAIL — `TypeError: Context.__init__ unexpected keyword 'dca_enabled'` / aksi DCA tidak ada.

- [ ] **Step 3: Implementasi**

Di `engine.py`:
1. `Context`: tambah `dca_enabled: bool = False` sebagai field terakhir, dengan komentar: sakelar per-siklus dari `main_loop` (yang punya `conn`); `collect_actions` tidak menerima koneksi.
2. `collect_actions`: setelah daftar take-profit, bila `ctx.dca_enabled` tambahkan `[(rules.RULE_DCA, a) for a in rules.dca_actions(ctx.holdings, ctx.free_idr, ctx.prices, max_order=safeguards.MAX_ORDER_IDR)]`. Sebelum membuat daftar reserve buy, hitung `dibeli = {(a.kind, a.pair) for nama, a in sumber if nama == rules.RULE_DCA}` dan saring reserve buy dengan `if (a.kind, a.pair) not in dibeli` — **di `collect_actions`, bukan di `reserve_buy_actions`** (non-goal: reserve buy tidak diubah). Pendaftaran `_catat_asal` tetap untuk semua sumber. Urutan: take-profit → DCA → reserve buy (kontrak merge: SELL pertama).
3. `tick` baris ~307: ganti literal `window_s=proposal.REACTIVE_WINDOW_S` menjadi `window_s=(proposal.DCA_WINDOW_S if rule == rules.RULE_DCA else proposal.REACTIVE_WINDOW_S)`. Komentari: DCA adalah janji 24 jam (spec §7.2);90 detik akan membuat hampir semua Setuju hangus.

- [ ] **Step 4: Jalankan, pastikan hijau + regresi**

Run: `python -m pytest -q --basetemp=.ptmp tests/test_engine_dca.py tests/test_engine.py tests/test_safeguards.py tests/test_actions_merge.py`
Expected: semua PASS (220+ hijau; tes lama engine tidak berubah hasil karena `dca_enabled` bawaan False).

- [ ] **Step 5: Bukti mutasi**

1. Hapus penyaringan reserve buy → `test_dca_mengalahkan_reserve_buy_pada_pasangan_sama` gagal (ValueError dari `_catat_asal`).
2. Kembalikan `window_s=proposal.REACTIVE_WINDOW_S` tanpa cabang → `test_tick_memberi_jendela_24_jam_untuk_dca` gagal.
3. Hilangkan sakelar (`if ctx.dca_enabled` dihapus / selalu True) → `test_collect_actions_tanpa_sakelar_tidak_menghasilkan_dca` gagal.
4. Ganti `rules.RULE_DCA` jadi `"dca"` di satu sisi pendaftaran saja → tes provenance gagal (peta ≠ nama).

- [ ] **Step 6: Commit**

```bash
git add engine.py tests/test_engine_dca.py
git commit -m "feat(dca): daftarkan sumber DCA di engine dengan jendela 24 jam (F45/F32)"
```

---

### Task 15.4: Wiring loop utama — jendela hanya saat terkirim, dan F46

**Files:**
- Modify: `main_loop.py` (impor `dataclasses` + `rules`; `tick_cycle` baris ~310, ~363-377; `_jawab` baris 218-227; `apply_ulang` baris 385-395; fungsi baru `_catat_dca`)
- Modify: `tests/test_main_loop.py` (8 tes baru + **perbarui 2 tes lama** baris 612-623 dan 626-637)
- Consumes: `dca_window.boleh_usul_dca`, `dca_window.mark_skip`, `dca_window.mark_pending` (Task 15.2), `rules.RULE_DCA`, `Context.dca_enabled` (Task 15.3)
- Produces: `_catat_dca(conn, now, usulan)` (privat); `tick_cycle` mengisi `ctx.dca_enabled` tiap siklus; `_jawab` hanya memanggil `mark_confirmed`/`mark_batal` untuk `usulan.rule == rules.RULE_DCA`

- [ ] **Step 1: Tulis tes yang gagal**

Tambahkan di `tests/test_main_loop.py` (pakai `_ts`, `_fresh_db`, `_TelegramPerekam`, `_FetchPerekam`, `_satu_siklus`, `_ok_verdict` yang ada). Siapkan helper lokal `_ctx_dca(free_idr=600000.0)` berisi `prices` untuk btc/eth/sol (harga bulat wajar, bukan float mustahil):

1. `test_siklus_pertama_bulan_mengusulkan_dca_dan_membuka_jendela` — `_satu_siklus` pada `_ts(day=1, hour=9, minute=5)` dengan `_ctx_dca()` → hasil `"proposed"`; baris `bot_state`: `last_dca_date == "%Y-%m"` bulan itu, `dca_status == "awaiting"`, `dca_sent_ts == now`; `telegram.konfirmasi` memuat proposal `rule == rules.RULE_DCA` dengan `window_s == 86400`.
2. `test_siklus_berikutnya_tidak_mengusulkan_dca_lagi` — jalankan siklus kedua 5 menit kemudian dengan ctx sama → tidak ada konfirmasi DCA baru.
3. `test_dca_gagal_di_gerbang_menutup_bulan_sekali` — `verdict` selalu `(False, 99.0)`; siklus pertama (jam 09.05) → `last_dca_date` bulan itu, `dca_status == 'batal'`, `dca_sent_ts` NULL; **siklus kedua** → tidak ada baris `spread_rejected` baru di `shadow_log` (tidak ada percobaan ulang yang meracuni shadow day).
4. `test_setuju_take_profit_tidak_menutup_jendela_dca` — `mark_pending(conn, now)`; `main_loop._ingat([p])` dengan `p` ber-`rule="take_profit"`; tekan Setuju via `_FetchPerekam` → `dca_state(...) [0] == "awaiting"` (F46).
5. `test_setuju_dca_menutup_jendela` — sama, tapi `p` ber-`rule=rules.RULE_DCA`, `window_s=proposal.DCA_WINDOW_S` → `"confirmed"`.
6. `test_batal_pada_usulan_bukan_dca_tidak_menyentuh_jendela` — `mark_pending`; tekan Batal pada proposal take-profit → jendela tetap `"awaiting"`; **tidak** ada pesan "Bulan ini sudah selesai" (tombol itu bukan urusan DCA).
7. `test_ulang_pada_jam_usul_dca_mencatat_jendela` — `_update_teks("/ulang")` pada jam 09.05, ctx DCA → konfirmasi DCA terkirim lewat `apply_ulang` DAN `bot_state` ikut tercatat `awaiting` (engine.tick siklus dilewati, tetapi penanda tetap tertulis).
8. **Perbarui dua tes lama**: `test_tombol_batal_menolak_tahu_bila_bulan_sudah_selesai` (baris 612) dan `test_jawaban_setuju_menandai_dca_bulan_ini_confirmed` (baris 626) memakai id usulan tak dikenal; setelah F46, jawaban untuk id tak dikenal tidak boleh menyentuh jendela (id tak dikenal = tidak tahu jenis usulannya = ragu = tidak menulis). Daftarkan proposal sungguhan ber-`rule=rules.RULE_DCA` via `main_loop._ingat([...])` sebelum polling, dan pakai id itu. Ini **perubahan perilaku yang disengaja** (F46), bukan pelonggaran tes.

- [ ] **Step 2: Jalankan, pastikan merah**

Run: `python -m pytest -q --basetemp=.ptmp tests/test_main_loop.py`
Expected: 8 tes baru merah; dua tes lama ikut merah setelah diperbarui.

- [ ] **Step 3: Implementasi**

Di `main_loop.py`:
1. Impor `dataclasses` dan `rules`.
2. Fungsi privat baru:
```python
def _catat_dca(conn, now, usulan):
    """Buka atau tutup bulan DCA setelah satu tick — HANYA bila disebut.

    Usulan DCA di daftar berarti pesan benar-benar terkirim (`engine.tick`
    hanya mengembalikan yang terkirim) -> jendela resmi dibuka. DCA aktif
    tapi nol usulan terkirim = percobaan sudah habis -> bulan ditutup
    (keputusan pengguna 2026-10-17: sekali coba, gagal = dilewati).
    """
    if any(p.rule == rules.RULE_DCA for p in usulan):
        dca_window.mark_pending(conn, now)
    else:
        dca_window.mark_skip(conn, now)
```
3. `tick_cycle`: tepat setelah resolusi `ctx`, isi sakelar per-siklus:
   `ctx = dataclasses.replace(ctx, dca_enabled=dca_window.boleh_usul_dca(conn, now))`.
   Setelah `_ingat(usulan)`: `if ctx.dca_enabled and not diulang: _catat_dca(conn, now, usulan)` — guard `not diulang` mencegah pencatatan ganda, karena `/ulang` sudah mencatat di `apply_ulang`.
4. `apply_ulang`: setelah `_ingat(engine.tick(...))`, `if ctx.dca_enabled: _catat_dca(conn, now, usulan)`, lalu return seperti biasa.
5. `_jawab` (F46): **geser pencarian usulan ke atas**, sebelum dua tombol:
```python
usulan = _USULAN_MENUNGGU.get(id_usulan)
if aksi == telegram_gate.BUTTON_SETUJU:
    if usulan is not None and usulan.rule == rules.RULE_DCA:
        dca_window.mark_confirmed(conn, now)
elif aksi == telegram_gate.BUTTON_BATAL:
    if usulan is not None and usulan.rule == rules.RULE_DCA:
        if not dca_window.mark_batal(conn, now):
            telegram.line("Bulan ini sudah selesai, Batal bulan ini tidak berlaku.")
```
   sisanya (pesan id tak dikenal, cek transport, `on_answer`) tidak berubah urutannya. Id tak dikenal = tidak ada sentuhan jendela.

- [ ] **Step 4: Jalankan, pastikan hijau + regresi**

Run: `python -m pytest -q --basetemp=.ptmp`
Expected: SELURUH suite hijau (38 tes lama + 8 baru, dua yang diperbarui disesuaikan).

- [ ] **Step 5: Bukti mutasi**

1. Hapus gerbang `usulan.rule == rules.RULE_DCA` pada Setuju → `test_setuju_take_profit_tidak_menutup_jendela_dca` gagal.
2. Ganti `mark_skip` jadi `mark_pending` di `_catat_dca` → `test_dca_gagal_di_gerbang_menutup_bulan_sekali` gagal (status `awaiting` bohong + `dca_sent_ts` terisi).
3. Pindahkan `mark_pending` ke **sebelum** `engine.tick` (jendela dibuka walau pesan gagal kirim) → tes 1 tidak menangkap langsung; ganti dengan mutasi: di `tick_cycle`, panggil `_catat_dca` tanpa guard `ctx.dca_enabled` → `test_siklus_berikutnya_tidak_mengusulkan_dca_lagi` gagal (siklus kedua ikut menulis). Jalankan dan catat mana yang benar-benar tertangkap; bila ada mutasi lolos, PERBAIKI tesnya sebelum commit.
4. Hapus guard `not diulang` → jalankan `test_ulang_pada_jam_usul_dca_mencatat_jendela`; bila lolos, tambahkan tes yang mengunci (siklus dengan `/ulang` + `mark_pending` yang sudah tercatat tidak menimpa `dca_sent_ts` dengan waktu berbeda).

- [ ] **Step 6: Commit**

```bash
git add main_loop.py tests/test_main_loop.py
git commit -m "feat(dca): wiring jendela DCA per siklus + batasi tombol DCA (F45/F46)"
```

---

### Task 15.5: Capstone integrasi + sapu regresi

**Files:**
- Test: `tests/test_main_loop.py` (1 tes capstone)

- [ ] **Step 1: Tulis tes capstone yang gagal**

`test_dca_konfirmasi_menghasilkan_satuan_order_end_to_end`: siklus jam 09.05 mengusulkan DCA (shadow) → user menekan Setuju (update tombol, id dari `telegram.konfirmasi`) pada siklus berikutnya dengan `shadow=False`, `transport=_TransportPerekam(status="FILLED")`, `verdict=_ok_verdict` → assert: `transport.posted` tepat satu order dengan payload `limit` dan `client_order_id` == id proposal; baris `order_status` tertulis dengan `status="filled"` dan `rule == rules.RULE_DCA`; `dca_state` == `"confirmed"`; tidak ada order kedua untuk jawaban ganda.

- [ ] **Step 2: Jalankan, pastikan merah lalu implement minimal**

Run: `python -m pytest -q --basetemp=.ptmp tests/test_main_loop.py::test_dca_konfirmasi_menghasilkan_satuan_order_end_to_end`
Expected: merah dulu; bila seluruh wiring Task 15.1-15.4 sudah benar, tes ini seharusnya **hijau tanpa mengubah produksi** — kalau perlu produksi berubah, hentikan dan tanyakan (jangan menekuk aturan untuk memaksa hijau).

- [ ] **Step 3: Sapu regresi penuh**

```bash
python -m pytest -q --basetemp=.ptmp
python -m pytest -q --basetemp=.ptmp --ignore=tests/test_main_loop.py --ignore=tests/test_dca_actions.py --ignore=tests/test_engine_dca.py
git diff --stat main
```
Expected: suite penuh semua hijau; baseline **diukur ulang** (bandingkan dengan ledger 181 + tes baru yang sah — jangan menghafal); `git diff` pada root `test_*.py` asli **kosong** (46 tes tak tersentuh).

- [ ] **Step 4: Commit + minta review independen**

```bash
git add tests/test_main_loop.py
git commit -m "test(dca): capstone end-to-end DCA bulanan"
```

Lalu dispatch reviewer independen (bukan implementer) dengan paket diff HEAD~5..HEAD, ledger `.superpowers/sdd/2026-10-15-semi-auto-execution/progress.md` (baca F45/F46), dan addendum desain. Perbarui `tasks.md` (baris Task 15) dan `progress.md` (ruling baru F47… bila ada temuan) setelah review.

---

## Catatan eksekusi (bukan tugas terpisah)

- Lingkungan: shell default PowerShell; jalankan perintah pytest apa adanya (tanpa redir `2>&1` yang rapuh); Python `C:\Users\KakiTangan\AppData\Local\Programs\Python\Python314\python.exe` dari root repo.
- Kelas defek berulang di repo ini: literal tes yang tidak bisa gagal (float mustahil, `nan==nan`, dict kosong, identifier berspasi) — verifikasi tiap literal; **jangan menekuk produksi** untuk memaksa tes hijau.
- Keputusan pengguna yang mengikat (addendum §Angka): drift 0,5% tetap; sekali coba tanggal 1 (gagal = dilewati); order gagal setelah Setuju = bulan selesai; reserve buy hanya dari sisa cadangan.
