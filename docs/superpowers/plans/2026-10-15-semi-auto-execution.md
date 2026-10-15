# Semi-Auto Execution (v1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Membangun lapisan eksekusi semi-otomatis yang mengirim order ke Indodax **hanya**
setelah konfirmasi Telegram, dengan pengaman yang berhenti sendiri saat ada keraguan.

**Architecture:** Satu modul murni per kebijakan (`spread`, `safeguards`, `proposal`,
`reconcile`, `shadow_log`) dipanggil oleh satu `engine` tick. Eksekusi melewati
`executor` yang menerima objek `Transport` — jalur jaringan disuntikkan, bukan dipanggil
langsung, sehingga **seluruh jalur eksekusi bisa diuji tanpa pernah mengirim order**.
Dalam mode bayangan **tidak ada `Transport` yang dibuat**, jadi tidak ada jalur fisik ke
bursa.

**Tech Stack:** Python 3.14, `urllib`/`requests`, SQLite (`portfolio.db`), Telegram Bot
API (inline keyboard), pytest.

**Spec:** `docs/superpowers/specs/2026-10-15-semi-auto-execution-design.md` (disetujui
2026-10-15). Rencana ini berargumen dari spec — keduanya harus dibaca bersama.

---

## Global Constraints

- **Bahasa:** semua pesan, tombol, dan log **Bahasa Indonesia**, bahasa sederhana.
- **Prinsip:** *ragu = tidak kirim*. Kegagalan = **berhenti dan diam**, tidak pernah
  mengulang pengiriman.
- **Order hanya `limit`.** Tidak ada market order. Tidak ada `withdraw` kapan pun.
- **`portfolio.db` hanya ditulis setelah bursa menjawab status final** (terisi / batal /
  ditolak). Status ambigu → **tidak ada tulisan ke DB**.
- **Atap per order: Rp 275.000.**
- **Pagu: 3 order per hari PER ASET** (bukan global).
- **Jeda: 15–30 menit** setelah satu order terisi, sebelum aset itu menerima order lagi.
- **Jendela konfirmasi reaktif: 90 detik.** Dr harga > **0,5%** dari pesan → hangus.
- **Jendela DCA: 24 jam** dari 09:00. Pengingat **15.00 dan 21.00** saja.
- **DND 23:00–07:00** — menang atas jadwal pengingat; sinyal hanya dicatat.
- **`MAX_SPREAD_BPS = 30`**, formula `(ask − bid) / mid × 10_000`.
  Gugur → **notifikasi wajib satu baris**, bukan diam.
- **`available = free − cadangan_terkunci`** (`free` sudah bersih dari order terbuka).
- **`recvWindow: 15000` ms** untuk POST order.
- **Query status: maks 3× dengan jeda `[2, 5, 10]` detik, ID yang sama.** Masih ambigu
  → halt + notifikasi. **Membuat ID baru dilarang.**
- **`canTrade` / `canWithdraw` dipantau setiap putaran.** `canWithdraw == True` → halt.
  `canTrade` tidak cocok dengan mode → halt.
- **v1 hanya: DCA + take-profit + reserve buy.** `rebalance_actions` **tidak dipanggil**.
- **Dihapus, jangan ditambah kembali:** drawdown, rugi harian, daftar pair izin.
- **Mode bayangan: `shadow_day` di-reset ke 0** begitu ada satu pun penolakan.
- Kolom waktu memakai **waktu lokal** (`time.localtime`), konsisten dengan
  `portfolio.py:223` dan `growth_report.py:13`.

## Review Focus

Lima hal yang paling mungkin merusak uang atau disiplin, dan tidak diuji oleh spec mana
pun secara langsung. Setiap baris punya tesnya di task yang memiliki kodenya.

1. **Balasan Telegram hilang saat submit** → risiko order ganda. Task 6: tes transport
   melempar `TimeoutError` setelah payload terkirim; harus **tidak** ada submit kedua dan
   status ditanya dengan **ID yang sama**.
2. **Query `404` sesaat setelah order sukses** (didokumentasikan Indodax) → salah dianggap
   gagal lalu mengulang. Task 7: `404` pada percobaan 1–3 → tetap **menggunakan ID yang
   sama** melewati backoff `[2,5,10]`, dan **tidak pernah** memanggil `post_order` lagi.
3. **Spread lolos saat menyusun, melebar saat eksekusi** → order pada harga buruk.
   Task 12: bps 10 saat komposisi, 45 saat eksekusi → **tolak + notifikasi**, bukan kirim.
4. **Dua aturan menghasilkan SELL pair yang sama** → kuantitas tergandakan (bug "25+25=50").
   Task 4: `take_profit_actions` + `rebalance_actions` untuk pair sama → **satu** aksi
   hasil gabungan, `qty` dihitung dari posisi tersisa.
5. **DCA di hari pagunya penuh atau pengingat 03.00 masuk DND** → DCA terlewat diam-diam.
   Task 2: 3 order sudah terisi `btc_idr` hari ini → order ke-4 **ditolak + notifikasi**;
   Task 13: pengingat yang jatuh dalam 23:00–07:00 **tidak dikirim**, hanya dicatat.

---

## File Structure

| File | Tanggung jawab |
|---|---|
| `spread.py` | Ukur spread dari depth, putuskan lolos/tolak (murni + 1 fetch) |
| `safeguards.py` | Semua gerbang **per-aksi**: atap, pagu per aset, jeda, anti-loop, saldo, izin |
| `proposal.py` | Model proposal, teks konfirmasi, kadaluarsa, geser harga |
| `actions_merge.py` | Gabung aksi kembar per `(kind, pair)` — pewaris dedupe `24ad5ef` |
| `reconcile.py` | Bandingkan `portfolio.db` vs bursa → daftar selisih |
| `executor.py` | `client_order_id`, submit satu-di-udara, query status ber-backoff |
| `telegram_gate.py` | Kirim konfirmasi + inline keyboard, poll update, baca tombol |
| `shadow_log.py` | Catat setiap proposal + ringkasan harian untuk review |
| `engine.py` | Satu `tick()`: baca → aturan → gate → usulkan → eksekusi |
| `tests/test_*.py` | Satu berkas per modul di atas |

---

### Task 1: Gerbang spread

**Files:**
- Create: `spread.py`
- Test: `tests/test_spread.py`

**Interfaces:**
- Consumes: `portfolio.fetch_price(pair) -> float | None`
- Produces:
  - `MAX_SPREAD_BPS = 30`
  - `spread_bps(depth: dict) -> float` — `depth` punya kunci `buy`/`sell`, masing-masing
    daftar `[price, amount]`
  - `fetch_depth(pair_id: str) -> dict | None` — `None` bila gagal/429/kosong
  - `spread_verdict_from_depth(depth: dict, max_bps: float = MAX_SPREAD_BPS)
    -> tuple[bool | None, float]` — inti murni; `depth` kosong → `(None, nan)`
  - `spread_verdict(pair_id: str, max_bps: float = MAX_SPREAD_BPS, *,
    fetch=fetch_depth) -> tuple[bool | None, float]` — **`fetch` disuntikkan**,
    inilah yang dipakai `engine` supaya tes tidak pernah membuka jaringan

- [ ] **Step 1: Tulis tes yang gagal**

```python
def test_spread_bps_uses_mid_not_bid():
    depth = {"buy": [["100", "1"]], "sell": [["101", "1"]]}
    assert spread.spread_bps(depth) == pytest.approx(10000 * 1 / 100.5)

def test_verdict_passes_below_threshold():
    depth = {"buy": [["100", "1"]], "sell": [["100.1", "1"]]}   # ~10 bps
    assert spread.spread_verdict_from_depth(depth, max_bps=30) == (True, 10)

def test_verdict_rejects_bch_like_spread():
    depth = {"buy": [["4655000", "1"]], "sell": [["4699000", "1"]]}  # ~94 bps
    ok, bps = spread.spread_verdict_from_depth(depth, max_bps=30)
    assert ok is False and bps == pytest.approx(94, abs=1)

def test_empty_depth_is_not_zero():
    # Harga hilang tidak boleh dinilai 0 lalu diloloskan (spec 6.3)
    assert spread.spread_verdict_from_depth({}, max_bps=30) == (None, float("nan"))
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_spread.py -v`
Expected: FAIL — `AttributeError: module 'spread' has no attribute ...`

- [ ] **Step 3: Implementasi `spread.py`**

`spread_bps` menghitung `(ask − bid) / mid × 10_000` dari `depth["sell"][0][0]` dan
`depth["buy"][0][0]`. `spread_verdict_from_depth` adalah inti murni (dipakai tes tanpa
jaringan); `fetch_depth` membungkus `GET https://indodax.com/api/depth/{pair_id}` dengan
UA dari `indodax_api.UA`, timeout 20 dtk, dan mengembalikan `None` untuk status non-200
(termasuk 429) atau body kosong.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_spread.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add spread.py tests/test_spread.py
git commit -m "feat: gerbang spread 30 bps dengan notifikasi wajib"
```

---

### Task 2: Gerbang per-aksi

**Files:**
- Create: `safeguards.py`
- Test: `tests/test_safeguards.py`

**Interfaces:**
- Consumes: `rules.Action` (field `kind`, `pair`, `amount_idr`, `qty`, `executable`)
- Produces:
  - `MAX_ORDER_IDR = 275000`
  - `MAX_PER_ASSET_PER_DAY = 3`
  - `COOLDOWN_SECONDS = 1800`
  - `gates(action: rules.Action, *, free_idr, reserve_idr, today_counts: dict[str,int],
    last_fill_ts: dict[str,float], held_pairs_24h: set[str], now: float) -> list[str]`
    Mengembalikan daftar **alasan penolakan berbahasa Indonesia**; kosong = lolos.
    Atap per order memakai konstanta `MAX_ORDER_IDR` — tidak ada parameter terpisah.
  - `permissions_ok(can_trade: bool, can_withdraw: bool, switch_on: bool) -> str | None`

- [ ] **Step 1: Tulis tes yang gagal**

```python
def _act(pair="btc_idr", amount=100000):
    return rules.Action("BUY", pair, amount, 1.0, 100.0, "uji", True)

def test_rejects_above_atap():
    a = _act(amount=300000)
    assert any("275.000" in r for r in safeguards.gates(a, **_ok()))

def test_rejects_fourth_order_same_asset_today():
    # Review Focus #5
    a = _act()
    out = safeguards.gates(a, **_ok(today_counts={"btc_idr": 3}))
    assert any("3 order" in r for r in out)

def test_allows_fourth_order_other_asset_today():
    a = _act(pair="eth_idr")
    assert safeguards.gates(a, **_ok(today_counts={"btc_idr": 3})) == []

def test_rejects_when_reserve_would_be_touched():
    # available = free - cadangan
    a = _act(amount=100000)
    out = safeguards.gates(a, **_ok(free_idr=120000, reserve_idr=50000))
    assert any("cadangan" in r for r in out)

def test_rejects_buysell_pair_within_24h():
    a = rules.Action("SELL", "btc_idr", 100000, 1.0, 100.0, "uji", True)
    out = safeguards.gates(a, **_ok(held_pairs_24h={"btc_idr"}))
    assert any("24 jam" in r for r in out)

def test_halt_when_can_withdraw_true():
    assert safeguards.permissions_ok(False, True, False) is not None

def test_halt_when_can_trade_disagrees_with_mode():
    # bayangan aktif tetapi kunci sudah bisa trade
    assert safeguards.permissions_ok(True, False, False) is not None
    # sakelar aktif tetapi kunci tidak bisa trade
    assert safeguards.permissions_ok(False, False, True) is not None
```

`_ok()` adalah helper yang mengembalikan semua kunci lain dengan nilai lolos
(`free_idr=10**9`, `reserve_idr=0`, `today_counts={}`, `last_fill_ts={}`,
`held_pairs_24h=set()`, `now=0.0`).

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_safeguards.py -v`
Expected: FAIL — modul belum ada

- [ ] **Step 3: Implementasi `safeguards.py`**

Urutan pemeriksaan: atap per order → **pagu per aset hari ini** (`MAX_PER_ASSET_PER_DAY`)
→ jeda (`now − last_fill_ts[pair] < COOLDOWN_SECONDS`) → anti-loop 24 jam →
`free_idr − reserve_idr < amount_idr`. Setiap penolakan memakai `alerts._rp` untuk angka
rupiah supaya formatnya `Rp 275.000`. `permissions_ok` mengembalikan kalimat Indonesia
atau `None`.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_safeguards.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add safeguards.py tests/test_safeguards.py
git commit -m "feat: gerbang per-aksi (atap, pagu per aset, jeda, cadangan, izin)"
```

---

### Task 3: Model proposal & teks konfirmasi

**Files:**
- Create: `proposal.py`
- Test: `tests/test_proposal.py`

**Interfaces:**
- Consumes: `rules.Action`, `alerts._rp`
- Produces:
  - `@dataclass Proposal`: `id: str`, `kind: str`, `pair: str`, `amount_idr: float`,
    `qty: float`, `price_ref: float`, `rule: str`, `reserve_after: float`,
    `window_s: int`, `created_ts: float`, `status: str`
  - `REACTIVE_WINDOW_S = 90`, `DCA_WINDOW_S = 86400`, `PRICE_DRIFT_TOL = 0.005`
  - `new_proposal(action: rules.Action, *, rule: str, reserve_after: float,
    window_s: int, now: float) -> Proposal`
  - `confirmation_text(p: Proposal) -> str`
  - `is_expired(p: Proposal, now: float) -> bool`
  - `price_still_valid(p: Proposal, current_price: float | None) -> bool`

- [ ] **Step 1: Tulis tes yang gagal**

```python
def _act(kind="BUY", pair="btc_idr", amount=275000, price=1000.0):
    return rules.Action(kind, pair, amount, amount / price, price, "alasan uji", True)

def test_confirmation_text_carries_all_eight_fields():
    p = proposal.new_proposal(_act(), rule="Take-profit +50%", reserve_after=150000,
                              window_s=90, now=0.0)
    t = proposal.confirmation_text(p)
    # qty = 275.000 / 1.000 = 275.0  (bukan 2750.0 — gagal di sini berarti
    # implementasi salah, bukan ekspektasi salah)
    for expected in ("btc_idr", "BELI", "Rp 275.000", "275.0",
                     "1.000", "Take-profit +50%", "0,2%", "Rp 150.000"):
        assert expected in t, expected

def test_expires_at_window_boundary():
    p = proposal.new_proposal(_act(), rule="r", reserve_after=0, window_s=90, now=100.0)
    assert not proposal.is_expired(p, 100 + 89)
    assert proposal.is_expired(p, 100 + 91)

def test_price_drift_over_half_percent_hangs_confirmation():
    p = proposal.new_proposal(_act(price=1000.0), rule="r", reserve_after=0,
                              window_s=90, now=0.0)
    assert proposal.price_still_valid(p, 1004.0)   # +0,4% -> masih valid
    assert not proposal.price_still_valid(p, 1006.0)  # +0,6% -> hangus

def test_missing_price_is_invalid_not_zero():
    assert proposal.price_still_valid(p_with_price(1000.0), None) is False
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_proposal.py -v`
Expected: FAIL — modul belum ada

- [ ] **Step 3: Implementasi `proposal.py`**

`confirmation_text` menyusun satu blok Indonesia dengan **delapan field wajib** (§6.2
spec): `pair`, sisi (`BELI`/`JUAL`), nominal IDR via `alerts._rp`, jumlah koin, harga
limit, aturan pemicu, perkiraan fee **0,2%**, dan sisa cadangan. `price_still_valid`
mengembalikan `False` bila harga `None` (harga hilang **bukan** berarti 0).

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_proposal.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add proposal.py tests/test_proposal.py
git commit -m "feat: model proposal + teks konfirmasi 8 field wajib"
```

---

### Task 4: Gabung aksi kembar (pewaris dedupe `24ad5ef`)

**Files:**
- Create: `actions_merge.py`
- Test: `tests/test_actions_merge.py`

**Interfaces:**
- Consumes: `rules.Action`, `rules.take_profit_actions`, `rules.rebalance_actions`
- Produces: `merge_actions(actions: list[rules.Action]) -> list[rules.Action]`

- [ ] **Step 1: Tulis tes yang gagal**

```python
def test_two_rules_same_pair_become_one_action():
    # Review Focus #4 — bug "jual 25% + 25% = 50%" harus mati di lapisan ini
    h = _holding(pair="btc_idr", qty=1.0, price=100.0, avg_cost=50.0, value=100.0)
    tp = rules.take_profit_actions([h])          # ambang +50% tercapai
    rb = rules.rebalance_actions([h], idr_reserve=0.0)
    merged = actions_merge.merge_actions(tp + rb)
    sells = [a for a in merged if a.kind == "SELL" and a.pair == "btc_idr"]
    assert len(sells) == 1

def test_merged_qty_counted_from_remaining_position():
    # kuantitas hasil gabungan tidak boleh melebihi saldo posisi
    a = rules.Action("SELL", "btc_idr", 999999, 5.0, 100.0, "r", True)
    b = rules.Action("SELL", "btc_idr", 999999, 5.0, 100.0, "r", True)
    merged = actions_merge.merge_actions([a, b])
    assert len(merged) == 1
    assert merged[0].qty <= 5.0
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_actions_merge.py -v`
Expected: FAIL — modul belum ada

- [ ] **Step 3: Implementasi `actions_merge.py`**

Kelompokkan per `(kind, pair)`. Kelompok berisi **lebih dari satu** aksi menjadi
`HOLD`-free satu aksi dengan `qty` diambil dari aksi pertama (satu-satunya yang berasal
dari `take_profit_actions`, yang memang menghitung dari saldo posisi) dan `reason`
digabung dengan `"; "`. Kelompok `BUY` dengan nominal berbeda **jangan** dijumlahkan —
ambil nominal terbesar dan gabung alurnya, karena menggabungkan nominal dua sumber akan
melanggar aturan 6.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_actions_merge.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add actions_merge.py tests/test_actions_merge.py
git commit -m "fix: gabung aksi kembar per (kind, pair) sebelum pesan dibuat"
```

---

### Task 5: Rekonsiliasi DB vs bursa

**Files:**
- Create: `reconcile.py`
- Test: `tests/test_reconcile.py`

**Interfaces:**
- Consumes: `indodax_api.account_info()`, `portfolio.parse_balances`
- Produces: `mismatches(db_state: dict, exchange_state: dict) -> list[str]`

- [ ] **Step 1: Tulis tes yang gagal**

```python
def test_any_balance_gap_is_reported():
    db = {"IDR": 200000.0, "ETH": 0.000001}
    ex = {"IDR": 203394.0, "ETH": 0.000001}
    out = reconcile.mismatches(db, ex)
    assert out and "IDR" in out[0]

def test_identical_state_reports_nothing():
    s = {"IDR": 203394.0}
    assert reconcile.mismatches(s, s) == []

def test_open_orders_must_match_db():
    # Order tertunda mengurangi saldo tersedia — selisih apa pun = halt
    out = reconcile.mismatches({"open_orders": 0}, {"open_orders": 1})
    assert out
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_reconcile.py -v`
Expected: FAIL — modul belum ada

- [ ] **Step 3: Implementasi `reconcile.py`**

Bandingkan per aset dengan toleransi sangat kecil (`1e-8` untuk koin, `1` untuk IDR)
dan jumlah `open_orders`. Setiap selisih memunculkan satu baris Indonesia berbentuk
`"Selisih {aset}: db={..} bursa={..} -> trading mati, cek manual"`. Fungsi ini murni —
pengambilan data ada di `engine`.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_reconcile.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add reconcile.py tests/test_reconcile.py
git commit -m "feat: deteksi selisih portfolio.db vs bursa"
```

---

### Task 6: Eksekutor — satu order, satu kali

**Files:**
- Create: `executor.py`
- Test: `tests/test_executor.py`

**Interfaces:**
- Consumes: `proposal.Proposal`
- Produces:
  - `MAX_CLIENT_ID_LEN = 36` (karakter `[0-9A-Za-z_-]`)
  - `make_client_order_id(prefix: str, now: float) -> str`
  - `class Transport(Protocol)`:
    `post_order(payload: dict) -> dict`,
    `get_order_status(client_order_id: str) -> dict`
  - `class SubmissionUncertain(Exception)`
  - `submit_once(transport, payload: dict, client_order_id: str) -> dict`
  - `payload_for(p: Proposal, client_order_id: str, recv_window_ms: int = 15000) -> dict`

- [ ] **Step 1: Tulis tes yang gagal**

```python
class _ExplodingTransport:
    """Payload terkirim ke jaringan, lalu koneksi putus — kasus paling mahal."""
    def __init__(self):
        self.posted = []
    def post_order(self, payload):
        self.posted.append(payload)
        raise TimeoutError("koneksi putus")

def test_client_order_id_is_stable_charset_and_length():
    cid = executor.make_client_order_id("dca", 1760000000.0)
    assert len(cid) <= 36 and re.fullmatch(r"[0-9A-Za-z_-]+", cid)

def test_never_resends_after_lost_reply():
    # Review Focus #1
    t = _ExplodingTransport()
    with pytest.raises(executor.SubmissionUncertain):
        executor.submit_once(t, {"pair": "btc_idr"}, "fixed-id-1")
    assert len(t.posted) == 1          # tidak ada percobaan kedua

def test_payload_carries_client_id_and_recv_window():
    p = _proposal()
    pl = executor.payload_for(p, "abc", recv_window_ms=15000)
    assert pl["recvWindow"] == 15000
    assert pl["client_order_id"] == "abc"
    assert pl["order_type"] == "limit"
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_executor.py -v`
Expected: FAIL — modul belum ada

- [ ] **Step 3: Implementasi `executor.py`**

`make_client_order_id` = `f"{prefix}-{int(now)}"` yang **dipotong ke 36 karakter** bila
lebih panjang (potongan dari akhir membuat ID tetap unik per detik — bila ternyata perlu
lebih unik, tambahkan hash 4 karakter, keputusan implementor, tetapi batas 36 mutlak).

`submit_once` memanggil `transport.post_order` **tepat satu kali**; hasil apa pun selain
jawaban yang mengandung status sukses → `SubmissionUncertain`. **Tidak ada retry di
dalam fungsi ini** — retry adalah tanggung jawab query status (Task 7), dan mengulang
`post_order` dilarang oleh spesifikasi.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_executor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add executor.py tests/test_executor.py
git commit -m "feat: eksekutor satu-di-udara, tidak pernah mengulang kirim"
```

---

### Task 7: Eksekutor — tanya status ber-backoff

**Files:**
- Modify: `executor.py`
- Test: `tests/test_executor.py`

**Interfaces:**
- Produces: `STATUS_BACKOFF_S = (2, 5, 10)`; `resolve_status(transport,
  client_order_id: str, *, sleep=time.sleep) -> str` dengan hasil
  `"filled" | "cancelled" | "rejected" | "unknown"`

- [ ] **Step 1: Tulis tes yang gagal**

```python
def test_queries_with_same_id_and_documented_backoff():
    # Review Focus #2 — 404 sesaat setelah order sukses
    t = _StatusTransport(responses=[{"code": -2013}, {"code": -2013}, {"status": "FILLED"}])
    slept = []
    out = executor.resolve_status(t, "same-id", sleep=slept.append)
    assert out == "filled"
    assert slept == [2, 5, 10]
    assert t.queried_ids == ["same-id"] * 3

def test_gives_up_unknown_after_three_asks_without_new_id():
    t = _StatusTransport(responses=[{"code": -2013}] * 3)
    ids = []
    t.queried_ids = ids
    assert executor.resolve_status(t, "same-id", sleep=lambda s: None) == "unknown"
    assert set(ids) == {"same-id"}

def test_unknown_is_never_reported_as_cancelled():
    t = _StatusTransport(responses=[{"code": -2013}] * 3)
    assert executor.resolve_status(t, "id", sleep=lambda s: None) != "cancelled"
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_executor.py -v -k status`
Expected: FAIL — `resolve_status` belum ada

- [ ] **Step 3: Implementasi `resolve_status`**

Loop maksimal 3 kali. Untuk i = 0, 1, 2: **`sleep(STATUS_BACKOFF_S[i])` lalu** query
percobaan ke-(i+1) — tidur mendahului setiap query, **termasuk sebelum yang pertama**.
Tiga query karenanya mengamati tidur `[2, 5, 10]`, memenuhi baik tes maupun spesifikasi
("3 kali bertanya, dengan jeda [2,5,10]"). Menunggu 2 dtk sebelum query pertama memang
perilaku yang benar: memberi bursa waktu menetap setelah balasan hilang — ini **bukan**
anti-pattern "tidur tepat setelah submit". Bila query berikutnya sudah terminal,
hentikan sisa tidur. `sleep` disuntikkan supaya tes tidak menunggu 17 detik.
Respons yang tidak mengandung status yang dikenal dianggap `unknown` — **bukan**
`cancelled`. `unknown` diteruskan ke `engine` yang wajib meng-halt (Task 12).

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_executor.py -v`
Expected: PASS (seluruh berkas)

- [ ] **Step 5: Commit**

```bash
git add executor.py tests/test_executor.py
git commit -m "feat: query status dengan backoff [2,5,10] dan ID yang sama"
```

---

### Task 8: Gerbang Telegram — kirim konfirmasi ber-tombol

**Files:**
- Create: `telegram_gate.py`
- Test: `tests/test_telegram_gate.py`

**Interfaces:**
- Consumes: `proposal.confirmation_text`, `alerts.send_telegram` (diganti agar mendukung
  `reply_markup`)
- Produces:
  - `BUTTON_SETUJU = "setuju"`, `BUTTON_TUNDA = "tunda"`, `BUTTON_BATAL = "batal"`
  - `class Telegram` — **antarmuka yang dipakai `engine` dan `main_loop`**:
    - `__init__(token: str, chat_id: str, *, send=None)`
    - `line(text: str) -> bool` — satu baris, tanpa tombol
    - `confirmation(p: Proposal) -> int | None` — balikannya `message_id`
  Semua pemanggilan di Task 11, 12, dan 14 memakai objek `telegram` bertipe ini
  (`telegram.line(...)`, `telegram.confirmation(...)`), sehingga tes cukup menyuntikkan
  objek Telegram dengan `send` yang merekam.

- [ ] **Step 1: Tulis tes yang gagal**

```python
def test_confirmation_has_three_buttons_and_no_approve_all():
    captured = {}
    def fake_send(token, chat_id, text, reply_markup=None):
        captured.update(text=text, markup=reply_markup)
        return 42
    tg = telegram_gate.Telegram("t", "c", send=fake_send)
    assert tg.confirmation(_proposal()) == 42
    labels = [b["text"] for row in captured["markup"]["inline_keyboard"] for b in row]
    assert labels == ["Setuju", "Tunda", "Batal bulan ini"]
    assert "semua" not in " ".join(labels).lower()

def test_expiry_notifies_once_in_one_line():
    sent = []
    tg = telegram_gate.Telegram(
        "t", "c", send=lambda *a, **k: sent.append(a) or True)
    tg.line("BTC konfirmasi kadaluarsa — tidak ada order dikirim.")
    assert len(sent) == 1 and "\n" not in sent[0][2]

def test_line_returns_false_rather_than_raising():
    # gagal kirim = diam, bukan menabrak loop
    tg = telegram_gate.Telegram("t", "c", send=lambda *a, **k: (_ for _ in ()).throw(OSError()))
    assert tg.line("x") is False
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_telegram_gate.py -v`
Expected: FAIL — modul belum ada

- [ ] **Step 3: Implementasi `telegram_gate.Telegram`**

`confirmation` mengirim `proposal.confirmation_text(p)` dengan inline keyboard satu baris
tiga tombol; callback data berformat `"{tombol}:{p.id}"`. `send` disuntikkan; default
`None` menghasilkan `False`/`None` tanpa mengirim (panggilan jaringan asli tidak pernah
muncul di tes). `line` menangkap setiap exception dan mengembalikan `False` — **kegagalan
mengirim pesan tidak boleh menghentikan loop**.

Perluas `alerts.send_telegram` dengan parameter opsional `reply_markup: dict | None = None`
tanpa mengubah perilaku lama (46 tes yang ada tidak boleh berubah hasilnya).

- [ ] **Step 4: Jalankan, pastikan lulus + regresi penuh**

Run: `python -m pytest -q`
Expected: PASS — semua tes, termasuk 46 yang sudah ada

- [ ] **Step 5: Commit**

```bash
git add telegram_gate.py alerts.py tests/test_telegram_gate.py
git commit -m "feat: konfirmasi Telegram dengan tiga tombol, tanpa setuju semua"
```

---

### Task 9: Gerbang Telegram — baca jawaban & `/ulang`

**Files:**
- Modify: `telegram_gate.py`
- Test: `tests/test_telegram_gate.py`

**Interfaces:**
- Produces:
  - `get_updates(token: str, *, offset: int = 0, fetch=None) -> tuple[list[dict], int]`
  - `parse_update(update: dict) -> tuple[str, str] | None`
    → `("setuju", "<proposal_id>")`, `("tunda", ...)`, `("batal", ...)`,
    `("ulang", "")`, atau `None`

- [ ] **Step 1: Tulis tes yang gagal**

```python
def test_parse_callback_button():
    u = {"callback_query": {"id": "1", "data": "setuju:p-9"}}
    assert telegram_gate.parse_update(u) == ("setuju", "p-9")

def test_parse_reply_ulang():
    u = {"message": {"text": "/ulang"}}
    assert telegram_gate.parse_update(u) == ("ulang", "")

def test_unrelated_message_is_none():
    assert telegram_gate.parse_update({"message": {"text": "halo"}}) is None

def test_get_updates_advances_offset():
    fetched = {}
    def fake(token, offset=0):
        fetched["offset"] = offset
        return {"result": [{"update_id": 7}], "ok": True}
    updates, nxt = telegram_gate.get_updates("t", offset=0, fetch=fake)
    assert fetched["offset"] == 0 and nxt == 8 and len(updates) == 1
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_telegram_gate.py -v -k "parse or updates"`
Expected: FAIL

- [ ] **Step 3: Implementasi `parse_update` dan `get_updates`**

`parse_update` hanya mengenali tiga tombol, `/ulang`, dan mengabaikan sisanya (`None`)
— kegagalan mengenali = tidak ada tindakan, bukan asumsi "setuju".
`get_updates` memanggil `getUpdates` dengan `timeout=0` dan `offset`, lalu mengembalikan
`(updates, offset_berikutnya)` = `max(update_id) + 1`; `fetch` disuntikkan.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_telegram_gate.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add telegram_gate.py tests/test_telegram_gate.py
git commit -m "feat: baca jawaban konfirmasi dan perintah /ulang"
```

---

### Task 10: Log mode bayangan

**Files:**
- Create: `shadow_log.py`
- Test: `tests/test_shadow_log.py`

**Interfaces:**
- Consumes: `proposal.Proposal`
- Produces:
  - `record(conn, p: Proposal, outcome: str, note: str = "") -> None`
  - `outcomes = ("proposed", "setuju", "tunda", "batal", "expired", "spread_rejected",
    "gate_rejected", "dca_missed", "halted", "executed")`
  - `daily_summary(conn, day: str) -> list[str]` — baris Indonesia untuk review harian
  - `rejected_count(conn, day: str) -> int`

- [ ] **Step 1: Tulis tes yang gagal**

```python
def test_records_every_proposal_with_its_rule(tmp_path):
    conn = _fresh_db(tmp_path)
    p = proposal.new_proposal(_act(), rule="Take-profit +50%", reserve_after=0,
                              window_s=90, now=0.0)
    shadow_log.record(conn, p, "proposed")
    assert [r["rule"] for r in _rows(conn)] == ["Take-profit +50%"]

def test_summary_lists_each_outcome_in_indonesian(tmp_path):
    conn = _fresh_db(tmp_path)
    p = proposal.new_proposal(_act(), rule="DCA bulanan", reserve_after=0,
                              window_s=86400, now=0.0)
    shadow_log.record(conn, p, "expired")
    lines = shadow_log.daily_summary(conn, "1970-01-01")
    assert len(lines) == 1 and "kedaluarsa" in lines[0]

def test_rejected_count_is_the_shadow_reset_signal(tmp_path):
    conn = _fresh_db(tmp_path)
    for outcome in ("gate_rejected", "spread_rejected"):
        p = proposal.new_proposal(_act(), rule="r", reserve_after=0,
                                  window_s=90, now=0.0)
        shadow_log.record(conn, p, outcome)
    assert shadow_log.rejected_count(conn, "1970-01-01") == 2
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_shadow_log.py -v`
Expected: FAIL — modul belum ada

- [ ] **Step 3: Implementasi `shadow_log.py`**

Buat tabel `proposals(id, ts, day, kind, pair, amount_idr, price_ref, rule,
outcome, note)` bila belum ada (`CREATE TABLE IF NOT EXISTS`). `day` dihitung dengan
`time.localtime` (lihat Global Constraints). `rejected_count` menghitung outcome
`gate_rejected` + `spread_rejected` — **angka inilah yang mereset `shadow_day` ke 0**
(spec §9), sehingga letakkan komentar yang menyebutnya di kode.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_shadow_log.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add shadow_log.py tests/test_shadow_log.py
git commit -m "feat: log usulan harian untuk review mode bayangan"
```

---

### Task 11: Engine — satu tick mengusulkan

**Files:**
- Create: `engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Consumes: `portfolio.refresh`, `rules.take_profit_actions`,
  `rules.reserve_buy_actions`, `actions_merge.merge_actions`, `spread.spread_verdict`,
  `safeguards.gates`, `proposal.*`, `telegram_gate.Telegram`
  (`telegram.line(...)`, `telegram.confirmation(...)`),
  `shadow_log.record`, `alerts._rp`
- Produces:
  - `@dataclass Context`: `holdings`, `prices`, `free_idr`, `reserve_idr`,
    `today_counts`, `last_fill_ts`, `held_pairs_24h`
  - `collect_actions(ctx) -> list[rules.Action]` — **hanya** take-profit + reserve buy
  - `tick(conn, ctx, *, now: float, telegram, shadow: bool,
    verdict=spread.spread_verdict) -> list[proposal.Proposal]`
    `verdict` disuntikkan agar tes tidak membuka jaringan; pemanggil asli memakai default.

- [ ] **Step 1: Tulis tes yang gagal**

```python
def test_v1_never_calls_rebalance():
    # spec §10 — rebalance adalah modul v2. Yang diperiksa adalah SUMBERNYA,
    # bukan nama prosedur yang dijanjikan — tes ini harus bisa gagal
    import inspect, engine
    assert "rebalance_actions" not in inspect.getsource(engine)

def test_every_action_carries_a_reason():
    # alasan ikut tampil di pesan Telegram; tanpa ini pengguna hanya melihat angka
    ctx = _rich_ctx()
    acts = engine.collect_actions(ctx)
    assert acts and all(a.reason != "" for a in acts)

def test_spread_rejected_is_notified_and_logged(tmp_path):
    conn = _fresh_db(tmp_path)
    sent = []
    ctx = _ctx()
    engine.tick(conn, ctx, now=0.0, telegram=_tg(sent), shadow=True,
                verdict=lambda pair, **k: (False, 94.0))   # seperti bchidr
    assert any("tidak dieksekusi" in t for t in sent)
    assert shadow_log.rejected_count(conn, _today()) >= 1

def test_stale_numbers_are_never_reused_across_proposals(tmp_path):
    # spec 7.1 butir 3 — sinyal kedaluarsa dihitung ulang dari nol
    conn = _fresh_db(tmp_path)
    ctx = _ctx(price=1000.0)
    first = engine.tick(conn, ctx, now=0.0, telegram=_tg([]), shadow=True,
                        verdict=lambda p, **k: (True, 1.0))
    ctx2 = _ctx(price=1400.0)          # harga berubah setelah kadaluarsa
    second = engine.tick(conn, ctx2, now=600.0, telegram=_tg([]), shadow=True,
                         verdict=lambda p, **k: (True, 1.0))
    assert first and second
    assert second[0].price_ref == 1400.0      # bukan angka lama 1000.0
    assert second[0].id != first[0].id         # konfirmasi baru yang berdiri sendiri

def test_shadow_writes_nothing_to_exchange():
    # tidak ada Transport dibuat — jalur fisik tidak ada
    calls = {"n": 0}
    ctx = _ctx_with_spread(5.0)
    engine.tick(conn, ctx, now=0.0, telegram=_tg([]), shadow=True)
    assert calls["n"] == 0

def test_duplicate_rules_produce_single_proposal():
    ctx = _ctx_where_two_rules_fire()
    props = engine.tick(conn, ctx, now=0.0, telegram=_tg([]), shadow=True)
    sells = [p for p in props if p.kind == "SELL" and p.pair == "btc_idr"]
    assert len(sells) <= 1
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_engine.py -v`
Expected: FAIL — modul belum ada

- [ ] **Step 3: Implementasi `engine.collect_actions` dan `engine.tick`**

`collect_actions` menggabungkan **hanya** `take_profit_actions(holdings)` dan
`reserve_buy_actions(holdings, idr_reserve=ctx.reserve_idr)` lalu memanggil
`merge_actions`. `rebalance_actions` **tidak boleh muncul** di `engine.py` —
dipastikan oleh `test_v1_never_calls_rebalance` di Step 1 (jangan mendefinisikan
fungsi bernama sama di sini: Python akan menimpanya dan satu tes hilang diam-diam).

`tick` berurutan: `merge_actions` → untuk tiap aksi: `verdict(pair)` → **tiga hasil
berbeda, tidak pernah dicampur**:
- `True` → lolos, lanjut ke `safeguards.gates`;
- `False` → spread gugur → `telegram.line(...)` satu baris +
  `shadow_log.record(..., "spread_rejected")`, **lanjut** ke aksi berikutnya;
- `None` → **harga hilang = BERHENTI (spec §6.3)** → `record(..., "halted")`,
  satu notifikasi, dan `tick` mengembalikan `no_action` **tanpa usulan apa pun**.
  `None` bukan `False`: menolak lalu lanjut menyamakan "depth kosong" dengan
  "spread jelek", dan yang pertama berarti data tidak ada.

Lalu `safeguards.gates` (ditolak → `telegram.line(...)` + record `gate_rejected`, lanjut) →
`proposal.new_proposal` + `telegram.confirmation(...)` + record `proposed`.
**Tidak ada panggilan eksekusi** di tick ini. Setiap proposal dibuat ulang dari data
`ctx` yang segar — `tick` tidak pernah menyimpan angka dari putaran sebelumnya
(spec §7.1 butir 3).

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: tick engine mengusulkan aksi lewat gerbang spread dan safeguard"
```

---

### Task 12: Engine — konfirmasi sampai eksekusi

**Files:**
- Modify: `engine.py`
- Test: `tests/test_engine.py`

**Interfaces:**
- Produces: `on_answer(conn, ctx, *, p: proposal.Proposal, answer: str, now: float,
  transport, telegram, shadow: bool, verdict=spread.spread_verdict) -> str`
  dengan hasil `"sent" | "ignored" | "halted" | "gate_blocked"`

- [ ] **Step 1: Tulis tes yang gagal**

```python
def test_expired_proposal_writes_no_order_and_nothing_to_db(tmp_path):
    conn = _fresh_db(tmp_path)
    p = _proposal(window_s=90, created_ts=0.0)
    t = _FakeTransport()
    out = engine.on_answer(conn, _ctx(), p=p, answer="setuju", now=120.0,
                           transport=t, telegram=_tg([]), shadow=True)
    assert t.posted == [] and out == "ignored"
    assert _db_rows(conn) == []          # tidak ada tulisan sebelum bursa menjawab

def test_halt_when_status_unknown_keeps_db_untouched():
    # Review Focus #2 — 404 abadi bukan berarti batal
    t = _FakeTransport(status_sequence=["unknown", "unknown", "unknown"])
    out = engine.on_answer(conn, _ctx(), p=_fresh_proposal(), answer="setuju", now=0.0,
                           transport=t, telegram=_tg([]), shadow=False)
    assert out == "halted" and _db_rows(conn) == []

def test_spread_rechecked_at_execution_time():
    # Review Focus #3 — lolos saat komposisi, melebar saat eksekusi
    p = _proposal(price_ref=1000.0)      # saat komposisi 10 bps
    t = _FakeTransport(depth_bps=45.0)
    out = engine.on_answer(conn, _ctx(), p=p, answer="setuju", now=10.0,
                           transport=t, telegram=_tg([]), shadow=False)
    assert out == "gate_blocked" and t.posted == []

def test_halt_when_can_withdraw_flips_true():
    t = _FakeTransport(can_withdraw=True)
    out = engine.on_answer(conn, _ctx(), p=_fresh_proposal(), answer="setuju", now=0.0,
                           transport=t, telegram=_tg([]), shadow=False)
    assert out == "halted" and t.posted == []

def test_writes_db_only_after_exchange_answers(tmp_path):
    conn = _fresh_db(tmp_path)
    t = _FakeTransport(status_sequence=["filled"])
    engine.on_answer(conn, _ctx(), p=_fresh_proposal(), answer="setuju", now=0.0,
                     transport=t, telegram=_tg([]), shadow=False)
    assert _db_rows(conn) == [{"status": "filled"}]
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_engine.py -v -k on_answer`
Expected: FAIL — `on_answer` belum ada

- [ ] **Step 3: Implementasi `on_answer`**

Urutan, dan setiap langkah yang gagal **return `"halted"` tanpa menyentuh DB**:

1. `answer != "setuju"` → `"ignored"` (batal/tunda = tidak ada tindakan).
2. `proposal.is_expired` → `"ignored"`.
3. `shadow is True` → `"ignored"` + `shadow_log.record(..., "setuju")` — mode bayangan
   **tidak pernah** membuat `transport`; bila `transport` terisi padahal `shadow`,
   itu bug, bukan fitur.
4. `verdict(pair)` **diulang pada harga saat ini** — lihat Review Focus #3.
5. `safeguards.gates` ulang ( saldo, atap, pagu per aset, jeda, anti-loop ).
6. `safeguards.permissions_ok(can_trade, can_withdraw, switch_on=True)`.
7. `executor.submit_once(...)` → `SubmissionUncertain` → **return `"halted"`**
   (lanjut ke Task 7 query status, jangan pernah kirim ulang).
8. `executor.resolve_status(...)` → `"unknown"` → **`"halted"` + notifikasi, DB disentuh
   nol kali**; `"filled" | "cancelled" | "rejected"` → **baru** tulis satu baris ke DB
   dan `shadow_log.record(..., "executed")`.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_engine.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add engine.py tests/test_engine.py
git commit -m "feat: alur konfirmasi -> eksekusi dengan DB hanya setelah bursa menjawab"
```

---

### Task 13: Jendela DCA, pengingat, dan DND

**Files:**
- Create: `dca_window.py`
- Test: `tests/test_dca_window.py`

**Interfaces:**
- Produces:
  - `DCA_SEND_HOUR = 9`, `DCA_WINDOW_H = 24`, `DND = (23, 7)`
  - `REMINDER_HOURS = (15, 21)` — 03.00 sengaja tidak ada
  - `in_dnd(now_ts: float) -> bool`
  - `dca_state(conn, now_ts: float) -> tuple[str, float]`
    → `("none" | "awaiting" | "confirmed" | "missed", sisa_detik)`
  - `should_remind(conn, now_ts: float) -> bool`
  - `mark_pending(conn, ts: float)`, `mark_missed(conn, ts: float)` —
    keduanya dipakai langsung oleh tes di bawah, jadi wajib ada di Interfaces.

- [ ] **Step 1: Tulis tes yang gagal**

```python
def test_dnd_window_covers_23_to_07():
    assert dca_window.in_dnd(_ts(hour=23, minute=0)) is True
    assert dca_window.in_dnd(_ts(hour=2, minute=0)) is True
    assert dca_window.in_dnd(_ts(hour=7, minute=0)) is False
    assert dca_window.in_dnd(_ts(hour=12, minute=0)) is False

def test_reminder_only_at_15_and_21():
    # Review Focus #5 — pengingat 03.00 jatuh dalam DND, tidak boleh dikirim
    assert dca_window.REMINDER_HOURS == (15, 21)
    assert dca_window.in_dnd(_ts(hour=3, minute=0)) is True

def test_missed_after_24_hours_writes_nothing():
    conn = _fresh_db(tmp_path)
    dca_window.mark_pending(conn, _ts(hour=9, minute=0))
    state, _ = dca_window.dca_state(conn, _ts(day=2, hour=9, minute=1))
    assert state == "missed"

def test_new_schedule_appears_next_month_only():
    conn = _fresh_db(tmp_path)
    dca_window.mark_missed(conn, _ts(hour=10, minute=0))
    # hari berikutnya di bulan yang sama: tidak ada jadwal baru
    assert dca_window.dca_state(conn, _ts(day=2, hour=9, minute=0))[0] == "missed"
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_dca_window.py -v`
Expected: FAIL — modul belum ada

- [ ] **Step 3: Implementasi `dca_window.py`**

Simpan `last_dca_date` (kolom sudah ada di `portfolio.db`) sebagai penanda bulan berjalan.
`should_remind` bernilai `True` **hanya** bila jam lokal dalam `REMINDER_HOURS`,
`in_dnd` bernilai `False`, dan DCA masih `awaiting` — pengingat **pasif**: tidak pernah
mengubah `Tunda` menjadi `Setuju`, tidak pernah mengirim apa pun selain satu baris.
`dca_state` menghitung jendela 24 jam dari pengiriman; lewat → `"missed"`.
Pencatatan `shadow_log.record(..., "dca_missed")` dilakukan **oleh `dca_window`
sendiri** tepat saat transisi ke `missed` (sekali saja, bukan setiap siklus) —
dengan begitu pemanggilnya (Task 14) tidak perlu mengulang buku catatan ini dan
tiap DCA yang terlewat tercatat pada siklus yang tepat.

- [ ] **Step 4: Jalankan, pastikan lulus**

Run: `python -m pytest tests/test_dca_window.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dca_window.py tests/test_dca_window.py
git commit -m "feat: jendela DCA 24 jam, pengingat pasif, DND menang"
```

---

### Task 14: Loop utama, sakelar, dan integrasi

**Files:**
- Create: `main_loop.py`
- Test: `tests/test_main_loop.py`

**Interfaces:**
- Consumes: semua modul Task 1–13
- Produces:
  - `HEARTBEAT_S = 300`, `ACTIVE_HOURS = (7, 23)`
  - `load_state(conn) -> dict` berisi `v1_enabled`, `shadow_day`, `last_dca_date`
  - `should_run_habitual(now_ts: float) -> bool`
  - `set_shadow_day(conn, day: int) -> None`, `apply_shadow_gate(conn) -> None`
  - `tick_cycle(conn, *, now, telegram, transport, shadow, db_state=None,
    exchange_state=None, verdict=spread.spread_verdict) -> str`
    `db_state`/`exchange_state` `None` = rekonsiliasi dilewati (untuk tes yang tidak
    mengujinya); non-`None` = **wajib** diperiksa **sebelum** usulan apa pun.
  - `apply_ulang(conn, ctx, *, now, telegram, shadow, verdict) -> list[proposal.Proposal]`
    — meneruskan `/ulang` pengguna ke `engine.tick` **segera**, memakai data saat itu juga
  - `main(argv=None) -> int` — `0` sukses, `1` berhenti (konsisten dengan `scheduler.py`)

- [ ] **Step 1: Tulis tes yang gagal**

```python
def test_habitual_skips_dnd_hours():
    assert main_loop.should_run_habitual(_ts(hour=3)) is False
    assert main_loop.should_run_habitual(_ts(hour=12)) is True

def test_shadow_day_resets_to_zero_on_single_rejection(tmp_path):
    conn = _fresh_db(tmp_path)
    main_loop.set_shadow_day(conn, 9)
    p = proposal.new_proposal(_act(), rule="r", reserve_after=0, window_s=90, now=0.0)
    shadow_log.record(conn, p, "spread_rejected")
    main_loop.apply_shadow_gate(conn)
    assert main_loop.load_state(conn)["shadow_day"] == 0   # reset dari nol, bukan 9+1

def test_execution_blocked_until_zero_rejections_for_7_days(tmp_path):
    conn = _fresh_db(tmp_path)
    main_loop.set_shadow_day(conn, 6)
    assert main_loop.load_state(conn)["v1_enabled"] is False
    main_loop.set_shadow_day(conn, 7)
    assert main_loop.load_state(conn)["v1_enabled"] is True

def test_tick_never_builds_transport_in_shadow():
    built = []
    main_loop.tick_cycle(conn, now=0.0, telegram=_tg([]), transport=None, shadow=True)
    assert built == []          # jalur fisik ke bursa tidak ada saat bayangan

def test_reconcile_mismatch_halts_before_any_proposal(tmp_path):
    conn = _fresh_db(tmp_path)
    out = main_loop.tick_cycle(conn, now=0.0, telegram=_tg([]), transport=_FakeTransport(),
                               shadow=False, db_state={"IDR": 1},
                               exchange_state={"IDR": 2})
    assert out == "halted"

def test_ulang_reproposes_immediately_with_fresh_data(tmp_path):
    # spec 7.1 butir 4 — hanya pengguna yang memicu ulang, dan datanya baru
    conn = _fresh_db(tmp_path)
    props = main_loop.apply_ulang(conn, _ctx(price=1400.0), now=9999.0,
                                  telegram=_tg([]), shadow=True,
                                  verdict=lambda p, **k: (True, 1.0))
    assert props and props[0].price_ref == 1400.0
```

- [ ] **Step 2: Jalankan, pastikan gagal**

Run: `python -m pytest tests/test_main_loop.py -v`
Expected: FAIL — modul belum ada

- [ ] **Step 3: Implementasi `main_loop.py`**

`tick_cycle` berurutan dan **berhenti di titik pertama yang gagal**:

1. Bila `db_state` dan `exchange_state` keduanya non-`None`:
   `reconcile.mismatches(...)` → ada → `telegram.line("Selisih ... -> trading mati")` +
   `shadow_log.record(..., "halted")` → return `"halted"` (**sebelum** ada usulan apa pun).
2. `safeguards.permissions_ok` → bukan `None` → `"halted"`.
3. **Polling Telegram**: `telegram_inbox.get_updates(...)` → tiap jawaban
   `parse_update` → `engine.on_answer(...)`; bila `text == "/ulang"` →
   `main_loop.apply_ulang(...)`. **Tanpa langkah ini gerbang bersifat tulis-saja dan
   setiap konfirmasi akan kedaluwarsa tanpa pernah terjawab (spec §7.1 butir 4)** —
   ini bukan pelengkap. Proses ini **satu-satunya** konsumen `getUpdates`.
4. `dca_window.should_remind` → `telegram.line(...)` pengingat pasif.
5. `engine.tick(...)` → daftar proposal.
6. `main_loop.apply_shadow_gate(conn)` → hitung `rejected_count`; bila > 0 →
   `shadow_day = 0`, `v1_enabled = False`.

`apply_ulang` memanggil `engine.tick` langsung dengan `now` yang diberikan — **tanpa**
menunggu heartbeat berikutnya, dan tanpa memakai angka dari proposal lama.

`transport` dibuat **hanya** bila `shadow is False` dan `v1_enabled is True`
(konstruksi `IndodaxTransport` ada di `main()`, bukan di `tick_cycle` — tes
menyuntikkan `transport=None`).

`main()` membaca `.env` memakai `_env` dari `scheduler.py`, membangun loop
`while True: sleep(HEARTBEAT_S)` dengan jeda hanya di jam aktif, dan **gagal dengan
exit 1** persis seperti perilaku `scheduler.py` yang sudah disepakati.

- [ ] **Step 4: Jalankan, pastikan seluruh suite**

Run: `python -m pytest -q`
Expected: PASS — seluruh suite, termasuk 46 tes lama (tidak boleh ada yang berubah hasil)

- [ ] **Step 5: Commit**

```bash
git add main_loop.py tests/test_main_loop.py
git commit -m "feat: loop utama, sakelar mode bayangan, rekonsiliasi dulu"
```

---

## Setelah rencana ini

- **Bukan** bagian rencana: membuat kunci trade, menjalankan mode bayangan sungguhan,
  menyalakan sakelar. Ketiganya keputusan pengguna (spec §16).
- Pengukuran spread beberapa hari untuk mengunci `MAX_SPREAD_BPS` (spec §14) berjalan
  paralel dan **tidak** memblokir rencana — angkanya satu konstanta.
- Jalur eksekusi **wajib** diuji dengan `Transport` palsu. Tidak ada tes yang boleh
  mengirim HTTP ke `indodax.com/tapi` atau `api.indodax.com`.
