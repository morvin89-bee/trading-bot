"""Laporan pertumbuhan bulanan: pisahkan setoran (kontribusi) dari pasar.

Dipakai untuk menjawab "berapa yang naik karena saya menabung vs karena
pasar bergerak". Laporan ini *forward-only*: hanya seakurat snapshot yang
sudah tersimpan.
"""
import time


def _month(ts):
    return time.strftime("%Y-%m", time.gmtime(ts))


def monthly_report(snapshots, deposits):
    """Ringkas snapshot & setoran menjadi baris per bulan.

    Untuk tiap bulan: `contributions_idr` = jumlah setoran bulan itu;
    `market_idr` = nilai akhir bulan − nilai awal bulan − setoran
    (yaitu perubahan nilai yang bukan berasal dari uang baru);
    `end_value_idr` = nilai pada snapshot terakhir bulan itu.

    Mengembalikan daftar baris `{month, contributions_idr, market_idr,
    end_value_idr}`, diurutkan naik per bulan.
    """
    snaps = sorted(snapshots, key=lambda s: s.get("ts") or 0)
    deps = sorted(deposits, key=lambda d: d.get("ts") or 0)

    months = {}
    for s in snaps:
        m = _month(s.get("ts") or 0)
        if m not in months:
            months[m] = {"first": s, "last": s}
        else:
            months[m]["last"] = s

    contrib = {}
    for d in deps:
        m = _month(d.get("ts") or 0)
        contrib[m] = contrib.get(m, 0.0) + float(d.get("idr") or 0)

    rows = []
    for m in sorted(months):
        aw = float(months[m]["first"].get("total_idr") or 0)
        akh = float(months[m]["last"].get("total_idr") or 0)
        c = contrib.get(m, 0.0)
        rows.append({
            "month": m,
            "contributions_idr": c,
            "market_idr": akh - aw - c,
            "end_value_idr": akh,
        })
    return rows
