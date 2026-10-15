"""Gabung aksi kembar per (kind, pair) sebelum pesan dibuat (spec §6.6).

Dua aturan yang menunjuk aksi yang sama — take-profit dan rebalance pada
pair yang sama — harus berakhir sebagai SATU aksi sebelum pesan Telegram
disusun. Bug "jual 25% + 25% = 50%" (Review Focus #4, pewaris dedupe
`24ad5ef`) mati di sini.

Kebijakan penggabungan:

- Dikelompokkan per `(kind, pair)` — SELL dan BUY pada pair yang sama
  adalah dua usulan berbeda dan TIDAK digabung.
- `qty`, `amount_idr`, `price_ref`, `executable` diambil dari aksi yang
  dipilih sebagai dasar (untuk SELL: aksi pertama — lihat syarat urutan
  di bawah; untuk BUY: nominal terbesar — nominal dua sumber tidak
  pernah dijumlahkan, karena itu melanggar atap per order §6.4).
- `reason` semua aksi kelompok digabung dengan `"; "` — pengguna harus
  melihat SEMUA pemicu (§6.2), bukan hanya yang pertama.
- Aksi tunggal dilewatkan apa adanya, tanpa disalin.

**Syarat urutan pemanggil — BELUM ditegakkan di mana pun.** Dasar SELL
adalah aksi **pertama** dalam grup input, dan angka itu hanya benar bila
aksi pertama berasal dari `take_profit_actions` (satu-satunya sumber yang
menghitung dari saldo posisi). `rules.Action` tidak membawa kolom
provenance, jadi modul ini **tidak bisa memeriksa** asal sebuah aksi —
dan tidak ada tes yang memaksa pemanggil memenuhi syarat ini. Pemanggil
wajib menempatkan output `take_profit_actions` lebih dulu daripada sumber
SELL lain dalam grup yang sama (Task 11, `collect_actions`). Kontrak
"aksi pertama yang menang" sendiri dikunci
`test_sell_base_is_first_input_action_not_last`. V1 saat ini menghasilkan
grup SELL beranggota tunggal per tik (take-profit maksimal satu per pair;
satu-satunya sumber SELL lain mendelegasikan ke hitungan yang sama), jadi
syarat urutan ini belum pernah dijalani dua aksi berbeda — latent, bukan
aman.

Modul ini murni daftar dan angka — tidak membuka jaringan, tidak punya
dan tidak pernah mengirim kemampuan order.
"""
import rules


def _base_of(kind, group):
    """Aksi dasar kelompok: pertama untuk SELL, nominal terbesar untuk BUY."""
    if kind != "BUY":
        return group[0]
    return max(group, key=lambda a: a.amount_idr)


def merge_actions(actions: list[rules.Action]) -> list[rules.Action]:
    """Satukan aksi kembar per `(kind, pair)`; urutan kemunculan dipertahankan.

    Inilah yang menjamin "satu aksi per (kind, pair) per tik" — jaminan
    yang dijanjikan docstring `proposal.new_proposal` dan diwarisi id
    proposal, kunci `shadow_log`, serta jangkar tombol Telegram.
    """
    groups = {}                      # (kind, pair) -> [aksi]; dict memesan
    for a in actions:                # sejak 3.7 — urutan kemunculan aman
        groups.setdefault((a.kind, a.pair), []).append(a)

    merged = []
    for (kind, pair), group in groups.items():
        if len(group) == 1:
            merged.append(group[0])
            continue
        base = _base_of(kind, group)
        merged.append(rules.Action(
            kind=kind,
            pair=pair,
            amount_idr=base.amount_idr,
            qty=base.qty,
            price_ref=base.price_ref,
            reason="; ".join(a.reason for a in group),
            executable=base.executable,
        ))
    return merged
