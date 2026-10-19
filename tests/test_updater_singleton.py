"""Satu thread pengambilan per proses — kasus nyata 14-19 Okt 2026.

errors.log: ribuan HTTP 429 per jam ke tokocrypto.site. Akar tambahannya:
tiap sesi Streamlit (tab baru / reload) membangun BackgroundUpdater BARU dan
thread lama tidak pernah mati — beban request berlipat sebanyak jumlah sesi,
dan riwayat z-score strategi (yang singleton global) terganda tiap siklus.

Kontrak: berapa pun jumlah sesi, pabrik `create_background_updater`
mengembalikan SATU updater yang sama dan hanya SATU thread dibangun;
updater yang mati diganti yang baru, bukan dipakai ulang.

Semua tes TANPA jaringan: `start` diganti tiruan yang tidak membangun
thread sungguhan.
"""
import dashboard.utils.background_updater as bu


class _ThreadPalsu:
    def __init__(self, hidup=True):
        self.hidup = hidup

    def is_alive(self):
        return self.hidup


class _StatePalsu:
    def get(self, kunci, awal=None):
        return awal


class _StrategiPalsu:
    def update_history(self, *a, **k):
        pass

    def compute_zscore(self, *a, **k):
        return 0.0

    def check_signal(self, *a, **k):
        return None


def _pasang(monkeypatch):
    """Nol-kan singleton; rekam tiap start tanpa thread/jaringan nyata."""
    dibangun = []

    def start_palsu(self):
        self._running = True
        self._thread = _ThreadPalsu()
        dibangun.append(self)

    monkeypatch.setattr(bu, "_UPDATER_SATUAN", None)
    monkeypatch.setattr(bu.BackgroundUpdater, "start", start_palsu)
    return dibangun


def _sesi_jalankan(u):
    """Meniru app.py: sesi baru hanya start bila belum berjalan."""
    if not u.is_running():
        u.start()


def test_dua_sesi_memakai_updater_yang_sama(monkeypatch):
    dibangun = _pasang(monkeypatch)

    a = bu.create_background_updater(
        _StatePalsu(), _StrategiPalsu(), ["btc_idr"], 9, "x.json")
    b = bu.create_background_updater(
        _StatePalsu(), _StrategiPalsu(), ["eth_idr"], 9, "x.json")

    assert a is b, "sesi kedua harus memakai updater sesi pertama, bukan thread baru"

    _sesi_jalankan(a)               # sesi pertama
    _sesi_jalankan(b)               # sesi kedua (tab/reload)
    assert len(dibangun) == 1, "dua sesi = satu start = satu thread"


def test_updater_matih_diganti_yang_baru(monkeypatch):
    dibangun = _pasang(monkeypatch)

    a = bu.create_background_updater(
        _StatePalsu(), _StrategiPalsu(), ["btc_idr"], 9, "x.json")
    _sesi_jalankan(a)               # sesi pertama menyalakannya
    a._thread.hidup = False          # thread lama mati
    a._running = False
    b = bu.create_background_updater(
        _StatePalsu(), _StrategiPalsu(), ["btc_idr"], 9, "x.json")

    assert b is not a, "updater mati harus diganti, jangan dipakai ulang"

    _sesi_jalankan(b)               # pengganti wajib benar-benar dijalankan
    assert len(dibangun) == 2, "pengganti harus benar-benar dijalankan"
