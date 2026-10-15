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