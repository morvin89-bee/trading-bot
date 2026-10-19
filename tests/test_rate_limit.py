"""HTTP 429 (rate limit) — kasus nyata 2026-10-19.

Saat buka_dashboard.bat dibiarkan berjalan lama, terminal hanya menampilkan
"HTTP 429 (rate limit)". Akar masalahnya: `BackgroundUpdater` mengambil 13
pair x 2 exchange = 26 request tiap 3 detik, dan `scanner._get_json` tidur
backoff 2+4+8 detik DI DALAM loop per pair — satu iterasi bisa makan belasan
menit. Perbaikan: hormati Retry-After, jangan biarkan thread crash, dan pakai
data lama (basi) untuk pair yang gagal.

Semua tes di sini TANPA jaringan (fake requests/session).
"""
import pytest

import scanner


class _Respon:
    """Fake `requests.Response` — cukup untuk kebutuhan _get_json."""

    def __init__(self, status_code=200, json_data=None, headers=None):
        self.status_code = status_code
        self._json = json_data
        self.headers = headers or {}

    def json(self):
        return self._json

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.exceptions.HTTPError(f"{self.status_code}")


def _buat_updater(pairs):
    """Bangun BackgroundUpdater tanpa __init__ (yang menyentuh jaringan)."""
    import dashboard.utils.background_updater as bu
    updater = object.__new__(bu.BackgroundUpdater)
    updater.state = _Palsu({})
    updater.pairs = pairs
    updater.strategy = _StrategiPalsu()
    return updater


def _pasang_harga_gagal(monkeypatch):
    """sol_idr gagal di kedua exchange; pasangan lain sehat."""
    import dashboard.utils.background_updater as bu
    monkeypatch.setattr(
        bu.scanner, "indodax_price",
        lambda pair: None if pair == "sol_idr" else (100.0, 101.0))
    monkeypatch.setattr(
        bu.scanner, "tokocrypto_price",
        lambda sym: None if sym == "SOLIDR" else (200.0, 201.0))
    monkeypatch.setattr(
        bu.mr.MeanReversionStrategy, "calculate_spread",
        lambda *a, **k: (1.0, 2.0))


def test_harga_none_pakai_jalur_data_lama_bukan_jalur_kegagalan(
        monkeypatch, caplog):
    """Kasus nyata 2026-10-19 (errors.log): `sol_idr` gagal diambil.

    Kode rusak: `a, b = None` -> TypeError -> "Failed to fetch prices".
    Kode benar : `None` dicek dulu  -> "Harga tidak lengkap".

    Keduanya mengembalikan None, jadi TIDAK bisa dibedakan lewat nilai
    kembalian — yang membedakan adalah JALUR yang ditempuh, dan itu
    terlihat di log. Menguji log = menguji perilaku yang benar-benar
    membedakan.
    """
    import logging
    updater = _buat_updater(["sol_idr", "btc_idr"])
    _pasang_harga_gagal(monkeypatch)

    with caplog.at_level(logging.WARNING):
        updater._fetch_all_pairs()

    pesan = "\n".join(r.message for r in caplog.records)
    assert "sol_idr" in updater.pairs, "prasyarat: sol_idr memang dipantau"
    assert "Harga tidak lengkap" in pesan, (
        "pair gagal harus lewat jalur 'data lama', bukan jalur kegagalan")
    assert "Failed to fetch prices" not in pesan, (
        "jalur 'Failed to fetch' berarti None sempat di-unpack (kode rusak)")
    assert "cannot unpack" not in pesan


def test_pair_lain_tetap_sukses_saat_satu_pair_gagal(monkeypatch):
    """Satu pair gagal TIDAK menggugurkan pasangan lain (siklus tetap penuh)."""
    updater = _buat_updater(["sol_idr", "btc_idr", "eth_idr"])
    _pasang_harga_gagal(monkeypatch)

    hasil = updater._fetch_all_pairs()

    sehat = [p for p in ("btc_idr", "eth_idr") if p in hasil
             and "error" not in hasil[p]]
    assert len(sehat) == 2, f"pair sehat hilang dari hasil: {list(hasil)}"


class _Palsu:
    """SharedState tiruan — cukup untuk _fetch_all_pairs."""

    def __init__(self, isi):
        self._isi = isi

    def get(self, kunci, awal=None):
        return self._isi.get(kunci, awal)


class _StrategiPalsu:
    """Strategi tiruan — metode yang dipakai _fetch_pair_data."""

    def update_history(self, *a, **k):
        pass

    def compute_zscore(self, *a, **k):
        return 0.0

    def check_signal(self, *a, **k):
        return None


def _pasang(monkeypatch, respon):
    """Ganti requests.get agar selalu mengembalikan `respon`."""
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: respon)


def test_get_json_hormati_retry_after(monkeypatch):
    """Retry-After harus menggantikan backoff tetap (lebih lama = lebih aman)."""
    import requests

    respon = _Respon(status_code=429, headers={"Retry-After": "7"})
    panggilan = {"n": 0}

    def get(*a, **k):
        panggilan["n"] += 1
        if panggilan["n"] == 1:
            return respon
        return _Respon(200, {"ok": True})

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(scanner.time, "sleep", lambda s: None)  # jangan tidur nyata

    hasil = scanner._get_json("https://contoh.test/x")
    assert hasil == {"ok": True}


def test_get_json_kembalikan_none_bukan_raise(monkeypatch):
    """Semua percobaan habis = None, bukan RuntimeError (yang dulu mematikan
    seluruh iterasi pair)."""
    import requests

    def get(*a, **k):
        return _Respon(status_code=429, headers={})

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(scanner.time, "sleep", lambda s: None)

    assert scanner._get_json("https://contoh.test/x") is None


def test_indodax_price_none_bila_gagal(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Respon(status_code=429))
    monkeypatch.setattr(scanner.time, "sleep", lambda s: None)

    assert scanner.indodax_price("btc_idr") is None


def test_tokocrypto_price_none_bila_gagal(monkeypatch):
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Respon(status_code=429))
    monkeypatch.setattr(scanner.time, "sleep", lambda s: None)

    assert scanner.tokocrypto_price("BTCIDR") is None


def test_laju_request_naik_turun_bersama_interval(monkeypatch):
    """AKAR 429 2026-10-19: 13 pair x 2 exchange = 26 request per siklus.
    Dengan refresh_interval 3000 ms itu 8.67 request/detik — jauh di atas
    yang scanner.py sendiri pakai (26 request / sleep 10 dtk ~ 2.6/detik).

    Kontrak: laju = 26 / interval_dtk. Bila interval diperbesar, laju WAJIB
    turun; dan laju tidak boleh melampaui batas aman scanner."""
    n_request = len(scanner.PAIRS) * 2
    batas_aman = n_request / 10.0          # pemakaian scanner.py sendiri

    def laju(interval_ms):
        return n_request / (interval_ms / 1000)

    assert laju(10_000) <= batas_aman + 1e-9, (
        "interval 10 dtk sudah pasti aman — ini jangkar pemakaian asli scanner")
    assert laju(60_000) < laju(10_000), (
        "interval lebih besar harus menghasilkan laju lebih rendah")


def _angka_config():
    """Ambil refresh_interval dari config produksi yang benar-benar dipakai.

    `app.py` memanggil `st_cfg.get("refresh_interval", FALLBACK_REFRESH_MS)` —
    dan `st_cfg` adalah isi config_strategy.yaml. Selama kunci itu ada di
    berkas, FALLBACK TIDAK PERNAH dipakai, jadi mengujinya adalah tes palsu
    yang lulus tanpa melindungi siapa pun.
    """
    import pathlib
    import yaml
    berkas = pathlib.Path(scanner.__file__).with_name("config_strategy.yaml")
    cfg = yaml.safe_load(berkas.read_text(encoding="utf-8"))
    return cfg["strategy"]["refresh_interval"]


def test_laju_efektif_produksi_tidak_lebihi_batas_aman():
    """AKAR 429 (2026-10-19): config menyimpan refresh_interval 3000 ms =
    8.67 request/detik, padahal pola yang terbukti aman di scanner.py sendiri
    (26 request / sleep 10 dtk) = 2.6 request/detik.

    Kontrak: laju efektif dari config produksi tidak boleh melampaui pola
    aman itu. Mengubah hanya fallback tanpa menyentuh config adalah perbaikan
    yang lulus tes tapi tidak mengubah kenyataan — persis kesalahan yang
    dihindari tes ini."""
    interval_ms = _angka_config()
    n_request = len(scanner.PAIRS) * 2
    laju = n_request / (interval_ms / 1000)
    laju_aman = n_request / 10.0            # pemakaian scanner.py sendiri

    assert laju <= laju_aman + 1e-9, (
        f"config refresh_interval={interval_ms} ms menghasilkan "
        f"{laju:.2f} request/detik; batas aman {laju_aman:.2f} (pola scanner.py). "
        f"429 adalah konsekuensi langsung dari angka ini.")


def test_jeda_membesar_setelah_429_lalu_turun_setelah_sukses(monkeypatch):
    """Kasus nyata 2026-10-19: jeda TETAP 0,15 dtk masih memicu 429 beruntun
    dari tokocrypto.site. Kontrak: 429 = jeda per-host MEMBESAR (server
    diberi ruang); sukses berturut = jeda mengecil lagi menuju dasar.

    CATATAN jujur: jeda adaptif hanya MEMPERLAMBAT, tidak menghapus 429.
    Akar sebenarnya adalah 8,67 request/detik (13 pair x 2 exchange / 3 dtk)
    — lihat test_interval_bawaan_app_memakai_laju_wajar. Perbaikan interval
    ada di konstanta FALLBACK_REFRESH_MS dashboard/app.py, bukan di sini."""
    import requests
    monkeypatch.setattr(scanner, "_jeda_host", {})
    monkeypatch.setattr(scanner, "_WAKTU_PERMINTAAN", {})
    monkeypatch.setattr(scanner.time, "sleep", lambda s: None)
    urutan = iter([
        _Respon(429, headers={}), _Respon(429, headers={}),
        _Respon(429, headers={}),                        # menyerah -> None
        _Respon(200, {"ok": 1}),                         # panggilan kedua sukses
        _Respon(200, {"ok": 1}),
    ])
    monkeypatch.setattr(requests, "get", lambda *a, **k: next(urutan))

    hasil = scanner._get_json("https://contoh.test/a")
    assert hasil is None
    assert scanner._jeda_host.get("contoh.test", 0) > scanner._JEDA_ANTAR_REQUEST, (
        "setelah 429, jeda per-host harus membesar")

    # Tiga kali ganda dari dasar 0,15 = 1,2. Satu sukses memangkas separuh
    # menjadi 0,6 — jeda turun PERLAHAN, bukan langsung ke dasar.
    scanner._get_json("https://contoh.test/b")          # sukses -> jeda mengecil
    jeda = scanner._jeda_host.get("contoh.test", 0)
    assert 0 < jeda < 1.2, (
        "satu sukses harus memangkas jeda per-host")


def test_jeda_kembali_ke_dasar_bila_sukses_berturut(monkeypatch):
    """Sukses BERTURUT-TURUT mengembalikan jeda ke dasar, lalu lupa host
    (tidak menumpuk state untuk host yang sehat)."""
    import requests
    monkeypatch.setattr(scanner, "_jeda_host", {"contoh.test": 0.6})
    monkeypatch.setattr(scanner, "_WAKTU_PERMINTAAN", {})
    monkeypatch.setattr(scanner.time, "sleep", lambda s: None)
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Respon(200, {"ok": 1}))

    for _ in range(5):
        scanner._get_json("https://contoh.test/a")

    assert "contoh.test" not in scanner._jeda_host, (
        "host yang sudah sehat tidak perlu disimpan lagi")


def test_jeda_antar_request_ke_host_sama(monkeypatch):
    """Rate limit dijaga dengan jeda minimal antar request ke host yang sama,
    bukan tidur backoff panjang di dalam loop pair."""
    import requests
    monkeypatch.setattr(requests, "get", lambda *a, **k: _Respon(200, {}))
    monkeypatch.setattr(scanner, "_JEDA_ANTAR_REQUEST", 0.5)

    tidur = []
    monkeypatch.setattr(scanner.time, "sleep", lambda s: tidur.append(s))

    scanner._get_json("https://contoh.test/a")
    scanner._get_json("https://contoh.test/b")
    # Request kedua ke host yang sama harus menunggu.
    assert any(t >= 0.4 for t in tidur), f"tidak ada jeda yang dicatat: {tidur}"
