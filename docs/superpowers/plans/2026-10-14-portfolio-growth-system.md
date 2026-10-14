# Portfolio Growth System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Membangun sistem read-only di Indodax yang melacak portofolio, memandu DCA bulanan, memberi peringatan take-profit berbasis harga rata-rata, dan melaporkan pertumbuhan — tanpa pernah bisa mengirim order.

**Architecture:** Lapisan data (`portfolio_store.py` + `portfolio.py`) membaca saldo & riwayat dari `indodax_api.py`, menggabungkannya dengan `dca.db` dan entri manual ke satu SQLite `portfolio.db`. Logika murni (`rules.py`) menghasilkan aksi BELI/JUAL/TAHAN; `alerts.py` + `growth_report.py` menyajikannya; tab Streamlit mandiri menampilkannya.

**Tech Stack:** Python 3, SQLite (stdlib), requests, PyYAML, Streamlit, pytest.

**Spec:** `docs/superpowers/specs/2026-10-14-portfolio-growth-system-design.md`

## Global Constraints

- Exchange: **hanya Indodax**. Tidak ada Tokocrypto.
- **100% read-only** — hanya `account_info()` dan `my_trades()`; tidak boleh ada pemanggilan order.
- Fee Indodax: **taker 0,2% / maker 0,1%**. Minimum order **Rp 10.000**.
- Aset & bobot target: `btc_idr` 0,55; `eth_idr` 0,25; `sol_idr` 0,10; `IDR` 0,10.
- Setoran bulanan target: **Rp 500.000**.
- Pemicu take-profit dievaluasi **neto setelah fee taker** memakai **harga bid** (`scanner.indodax_price(pair)[0]`).
- Tidak ada leverage/margin. UI berbahasa Indonesia. Dasbor tetap dapat diakses via Tailscale.
- Cakupan aset yang dilacak: BTC, ETH, SOL, BCH (debu). Koin lain diabaikan.
- **Batas v1:** saran take-profit bersifat **kumulatif & tanpa status** — sistem menampilkan "posisi yang seharusnya sudah terjual" pada level tertinggi yang terlewati; sistem tidak melacak level mana yang sudah benar-benar dieksekusi (pengguna mencatatnya manual lewat penjualan).

## Review Focus

Perilaku yang dimaksudkan spec tapi tidak otomatis teruji oleh fitur utama — masing-masing diberi tes di task pemiliknya:

1. **Koin tanpa riwayat beli** (debu BCH): harus tampil `basis_known=False`, bukan crash dan bukan 0% (Task 5).
2. **Dua bentuk respons API** (v1 `return.balance`/`balance_rp` vs v2 `balances` list) dengan banyak saldo nol/debu: parsing menerima keduanya dan memfilter nol (Task 4).
3. **Kuantitas jual > beli** karena reward staking menambah saldo: harga rata-rata tidak boleh negatif/crash (Task 5).
4. **Posisi di bawah minimum order** saat take-profit/DCA: aksi tidak boleh dikeluarkan sebagai perintah mustahil (Task 7, 8).
5. **Harga tak tersedia / HTTP 429** dari ticker: dasbor tidak crash; nilai aset dianggap 0 dan ditandai `n/a` (Task 11).

---

### Task 1: Bootstrap repo & test harness

**Files:**
- Create: `.gitignore`
- Create: `requirements-dev.txt`
- Create: `pytest.ini`

**Interfaces:**
- Consumes: —
- Produces: lingkungan `pytest` (dengan `pythonpath=.`) + repo git untuk semua task berikutnya.

- [ ] **Step 1: Tulis `.gitignore`**

```
__pycache__/
*.pyc
.env
*.db
errors.log
monitor.log
deskflow.log*
.pytest_cache/
```

- [ ] **Step 2: Tulis `requirements-dev.txt`**

```
-r requirements.txt
pytest>=8.0
```

- [ ] **Step 3: Tulis `pytest.ini`**

```ini
[pytest]
pythonpath = .
testpaths = tests
```

- [ ] **Step 4: Pasang & verifikasi**

Run: `python -m pip install -r requirements-dev.txt`
Then: `python -m pytest --version`
Expected: mencetak versi pytest, tanpa error.

- [ ] **Step 5: Inisialisasi repo & commit**

```bash
git init
git add .gitignore requirements-dev.txt pytest.ini
git commit -m "chore: bootstrap repo and test harness"
```

---

### Task 2: `portfolio_store.py` — skema & akses SQLite

**Files:**
- Create: `portfolio_store.py`
- Create: `tests/conftest.py`
- Test: `tests/test_portfolio_store.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `DB_FILE = "portfolio.db"`
  - `connect(db_path: str = DB_FILE) -> sqlite3.Connection` — `sqlite3.connect` dengan `row_factory = sqlite3.Row`.
  - `init_db(conn: sqlite3.Connection) -> None`
  - `insert_trade(conn, trade: dict) -> bool` — dedupe lewat `external_id`; `True` bila baris baru.
  - `insert_deposit(conn, idr: float, ts: int, note: str = "") -> int`
  - `list_trades(conn, pair: str | None = None) -> list[dict]`
  - `list_deposits(conn) -> list[dict]`
  - `total_contributions(conn) -> float`
  - `insert_snapshot(conn, snap: dict) -> int`
  - `list_snapshots(conn) -> list[dict]`
  - `trade` dict keys: `pair, side, price, qty, quote, fee, ts, source, external_id`; `side` ∈ {`buy`,`sell`,`reward`}; `source` ∈ {`api`,`manual`,`dca_db`}. `external_id` boleh `None` untuk entri manual (NULL unik-tak-bentrok di SQLite).

- [ ] **Step 1: Tulis tes yang gagal**

```python
# tests/test_portfolio_store.py
import portfolio_store as ps

def test_insert_trade_dedupes_by_external_id(tmp_conn):
    t = dict(pair="btc_idr", side="buy", price=1e9, qty=0.001, quote=1e6,
             fee=2000, ts=1, source="api", external_id="X1")
    assert ps.insert_trade(tmp_conn, t) is True
    assert ps.insert_trade(tmp_conn, t) is False
    assert len(ps.list_trades(tmp_conn)) == 1

def test_manual_trades_without_external_id_both_insert(tmp_conn):
    a = dict(pair="sol_idr", side="buy", price=1.9e6, qty=0.026,
             quote=50000, fee=0, ts=1, source="manual", external_id=None)
    assert ps.insert_trade(tmp_conn, a) is True
    assert ps.insert_trade(tmp_conn, dict(a)) is True
    assert len(ps.list_trades(tmp_conn)) == 2

def test_total_contributions_sums_deposits(tmp_conn):
    ps.insert_deposit(tmp_conn, 500000, 1)
    ps.insert_deposit(tmp_conn, 250000, 2)
    assert ps.total_contributions(tmp_conn) == 750000
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_portfolio_store.py -v`
Expected: FAIL (module `portfolio_store` tak ada).

- [ ] **Step 3: Tulis `tests/conftest.py`**

```python
import pytest
import portfolio_store as ps

@pytest.fixture
def tmp_conn(tmp_path):
    conn = ps.connect(str(tmp_path / "t.db"))
    ps.init_db(conn)
    yield conn
    conn.close()
```

- [ ] **Step 4: Implementasi `portfolio_store.py`**

Buat tabel:
`trades(id INTEGER PK AUTOINCREMENT, pair TEXT, side TEXT CHECK(side IN ('buy','sell','reward')), price REAL, qty REAL, quote REAL, fee REAL DEFAULT 0, ts INTEGER, source TEXT, external_id TEXT UNIQUE)`
`deposits(id INTEGER PK AUTOINCREMENT, idr REAL, ts INTEGER, note TEXT)`
`snapshots(id INTEGER PK AUTOINCREMENT, ts INTEGER, total_idr REAL, contributions_idr REAL, market_idr REAL, breakdown_json TEXT)`
`market_idr` disimpan sebagai **kumulatif** (`total_idr - contributions_idr`). `insert_trade` pakai `INSERT OR IGNORE`, kembalikan `cursor.rowcount == 1`. `list_*` mengembalikan `list[dict]` (dari `sqlite3.Row`). `total_contributions` = `SELECT COALESCE(SUM(idr),0) FROM deposits`.

- [ ] **Step 5: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_portfolio_store.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git add portfolio_store.py tests/conftest.py tests/test_portfolio_store.py
git commit -m "feat: portfolio sqlite store"
```

---

### Task 3: Migrasi `dca.db` → `portfolio.db`

**Files:**
- Create: `migrate_dca.py`
- Test: `tests/test_migrate_dca.py`

**Interfaces:**
- Consumes: `portfolio_store.insert_trade`
- Produces: `migrate(dca_path: str, conn) -> int` — memindahkan baris `purchases(id, pair, idr, price, coins, bought_at, api_trade_id)` menjadi `trades` (side `buy`, source `dca_db`, `quote=idr`, `qty=coins`, `fee=0`, `ts=bought_at`, `external_id = api_trade_id or f"dca:{id}"`); idempoten (aman diulang); mengembalikan jumlah baris baru.

- [ ] **Step 1: Tulis tes yang gagal**

```python
# tests/test_migrate_dca.py
import sqlite3
import migrate_dca as m
import portfolio_store as ps

def _make_dca(path):
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE purchases (id INTEGER PRIMARY KEY AUTOINCREMENT,"
              " pair TEXT, idr REAL, price REAL, coins REAL, bought_at INTEGER,"
              " api_trade_id TEXT UNIQUE)")
    c.execute("INSERT INTO purchases (pair,idr,price,coins,bought_at,api_trade_id)"
              " VALUES ('eth_idr',198129,44439000,0.00445846,1791570000,'T1')")
    c.execute("INSERT INTO purchases (pair,idr,price,coins,bought_at,api_trade_id)"
              " VALUES ('btc_idr',100000,1e9,0.0001,1791570001,NULL)")
    c.commit(); c.close()

def test_migrate_is_idempotent(tmp_path, tmp_conn):
    dca = tmp_path / "dca.db"; _make_dca(str(dca))
    assert m.migrate(str(dca), tmp_conn) == 2
    assert m.migrate(str(dca), tmp_conn) == 0
    rows = ps.list_trades(tmp_conn)
    assert len(rows) == 2
    assert {r["source"] for r in rows} == {"dca_db"}
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_migrate_dca.py -v`
Expected: FAIL (module `migrate_dca` tak ada).

- [ ] **Step 3: Implementasi `migrate_dca.py`**

Buka `dca.db` read-only, `SELECT id,pair,idr,price,coins,bought_at,api_trade_id FROM purchases`, petakan tiap baris lalu panggil `insert_trade`; jumlahkan yang mengembalikan `True`. Tangani `sqlite3.OperationalError` (file/tabel tak ada) dengan mengembalikan `0`.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_migrate_dca.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add migrate_dca.py tests/test_migrate_dca.py
git commit -m "feat: migrate dca.db purchases into portfolio.db"
```

---

### Task 4: Parsing saldo API (v1 & v2)

**Files:**
- Create: `portfolio.py`
- Test: `tests/test_portfolio_parse.py`

**Interfaces:**
- Consumes: `indodax_api.account_info()`
- Produces:
  - `COIN_PAIRS = {"btc": "btc_idr", "eth": "eth_idr", "sol": "sol_idr", "bch": "bch_idr"}`
  - `parse_balances(info: dict) -> dict[str, float]` — koin (lowercase) → kuantitas, termasuk `"IDR"`; memfilter nilai nol; menerima bentuk v1 dan v2.
  - `fetch_balances() -> dict[str, float]`

- [ ] **Step 1: Tulis tes yang gagal**

```python
# tests/test_portfolio_parse.py
import portfolio as p

V2 = {"balances": [
    {"asset": "BTC", "free": "0.0005", "locked": "0"},
    {"asset": "IDR", "free": "203394", "locked": "0"},
    {"asset": "SOL", "free": "0", "locked": "0"},
]}
V1 = {"success": 1, "return": {"balance": {"btc": "0.0005", "bch": "0.000583"},
                               "balance_rp": "203394"}}

def test_parse_v2_merges_free_locked_and_filters_zero():
    b = p.parse_balances(V2)
    assert b["btc"] == 0.0005
    assert b["IDR"] == 203394
    assert "sol" not in b          # nol difilter

def test_parse_v1_shape():
    b = p.parse_balances(V1)
    assert b["btc"] == 0.0005
    assert b["bch"] == 0.000583
    assert b["IDR"] == 203394
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_portfolio_parse.py -v`
Expected: FAIL (module `portfolio` tak ada).

- [ ] **Step 3: Implementasi `parse_balances` + `fetch_balances` di `portfolio.py`**

`fetch_balances` memanggil `indodax_api.account_info()` dan mengembalikan `parse_balances(resp)`. `parse_balances`: jika `info` punya kunci `balances` (list) → jumlahkan `free+locked` per `asset`; elif `info["return"]["balance"]` (dict) → pakai nilainya, plus `balance_rp` sebagai `IDR`. Kunci dinormalkan `.lower()`; nilai di-`float`, nol dibuang.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_portfolio_parse.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add portfolio.py tests/test_portfolio_parse.py
git commit -m "feat: parse indodax v1/v2 balances"
```

---

### Task 5: Harga rata-rata, kepemilikan (`Holding`), & P&L terealisasi

**Files:**
- Modify: `portfolio.py`
- Test: `tests/test_portfolio_holdings.py`

**Interfaces:**
- Consumes: `portfolio_store.list_trades`, `portfolio.COIN_PAIRS`
- Produces:
  - `@dataclass Holding(pair: str, coin: str, qty: float, avg_cost: float | None, price: float | None, value_idr: float, cost_basis_idr: float | None, unrealized_idr: float | None, basis_known: bool)`
  - `compute_holdings(trades: list[dict], balances: dict[str, float], prices: dict[str, float]) -> list[Holding]`
  - `realized_pnl(trades: list[dict]) -> dict[str, float]` — per `pair`.

**Aturan:** kuantitas = `balances` (otoritatif). Dasar biaya dari `trades` berurutan `ts`: `buy` menambah `qty_bought` & `cost` (+ `fee`); `sell` mengurangi `qty_bought` dan menambah `realized += quote - avg_cost*qty_sell`; `reward` menambah `qty_held` dengan biaya 0 (tidak mengubah `avg_cost`). Bila tidak ada `buy` untuk koin → `basis_known=False`, `avg_cost=None`, `cost_basis_idr=None`, `unrealized_idr=None`. Kuantitas negatif setelah penjualan di-clamp ke 0 (jangan crash).

- [ ] **Step 1: Tulis tes yang gagal**

```python
# tests/test_portfolio_holdings.py
import portfolio as p

def test_basis_unknown_for_coin_without_trades():
    h = p.compute_holdings([], {"bch": 0.000583}, {"bch_idr": 7_500_000})
    bch = [x for x in h if x.coin == "bch"][0]
    assert bch.basis_known is False and bch.avg_cost is None
    assert bch.value_idr == 0.000583 * 7_500_000

def test_rewards_increase_qty_without_raising_basis():
    trades = [dict(pair="eth_idr", side="buy", price=40_000_000, qty=0.001,
                   quote=40_000, fee=0, ts=1),
              dict(pair="eth_idr", side="reward", price=0, qty=0.0001,
                   quote=0, fee=0, ts=2)]
    h = p.compute_holdings(trades, {"eth": 0.0011}, {"eth_idr": 45_000_000})
    eth = [x for x in h if x.coin == "eth"][0]
    assert eth.avg_cost == 40_000_000          # 40.000 / 0.001
    assert eth.cost_basis_idr == 40_000

def test_sell_above_buy_keeps_avg_cost_nonnegative():
    trades = [dict(pair="eth_idr", side="buy", price=44_439_000, qty=0.00445846,
                   quote=198_129, fee=0, ts=1),
              dict(pair="eth_idr", side="sell", price=44_850_000, qty=0.00454928,
                   quote=201_035, fee=0, ts=2)]
    h = p.compute_holdings(trades, {"eth": 0.0}, {"eth_idr": 44_750_000})
    eth = [x for x in h if x.coin == "eth"][0]
    assert eth.avg_cost >= 0 and eth.basis_known is True

def test_realized_pnl_on_profitable_sell():
    trades = [dict(pair="btc_idr", side="buy", price=1_000_000, qty=1.0,
                   quote=1_000_000, fee=0, ts=1),
              dict(pair="btc_idr", side="sell", price=1_200_000, qty=1.0,
                   quote=1_200_000, fee=0, ts=2)]
    assert p.realized_pnl(trades)["btc_idr"] == 200_000
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_portfolio_holdings.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementasi `Holding`, `compute_holdings`, `realized_pnl`**

`realized_pnl` mengiterasi trade terurut `ts` per pair, melacak `avg_cost` & `qty`; pada `sell` menambah `quote - avg_cost*qty`. `compute_holdings` memakai hasil akhir per pair untuk membentuk `Holding` bagi setiap koin di `balances` (kecuali `IDR`) yang punya pair di `COIN_PAIRS`; urutkan menurun berdasar `value_idr`.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_portfolio_holdings.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add portfolio.py tests/test_portfolio_holdings.py
git commit -m "feat: holdings with average cost, unrealized and realized P&L"
```

---

### Task 6: Sinkronisasi riwayat transaksi dari API Indodax

**Files:**
- Modify: `portfolio.py`
- Test: `tests/test_portfolio_sync.py`

**Interfaces:**
- Consumes: `indodax_api.my_trades(pair)` → `list[{id, side, price, qty, quote, time}]` (sudah ternormalisasi), `portfolio_store.insert_trade`, `portfolio.COIN_PAIRS`
- Produces: `sync_trades(conn, pairs: list[str] | None = None) -> int` — untuk tiap pair (default `list(COIN_PAIRS.values())`) tarik `my_trades`, simpan sebagai `trades` dengan `source="api"`, `external_id=f"{pair}:{id}"`, `fee=0`, `ts=time`; lewati bila `side` bukan `buy`/`sell`. Kembalikan jumlah baris baru. Bungkus kegagalan jaringan per-pair dengan `try/except`, lanjutkan.

- [ ] **Step 1: Tulis tes yang gagal**

```python
# tests/test_portfolio_sync.py
import portfolio as p
import portfolio_store as ps

def test_sync_trades_stores_buys_and_sells_and_dedupes(tmp_conn, monkeypatch):
    fake = [{"id": "7", "side": "buy", "price": 1e9, "qty": 0.001,
             "quote": 1e6, "time": 5},
            {"id": "8", "side": "sell", "price": 1.1e9, "qty": 0.001,
             "quote": 1.1e6, "time": 6}]
    monkeypatch.setattr(p.indodax_api, "my_trades", lambda pair: fake)
    assert p.sync_trades(tmp_conn, ["btc_idr"]) == 2
    assert p.sync_trades(tmp_conn, ["btc_idr"]) == 0
    rows = ps.list_trades(tmp_conn)
    assert {r["external_id"] for r in rows} == {"btc_idr:7", "btc_idr:8"}
    assert {r["source"] for r in rows} == {"api"}
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_portfolio_sync.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementasi `sync_trades`**

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_portfolio_sync.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add portfolio.py tests/test_portfolio_sync.py
git commit -m "feat: sync buys and sells from indodax my_trades"
```

---

### Task 7: `rules.dca_split` — pembagian setoran bulanan

**Files:**
- Create: `rules.py`
- Test: `tests/test_rules_dca.py`

**Interfaces:**
- Consumes: —
- Produces:
  - `DEFAULT_TARGET = {"btc_idr": 0.55, "eth_idr": 0.25, "sol_idr": 0.10, "IDR": 0.10}`
  - `dca_split(amount_idr: float, holdings_value: dict[str, float], target: dict = DEFAULT_TARGET, min_order: int = 10000) -> dict[str, float]` — kunci = kunci `target`; total hasil = `amount_idr`.

**Aturan:** mulai dari bobot dasar × amount; hitung defisit tiap aset = `max(0, target_share − current_share)` (current_share dari `holdings_value` yang dijumlahkan bersama IDR cadangan); pindahkan dana dari aset surplus ke defisit secara proporsional; bulatkan ke bawah ke kelipatan Rp 1.000; alokasi aset < `min_order` dipindahkan ke `IDR`; sisa pembulatan masuk `IDR`.

- [ ] **Step 1: Tulis tes yang gagal**

```python
# tests/test_rules_dca.py
import rules

def test_split_basic_weights():
    out = rules.dca_split(500000, {})
    assert out["btc_idr"] == 275000
    assert out["eth_idr"] == 125000
    assert out["sol_idr"] == 50000
    assert out["IDR"] == 50000
    assert sum(out.values()) == 500000

def test_tilts_toward_most_underweight():
    hv = {"btc_idr": 250000, "eth_idr": 0, "sol_idr": 0, "IDR": 0}
    out = rules.dca_split(500000, hv)
    assert out["eth_idr"] > 125000
    assert sum(out.values()) == 500000

def test_below_min_order_goes_to_idr():
    out = rules.dca_split(20000, {})
    assert out["IDR"] == 20000
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_rules_dca.py -v`
Expected: FAIL (module `rules` tak ada).

- [ ] **Step 3: Implementasi `dca_split`**

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_rules_dca.py -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add rules.py tests/test_rules_dca.py
git commit -m "feat: dca flow split toward underweight assets"
```

---

### Task 8: `rules.take_profit_actions` — take-profit berbasis harga rata-rata

**Files:**
- Modify: `rules.py`
- Test: `tests/test_rules_takeprofit.py`

**Interfaces:**
- Consumes: `portfolio.Holding`
- Produces:
  - `@dataclass Action(kind: str, pair: str, amount_idr: float, qty: float | None, price_ref: float | None, reason: str, executable: bool)` (`kind` ∈ {`BUY`,`SELL`,`HOLD`})
  - `take_profit_actions(holdings: list[Holding], thresholds: tuple = ((50, 25), (100, 25)), fee_pct: float = 0.002, min_order: int = 10000) -> list[Action]`

**Aturan:** untuk tiap `Holding` dengan `basis_known` dan `price`: `gain_pct = (price*(1-fee_pct) − avg_cost)/avg_cost*100`. Tentukan **level tertinggi** yang terlewati; persennya adalah **jumlah kumulatif** dari `thresholds` sampai level itu (mis. lewat 50% → 25%; lewat 100% → 50%). Bila ada level terlewati → satu `SELL` dengan `qty = qty_posisi * pct/100`, `amount_idr = qty*price`, `price_ref = price`, `reason` menyebut level & persen. Bila `amount_idr < min_order` → `executable=False` (tetap dikembalikan sebagai saran). `basis_known=False` atau `price is None` → tidak ada aksi.

- [ ] **Step 1: Tulis tes yang gagal**

```python
# tests/test_rules_takeprofit.py
import pytest
import rules
from portfolio import Holding

def _h(avg, price, qty=1.0):
    return Holding("btc_idr", "btc", qty, avg, price, qty*price, avg*qty,
                   (price-avg)*qty, True)

def test_fires_cumulative_25pct_at_plus_50_net():
    a = rules.take_profit_actions([_h(1000, 1600)])   # net +59,7%
    assert len(a) == 1 and a[0].kind == "SELL" and a[0].executable
    assert a[0].qty == pytest.approx(0.25)

def test_fires_cumulative_50pct_at_plus_100():
    a = rules.take_profit_actions([_h(1000, 2100)])   # net +109,6%
    assert a[0].qty == pytest.approx(0.50)

def test_no_action_when_basis_unknown():
    h = Holding("bch_idr", "bch", 0.001, None, 7.5e6, 7500, None, None, False)
    assert rules.take_profit_actions([h]) == []

def test_below_min_order_not_executable():
    a = rules.take_profit_actions([_h(1000, 1600, qty=0.001)])  # 25% ~ Rp 0,4
    assert a and a[0].executable is False
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_rules_takeprofit.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementasi `Action` + `take_profit_actions`**

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_rules_takeprofit.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add rules.py tests/test_rules_takeprofit.py
git commit -m "feat: average-cost take-profit actions"
```

---

### Task 9: `rules.rebalance_actions` + ukuran posisi

**Files:**
- Modify: `rules.py`
- Test: `tests/test_rules_rebalance.py`

**Interfaces:**
- Consumes: `portfolio.Holding`, `rules.DEFAULT_TARGET`
- Produces:
  - `rebalance_actions(holdings: list[Holding], target: dict = DEFAULT_TARGET, band_pct: float = 10.0, fee_pct: float = 0.002, min_order: int = 10000) -> list[Action]`
  - `max_position_ok(amount_idr: float, total_idr: float, max_pct: float = 0.25) -> bool`

**Aturan (aliran dulu):** hanya menghasilkan `SELL` untuk aset yang **menyimpang > `band_pct` poin** di atas target **dan** memicu take-profit (memakai `take_profit_actions`). **Tidak pernah** menghasilkan `BUY` (pembelian hanya lewat aliran DCA) — sehingga cadangan IDR tidak pernah dibelanjakan oleh rebalance. `max_position_ok` = `amount_idr <= max_pct * total_idr`.

- [ ] **Step 1: Tulis tes yang gagal**

```python
# tests/test_rules_rebalance.py
import rules
from portfolio import Holding

def _h(coin, pair, avg, price, qty):
    return Holding(pair, coin, qty, avg, price, qty*price, avg*qty,
                   (price-avg)*qty, True)

def test_no_sell_on_drift_without_takeprofit():
    h = [_h("btc", "btc_idr", 1000, 1050, 100.0)]
    assert [a for a in rules.rebalance_actions(h) if a.kind == "SELL"] == []

def test_sell_when_overweight_and_takeprofit():
    h = [_h("btc", "btc_idr", 1000, 1600, 100.0)]
    sells = [a for a in rules.rebalance_actions(h) if a.kind == "SELL"]
    assert sells and sells[0].pair == "btc_idr"

def test_never_emits_buy_so_reserve_is_untouched():
    h = [_h("btc", "btc_idr", 1000, 1000, 1.0)]
    assert [a for a in rules.rebalance_actions(h) if a.kind == "BUY"] == []

def test_position_size_cap():
    assert rules.max_position_ok(200000, 1000000) is True
    assert rules.max_position_ok(300000, 1000000) is False
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_rules_rebalance.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementasi `rebalance_actions` + `max_position_ok`**

Hitung total portofolio (holdings + IDR cadangan); untuk tiap holding hitung `share` vs target; bila `share*100 - target*100 > band_pct` dan `take_profit_actions([holding])` tidak kosong → emit `SELL` (kuantitas dari take-profit). Selalu tanpa `BUY`.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_rules_rebalance.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add rules.py tests/test_rules_rebalance.py
git commit -m "feat: flow-first rebalance and position cap"
```

---

### Task 10: `alerts.py` — pesan peringatan

**Files:**
- Create: `alerts.py`
- Test: `tests/test_alerts.py`

**Interfaces:**
- Consumes: `rules.Action`
- Produces:
  - `build_alerts(holdings: list, actions: list[rules.Action]) -> list[str]`
  - `send_telegram(token: str, chat_id: str, text: str) -> bool` (disiapkan; tidak dipanggil di v1)

- [ ] **Step 1: Tulis tes yang gagal**

```python
# tests/test_alerts.py
import alerts, rules

def test_no_alerts_when_nothing():
    assert alerts.build_alerts([], []) == []

def test_alert_mentions_pair_and_reason():
    a = rules.Action("SELL", "btc_idr", 50000, 0.00003, 1.6e9,
                     "+59% dari rata-rata", True)
    out = alerts.build_alerts([], [a])
    assert len(out) == 1 and "btc_idr" in out[0] and "%" in out[0]
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_alerts.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementasi `alerts.py`**

`build_alerts` menghasilkan satu string berbahasa Indonesia per `Action` dengan `kind != "HOLD"`, memuat `pair`, `reason`, dan nominal; aksi `executable=False` diberi catatan "di bawah minimum order". `send_telegram` mengirim via `requests.get` ke Bot API, `try/except` → `False` bila gagal.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_alerts.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add alerts.py tests/test_alerts.py
git commit -m "feat: alert message builder"
```

---

### Task 11: `portfolio.refresh` — rangkai semuanya + snapshot

**Files:**
- Modify: `portfolio.py`
- Test: `tests/test_portfolio_refresh.py`

**Interfaces:**
- Consumes: `portfolio_store`, `compute_holdings`, `fetch_balances`, `sync_trades`, `rules`
- Produces:
  - `fetch_price(pair: str) -> float | None` — bid dari `scanner.indodax_price(pair)[0]`; `None` bila gagal/429.
  - `refresh(conn, online: bool = True) -> dict` — `{"holdings": list[Holding], "total_idr": float, "actions": list[Action]}`; menulis satu baris `snapshots`.

**Aturan:** `refresh` (bila `online`) memanggil `sync_trades` + `fetch_balances`; hitung holdings memakai `prices = {pair: fetch_price(pair)}`; harga `None` → nilai aset 0; jalankan `rules.take_profit_actions` + `rules.rebalance_actions`; tulis snapshot dengan `contributions_idr = portfolio_store.total_contributions(conn)` dan `market_idr = total_idr − contributions_idr`.

- [ ] **Step 1: Tulis tes yang gagal**

```python
# tests/test_portfolio_refresh.py
import portfolio as p
import portfolio_store as ps

def test_refresh_handles_missing_price(tmp_conn, monkeypatch):
    monkeypatch.setattr(p, "fetch_balances", lambda: {"btc": 0.001, "IDR": 200000})
    monkeypatch.setattr(p, "fetch_price", lambda pair: None)
    monkeypatch.setattr(p, "sync_trades", lambda conn, pairs=None: 0)
    out = p.refresh(tmp_conn)
    assert out["total_idr"] == 200000          # harga tak tersedia -> nilai aset 0
    assert len(ps.list_snapshots(tmp_conn)) == 1
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_portfolio_refresh.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementasi `fetch_price` + `refresh`**

`fetch_price`: `try/except` membungkus `scanner.indodax_price`, ambil `[0]`. `refresh`: ambil data (opsional online), bentuk holdings, jalankan aturan, `portfolio_store.insert_snapshot`, kembalikan dict.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_portfolio_refresh.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add portfolio.py tests/test_portfolio_refresh.py
git commit -m "feat: refresh orchestration and snapshots"
```

---

### Task 12: `growth_report.py` — kontribusi vs pasar

**Files:**
- Create: `growth_report.py`
- Test: `tests/test_growth_report.py`

**Interfaces:**
- Consumes: `portfolio_store.list_snapshots`, `portfolio_store.list_deposits`
- Produces: `monthly_report(snapshots: list[dict], deposits: list[dict]) -> list[dict]` dengan baris `{month: str, contributions_idr: float, market_idr: float, end_value_idr: float}`.

**Aturan:** urutkan snapshot & setoran berdasar `ts`; untuk tiap rentang bulan: `contributions_idr` = jumlah setoran pada bulan itu; `market_idr` = `nilai_akhir − nilai_awal − contributions_idr`; `end_value_idr` = snapshot terakhir bulan itu.

- [ ] **Step 1: Tulis tes yang gagal**

```python
# tests/test_growth_report.py
import growth_report as g

def test_splits_contributions_from_market():
    snaps = [{"ts": 1000, "total_idr": 700000},
             {"ts": 2000, "total_idr": 800000}]
    deps = [{"idr": 500000, "ts": 1500}]
    rows = g.monthly_report(snaps, deps)
    assert rows[-1]["contributions_idr"] == 500000
    assert rows[-1]["market_idr"] == 800000 - 700000 - 500000
    assert rows[-1]["end_value_idr"] == 800000
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_growth_report.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementasi `growth_report.py`**

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_growth_report.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add growth_report.py tests/test_growth_report.py
git commit -m "feat: monthly growth report"
```

---

### Task 13: `portfolio_cli.py` — catat setoran & beli manual

**Files:**
- Create: `portfolio_cli.py`
- Test: `tests/test_portfolio_cli.py`

**Interfaces:**
- Consumes: `portfolio_store`
- Produces:
  - `cmd_deposit(conn, idr: float, ts: int, note: str = "") -> int`
  - `cmd_buy(conn, pair: str, idr: float, price: float, ts: int) -> bool` — normalisasi `pair` (`sol` → `sol_idr`), `qty = idr/price`, `source="manual"`, `external_id=None`.
  - `cmd_status(conn) -> str`
  - `main(argv: list[str]) -> int` — subperintah `deposit <idr> [YYYY-MM-DD]`, `buy <pair> <idr> <harga> [YYYY-MM-DD]`, `status`, `snapshot`.

- [ ] **Step 1: Tulis tes yang gagal**

```python
# tests/test_portfolio_cli.py
import portfolio_cli as cli
import portfolio_store as ps

def test_cmd_buy_records_trade(tmp_conn):
    assert cli.cmd_buy(tmp_conn, "sol", 50000, 1_900_000, 1791570000) is True
    t = ps.list_trades(tmp_conn)[0]
    assert t["pair"] == "sol_idr" and t["source"] == "manual"

def test_cmd_deposit_records(tmp_conn):
    cli.cmd_deposit(tmp_conn, 500000, 1791570000, "setoran Okt")
    assert ps.total_contributions(tmp_conn) == 500000
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_portfolio_cli.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementasi `portfolio_cli.py`**

Pakai `argparse`; `ts` dari tanggal `YYYY-MM-DD` atau `time.time()` bila kosong; `cmd_status` meringkas holdings + kontribusi + aksi dari `portfolio.refresh(online=False)` (tanpa jaringan).

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_portfolio_cli.py -v`
Expected: 2 passed.

- [ ] **Step 5: Commit**

```bash
git add portfolio_cli.py tests/test_portfolio_cli.py
git commit -m "feat: cli to record deposits and manual buys"
```

---

### Task 14: Tab dasbor mandiri

**Files:**
- Create: `dashboard/portfolio_tab.py`
- Modify: `dashboard/app.py` (tambah satu tab yang memanggil `portfolio_tab.render()`)
- Test: `tests/test_portfolio_tab.py`

**Interfaces:**
- Consumes: `portfolio.refresh`, `rules`, `alerts`, `growth_report`, `portfolio_store`
- Produces: `fmt_idr(x: float) -> str`; `render() -> None`

- [ ] **Step 1: Tulis tes smoke untuk helper format**

```python
# tests/test_portfolio_tab.py
import dashboard.portfolio_tab as tab

def test_fmt_idr():
    assert tab.fmt_idr(1234567).startswith("Rp")
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_portfolio_tab.py -v`
Expected: FAIL (module tak ada).

- [ ] **Step 3: Implementasi `dashboard/portfolio_tab.py`**

`fmt_idr` pakai format ribuan Indonesia (`Rp 1.234.567`). `render()` membuka koneksi `portfolio.db`, panggil `portfolio.refresh`, tampilkan: (a) tabel holdings — kuantitas, harga rata-rata, nilai, P&L belum terealisasi, tanda **"harga rata-rata tidak diketahui"** bila `basis_known=False`; (b) panel aksi take-profit/rebalance dari `alerts.build_alerts`; (c) progres DCA bulan ini (`rules.dca_split` vs setoran nyata); (d) grafik pertumbuhan dari `growth_report.monthly_report`; (e) **banner tetap: "Read-only — sistem ini tidak bisa mengirim order."** Semua teks bahasa Indonesia; semua pemanggilan jaringan dibungkus `try/except` agar tab tidak crash.

- [ ] **Step 4: Jalankan tes & verifikasi manual**

Run: `python -m pytest tests/test_portfolio_tab.py -v` → Expected: 1 passed.
Run: `python -m streamlit run dashboard/app.py --server.headless=true`
Expected: tab "Portofolio" muncul tanpa error, tab lama tetap jalan, dan masih dapat dibuka via Tailscale.

- [ ] **Step 5: Commit**

```bash
git add dashboard/portfolio_tab.py dashboard/app.py tests/test_portfolio_tab.py
git commit -m "feat: read-only portfolio dashboard tab"
```

---

### Task 15: Snapshot harian + jadwal (opsional Telegram)

**Files:**
- Create: `scheduler.py`
- Test: `tests/test_scheduler.py`

**Interfaces:**
- Consumes: `portfolio.refresh`, `alerts.send_telegram`, `portfolio_store.DB_FILE`
- Produces:
  - `take_snapshot(conn) -> dict` — panggil `portfolio.refresh(conn)` lalu kembalikan hasilnya.
  - `main(argv: list[str]) -> int` — buka `portfolio_store.DB_FILE`, panggil `take_snapshot`, cetak ringkasan; dipakai oleh Windows Task Scheduler.

- [ ] **Step 1: Tulis tes yang gagal**

```python
# tests/test_scheduler.py
import scheduler
import portfolio_store as ps

def test_take_snapshot_writes_row(tmp_conn, monkeypatch):
    monkeypatch.setattr(scheduler.portfolio, "refresh",
                        lambda conn, online=True: {"holdings": [],
                                                   "total_idr": 0, "actions": []})
    monkeypatch.setattr(scheduler.portfolio_store, "insert_snapshot",
                        lambda conn, snap: 99)
    out = scheduler.take_snapshot(tmp_conn)
    assert out["total_idr"] == 0
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_scheduler.py -v`
Expected: FAIL.

- [ ] **Step 3: Implementasi `scheduler.py`**

`take_snapshot` hanya memanggil `portfolio.refresh` (yang menulis snapshot). `main` membuka DB `portfolio_store.DB_FILE`, `init_db`, `take_snapshot`, cetak ringkasan bernominal Rupiah. Opsional: `--telegram` mengirim hasil ke Telegram bila `TELEGRAM_TOKEN`/`TELEGRAM_CHAT_ID` diset.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_scheduler.py -v`
Expected: 1 passed.

- [ ] **Step 5: Commit**

```bash
git add scheduler.py tests/test_scheduler.py
git commit -m "feat: daily snapshot job"
```

---

## Catatan Penutup

- Jalankan seluruh tes: `python -m pytest -v` (target: semua lulus).
- Verifikasi read-only: grep kode baru — hanya `account_info` dan `my_trades`, tidak ada pemanggilan order (`/trade`, `placeOrder`, `cancelOrder`).
- Migrasi sekali (jalankan setelah Task 3):
  `python -c "import portfolio_store,migrate_dca; c=portfolio_store.connect(); portfolio_store.init_db(c); print(migrate_dca.migrate('dca.db', c))"`.
- Alur harian: `python portfolio_cli.py snapshot` (atau via `scheduler.py`) → buka dasbor.
