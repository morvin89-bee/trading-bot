import socket
import urllib.request

import pytest

import portfolio_store as ps


class JaringanDibuka(RuntimeError):
    """Tes mencoba membuka jaringan — pelanggaran batas keras."""


def _tolak(nama):
    def _wrapper(*args, **kwargs):
        raise JaringanDibuka(
            f"Tes ini mencoba membuka jaringan lewat {nama}. "
            "Batas keras: tidak ada tes dan tidak ada modul yang boleh "
            "membuka koneksi. Suntikkan fungsinya (fetch/send/transport) "
            "supaya tesnya murni."
        )
    return _wrapper


@pytest.fixture(scope="session", autouse=True)
def _blokir_jaringan():
    """Tutup semua jalur keluar jaringan selama seluruh sesi tes.

    Batas ini sebelumnya hanya ditulis sebagai docstring di tiap file tes dan
    tidak pernah ditegakkan, sehingga satu tes Task 11 sempat diam-diam
    memanggil `urlopen` ke indodax.com. Guard ini membuat pelanggaran seperti
    itu gagal keras, bukan lulus diam-diam.
    """
    import requests.sessions

    asli = {
        "urllib.request.urlopen": urllib.request.urlopen,
        "requests.Session.request": requests.sessions.Session.request,
        "socket.create_connection": socket.create_connection,
    }
    urllib.request.urlopen = _tolak("urllib.request.urlopen")
    requests.sessions.Session.request = _tolak("requests.Session.request")
    socket.create_connection = _tolak("socket.create_connection")
    try:
        yield
    finally:
        urllib.request.urlopen = asli["urllib.request.urlopen"]
        requests.sessions.Session.request = asli["requests.Session.request"]
        socket.create_connection = asli["socket.create_connection"]


@pytest.fixture
def tmp_conn(tmp_path):
    conn = ps.connect(str(tmp_path / "t.db"))
    ps.init_db(conn)
    yield conn
    conn.close()
