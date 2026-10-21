"""Laporan pertumbuhan bulanan: pisahkan setoran (kontribusi) dari pasar.

Dipakai untuk menjawab "berapa yang naik karena saya menabung vs karena
pasar bergerak". Laporan ini *forward-only*: hanya seakurat snapshot yang
sudah tersimpan.
"""
import time


def _month(ts):
    # Waktu lokal, konsisten dengan tanggal setoran (`portfolio_cli._parse_date`
    # memakai waktu lokal) dan dengan apa yang dilihat pengguna.
    return time.strftime("%Y-%m", time.localtime(ts))


def month_contributions(deposits, ts):
    """Jumlah setoran pada bulan yang sama dengan `ts` (waktu lokal)."""
    m = _month(ts)
    return sum(float(d.get("idr") or 0) for d in deposits
               if _month(d.get("ts") or 0) == m)


def _nilai_awal(snaps, deps, bulan):
    """Nilai portofolio SEBELUM setoran pertama bulan itu.

    Mengembalikan `(nilai, diketahui)`:

    - `diketahui` True: ada snapshot sebelum setoran pertama bulan itu, jadi
      nilai awalnya terukur dan `market_idr` = akhir − awal − setoran.
    - `diketahui` False: tidak ada snapshot sebelum itu. Nilai 0 dipakai dan
      laporan harus menyebut bahwa hasil pasar dihitung "sejak setoran
      pertama" (asumsi portofolio kosong sebelum uang masuk) — bukan
      mengarang angka.

    Kenapa bukan snapshot pertama bulan itu: snapshot pertama bulan bisa
    sudah MEMBAWA setoran bulan itu (setoran tanggal 1, snapshot pertama
    tanggal 14). Memakainya sebagai "awal" menghitung setoran dua kali —
    kasus nyata Oktober 2026: awal Rp 206.278 (sudah memuat modal awal
    Rp 206.000) dikurangi setoran Rp 502.606 -> hasil pasar −Rp 205.749
    (kerugian palsu; sebenarnya +Rp 529).

    `snaps` harus sudah terurut menaik (ts).
    """
    setoran = [d.get("ts") or 0 for d in deps if _month(d.get("ts") or 0) == bulan]
    if setoran:
        batas = min(setoran)
    else:
        # Tanpa setoran bulan ini: nilai awal = nilai bawaan dari bulan
        # lalu (snapshot terakhir sebelum snapshot pertama bulan ini).
        s_bulan = [s.get("ts") or 0 for s in snaps if _month(s.get("ts") or 0) == bulan]
        if not s_bulan:
            return 0.0, False
        batas = min(s_bulan)
    sebelumnya = [s for s in snaps if (s.get("ts") or 0) < batas]
    if not sebelumnya:
        return 0.0, False
    return float(sebelumnya[-1].get("total_idr") or 0), True


def monthly_report(snapshots, deposits):
    """Ringkas snapshot & setoran menjadi baris per bulan.

    Untuk tiap bulan: `contributions_idr` = jumlah setoran bulan itu;
    `market_idr` = nilai akhir bulan − nilai awal bulan − setoran
    (yaitu perubahan nilai yang bukan berasal dari uang baru);
    `end_value_idr` = nilai pada snapshot terakhir bulan itu;
    `opening_value_idr` = nilai awal yang dipakai; `opening_known` False
    berarti tak ada snapshot sebelum setoran pertama bulan itu, jadi
    `market_idr` dihitung "sejak setoran pertama" (lihat `_nilai_awal`).

    Mengembalikan daftar baris `{month, contributions_idr, market_idr,
    end_value_idr, opening_value_idr, opening_known}`, diurutkan naik per bulan.
    """
    snaps = sorted(snapshots, key=lambda s: s.get("ts") or 0)
    deps = sorted(deposits, key=lambda d: d.get("ts") or 0)

    months = {}
    for s in snaps:
        m = _month(s.get("ts") or 0)
        months[m] = s

    contrib = {}
    for d in deps:
        m = _month(d.get("ts") or 0)
        contrib[m] = contrib.get(m, 0.0) + float(d.get("idr") or 0)

    rows = []
    for m in sorted(months):
        akh = float(months[m].get("total_idr") or 0)
        c = contrib.get(m, 0.0)
        aw, diketahui = _nilai_awal(snaps, deps, m)
        rows.append({
            "month": m,
            "contributions_idr": c,
            "market_idr": akh - aw - c,
            "end_value_idr": akh,
            "opening_value_idr": aw,
            "opening_known": diketahui,
        })
    return rows
