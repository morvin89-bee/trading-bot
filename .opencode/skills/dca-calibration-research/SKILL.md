---
name: dca-calibration-research
description: Riset kalibrasi DCA memakai trading-skills MCP (jika terpasang) pada BTC-USD, ETH-USD, SOL-USD untuk volatilitas, drawdown, dan korelasi. Hasil hanya boleh ditulis ke docs/research/ dan tidak boleh diimpor kode bot.
---

# Riset Kalibrasi DCA

Tujuan: memberi bahan keputusan untuk parameter DCA, bukan mengubah kode bot
secara otomatis.

## Prasyarat

- Gunakan server MCP `trading-skills` **hanya jika sudah terpasang**.
  Jangan memasang, meng-clone, atau menjalankan `uv sync` tanpa persetujuan.
- Jika MCP tidak tersedia, tulis di laporan bahwa riset tidak dijalankan dan
  jangan mengarang angka.
- Semua panggilan bersifat read-only. Jangan memakai tool IB yang bisa
  mengirim order (`ib_stop_loss`, `ib_trailing_stop`).

## Aset dan metrik

Aset: `BTC-USD`, `ETH-USD`, `SOL-USD` (proxy USD).

Metrik yang diminta:

- **Volatilitas**: simpangan baku return harian, ATR, rentang persentil.
- **Drawdown**: maksimum drawdown dan durasi pemulihan.
- **Korelasi**: matriks korelasi return harian antar ketiga aset.

## Prosedur

1. Ambil riwayat harga harian yang cukup panjang (minimal 1 tahun; sebutkan
   rentang tanggalnya).
2. Hitung metrik dengan skrip/alat yang tersedia. Jangan menulis angka yang
   tidak bisa ditelusuri ke data.
3. Simpan hanya ke `docs/research/` dengan nama bertanggal, mis.
   `docs/research/dca-calibration-2026-11-05.md`.
4. Cantumkan sumber data, tanggal pengambilan, dan keterbatasan.

## Peringatan wajib

- **Proxy USD, bukan harga Indodax IDR.** Perbedaan likuiditas, spread, dan
  kurs membuat angka ini tidak bisa dipakai langsung sebagai parameter order.
- Data Yahoo Finance bisa tertunda hingga 15 menit dan bukan data tick.
- Hasil riset tidak boleh diimpor oleh kode bot. Tidak ada `import` dari
  `docs/research/` ke modul produksi.
- Jangan menyimpulkan sinyal beli/jual; ini bahan kalibrasi, bukan nasihat
  keuangan.
