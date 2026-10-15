"""Detektor selisih portfolio.db vs bursa — pemicu halt (§6.3 & §6.6).

Setiap putaran, saldo & jumlah order terbuka yang dipercaya `portfolio.db`
dicocokkan dengan jawaban bursa. **Selisih sekecil apa pun** berarti buku
bukan kenyataan: trading dimatikan dan pengguna diberi tahu — sistem tidak
melanjutkan kerja di atas gambaran yang tidak bisa dipercaya (spec §6.3 baris
"selisih apa pun ... trading mati", §6.6 "saldo & order terbuka dicocokkan
dengan bursa").

`mismatches` murni membandingkan dua dict yang disuntikkan pemanggil —
pengambilan data ada di `engine`. Modul ini tidak membuka jaringan, tidak
punya dan tidak pernah mengirim kemampuan order.

Toleransi (satu sisi angka, sisi lain angka):

- `IDR`: `TOLERANSI_IDR` = 1 rupiah;
- `open_orders`: nol — satu order tertunda pun mengubah saldo tersedia;
- koin lain: `TOLERANSI_KOIN` = 1e-8.

Kunci yang hanya ada di satu sisi (mis. bursa menyaring saldo nol lewat
`parse_balances`) tetap selisih; sisi yang hilang ditulis `tidak ada`
supaya pesan tidak pernah mengarang angka. Nilai yang ada di kedua sisi
wajib angka — bukan angka berarti `TypeError` (pemanggil menangkap; kegagalan
= berhenti dan diam).
"""
TOLERANSI_KOIN = 1e-8
TOLERANSI_IDR = 1
TOLERANSI_OPEN_ORDERS = 0
TIDAK_ADA = "tidak ada"


def _toleransi(aset: str) -> float:
    """Toleransi satu aset: IDR 1 rupiah, `open_orders` tepat, koin 1e-8."""
    if aset == "IDR":
        return float(TOLERANSI_IDR)
    if aset == "open_orders":
        return float(TOLERANSI_OPEN_ORDERS)
    return TOLERANSI_KOIN


def mismatches(db_state: dict, exchange_state: dict) -> list[str]:
    """Satu baris Indonesia per selisih; daftar kosong = buku cocok.

    Kedua dict berbentuk `{aset: jumlah, ..., "open_orders": n}` berisi angka
    yang sudah disuntikkan pemanggil (bursa: `portfolio.parse_balances` +
    jumlah `openOrders`; db: catatan `portfolio.db` yang sebanding).

    Setiap baris berbentuk persis
    `"Selisih {aset}: db={..} bursa={..} -> trading mati, cek manual"`
    dengan angka yang benar-benar dibandingkan — pesan yang salah angka lebih
    buruk daripada tidak ada pesan. Urutan mengikuti urutan `db_state`, lalu
    kunci yang hanya ada di `exchange_state`.
    """
    hasil = []
    for aset in list(db_state) + [k for k in exchange_state
                                  if k not in db_state]:
        di_db = aset in db_state
        di_bursa = aset in exchange_state
        if di_db and di_bursa:
            db_nilai = db_state[aset]
            bursa_nilai = exchange_state[aset]
            if abs(db_nilai - bursa_nilai) <= _toleransi(aset):
                continue
        elif di_db:
            db_nilai = db_state[aset]
            bursa_nilai = TIDAK_ADA
        else:
            db_nilai = TIDAK_ADA
            bursa_nilai = exchange_state[aset]
        hasil.append(f"Selisih {aset}: db={db_nilai} bursa={bursa_nilai} "
                     f"-> trading mati, cek manual")
    return hasil
