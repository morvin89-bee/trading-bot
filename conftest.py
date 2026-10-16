"""Guard jaringan untuk seluruh sesi tes — berlaku di mana pun tes dijalankan.

Batas keras rencana: **tidak ada tes dan tidak ada modul yang boleh membuka
koneksi**. Selama ini batas itu hanya ditulis sebagai docstring di tiap file
tes dan tidak pernah ditegakkan, sehingga satu tes Task 11 sempat diam-diam
memanggil `urlopen` ke indodax.com dan tetap lulus. Guard ini membuat
pelanggaran gagal keras.

Ada di root (bukan di `tests/`) supaya file tes level root yang tidak ikut
`testpaths` pun tetap terjaga bila dijalankan langsung.
"""
import socket
import urllib.request

import pytest


class JaringanDibuka(BaseException):
    """Tes mencoba membuka jaringan — pelanggaran batas keras.

    Sengaja mewarisi `BaseException`, bukan `Exception`: beberapa jalur produksi
    (mis. `alerts.send_telegram`) menangkap `Exception` secara luas, dan bila
    guard ini `Exception` biasa penangkapan itu akan menelannya sehingga tes
    lulus diam-diam untuk alasan yang salah — guard gagal di tengah tepat
    karena tugasnya berteriak.
    """


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
    """Tutup semua jalur keluar jaringan selama seluruh sesi tes."""
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
