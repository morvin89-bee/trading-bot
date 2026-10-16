"""Tes eksekutor: satu order, satu kali — tidak pernah membuka jaringan.
"""
import re
import pytest

import executor
import proposal
import rules


def _proposal():
    """Helper yang mengembalikan Proposal biasa untuk uji payload_for."""
    action = rules.Action(
        kind="BUY",
        pair="btc_idr",
        amount_idr=275000.0,
        qty=275.0,  # amount_idr / price
        price_ref=1000.0,
        reason="alasan uji",
        executable=True,
    )
    return proposal.new_proposal(
        action,
        rule="DCA bulanan",
        reserve_after=150000.0,
        window_s=proposal.DCA_WINDOW_S,
        now=1760000000.0,
    )


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


def test_payload_carries_pair_type_price_amount():
    # R-A (Critical): payload yang sampai ke transport.post_order harus
    # membawa instrumen — tanpa ini order terkirim TANPA pasangan.
    # Kosakata `type`/`price`/`amount` diambil dari catatan trade yang
    # dibaca `_normalize_v1`/`_normalize_v2` (indodax_api.py:148-201);
    # ejaan persis penempatan order BELUM diverifikasi (catatan di
    # `payload_for`).
    p = _proposal()
    pl = executor.payload_for(p, "abc")
    assert pl["pair"] == "btc_idr"
    assert pl["type"] == "buy"             # sisi ala catatan trade (v1: `type`)
    assert pl["price"] == 1000.0           # harga limit = price_ref
    assert pl["amount"] == 275.0           # jumlah koin (v1: `amount` = qty)


class _StatusTransport:
    def __init__(self, responses):
        self.responses = responses
        self.index = 0
        self.queried_ids = []

    def get_order_status(self, client_order_id: str) -> dict:
        self.queried_ids.append(client_order_id)
        if self.index < len(self.responses):
            resp = self.responses[self.index]
            self.index += 1
            return resp
        # If we run out of responses, return the last one? But the tests expect exactly three.
        # For safety, return the last response if we have one, else empty dict?
        # However, the tests only pass exactly three responses.
        return self.responses[-1] if self.responses else {}


def test_queries_with_same_id_and_documented_backoff():
    # Review Focus #2 â€” 404 sesaat setelah order sukses
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