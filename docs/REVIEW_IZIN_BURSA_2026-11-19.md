# Review: preflight mencocokkan izin kunci dengan bursa — 2026-11-19

## Temuan

`preflight` hanya **menggemakan** deklarasi `INDODAX_CAN_TRADE` /
`INDODAX_CAN_WITHDRAW` dari `.env`; klaim "Indodax tidak mengekspos izin key
lewat API" ternyata salah. `indodax_api.account_info()` (jalur v2) mengembalikan
`canTrade`, `canWithdraw`, dan `canWithdrawFiat` per kunci.

Akibatnya langkah 4 checklist ("preflight GO dengan kunci baca-saja") tidak
membuktikan apa pun. Bukti 2026-11-19: kunci di `.env` melaporkan
`canTrade=True`, deklarasinya kosong, dan `python -m preflight --target
bayangan` tetap **GO**.

## Perbaikan

`preflight._izin_bursa()` membaca izin sebenarnya; `_periksa_izin_bursa()`
membandingkannya dengan deklarasi. Beda = GAGAL (exit 1), bursa tidak menjawab
atau tidak melaporkan (jalur v1 `getInfo`) = TAK TERBACA (exit 2). Pemeriksaan
berjalan untuk target `live` maupun `bayangan`.

## Bukti

Sebelum: `[OK] izin kunci: canTrade=False, canWithdraw=False (sakelar mati)` →
GO.

Sesudah: `[GAGAL] izin kunci (bursa): canTrade: bursa=True, .env=False —
samakan .env dengan izin kunci sebenarnya` → NO-GO (exit 1).

## Tes

`tests/test_preflight.py`: `test_izin_bursa_beda_dari_deklarasi_no_go`,
`test_izin_bursa_tidak_terbaca_exit_2`, plus assert "cocok" di
`test_semua_lolos_berarti_go`. Tiga pemanggilan di `tests/test_denyut_terakhir.py`
kini menyuntik `baca_izin_bursa` (sebelumnya akan membuka jaringan).

## Risiko / batas

- `preflight` sekarang **butuh kunci API yang hidup**. Kunci mati atau jaringan
  gagal = exit 2, dan exit 2 tetap berarti jangan menyalakan sakelar.
- Jalur v1 (`getInfo`) tidak melaporkan izin, jadi pengguna v1 selalu mendapat
  TAK TERBACA. Disengaja: ragu = jangan GO; akun ini di jalur v2.
- `canTrade` adalah izin **per kunci** menurut bursa; bila Indodax mengubah
  arti field itu, pemeriksaan ini ikut salah — tes hanya mengunci logika
  perbandingan, bukan arti field di bursa.
