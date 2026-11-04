"""Tes price feed mandiri pengganti scanner.py (tanpa jaringan)."""
import price_feed


class _Respon:
    def __init__(self, status_code=200, data=None, headers=None):
        self.status_code = status_code
        self._data = data
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._data


def test_indodax_price_membaca_bid_ask(monkeypatch):
    resp = _Respon(data={"ticker": {"buy": "1000", "sell": "1010"}})
    monkeypatch.setattr(price_feed.requests, "get", lambda *a, **k: resp)
    assert price_feed.indodax_price("btc_idr") == (1000.0, 1010.0)


def test_indodax_price_none_saat_gagal(monkeypatch):
    def _gagal(*args, **kwargs):
        raise RuntimeError("jaringan mati")

    monkeypatch.setattr(price_feed.requests, "get", _gagal)
    assert price_feed.indodax_price("btc_idr") is None


def test_indodax_price_none_saat_data_rusak(monkeypatch):
    resp = _Respon(data={"ticker": {"buy": "bukan-angka", "sell": "1010"}})
    monkeypatch.setattr(price_feed.requests, "get", lambda *a, **k: resp)
    assert price_feed.indodax_price("btc_idr") is None


def test_indodax_price_menghormati_retry_after(monkeypatch):
    resp = _Respon(status_code=429, headers={"Retry-After": "1"})
    tidur = []
    monkeypatch.setattr(price_feed.requests, "get", lambda *a, **k: resp)
    monkeypatch.setattr(price_feed.time, "sleep", lambda s: tidur.append(s))
    assert price_feed.indodax_price("btc_idr") is None
    assert tidur == [1.0]
