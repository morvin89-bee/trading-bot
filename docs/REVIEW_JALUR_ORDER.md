# Tinjauan jalur order — semua perubahan sejak `main`

Rentang: `git log main..HEAD` per 2026-11-15 (kepala: `6eb0f8d`).
Tujuan: satu pembaca bisa memeriksa **seluruh** perubahan yang menyentuh jalur
order dalam ±1 jam, tanpa membaca diff.

Cara baca: setiap baris = satu fungsi. Kolom "perubahan" menyebut commit
pendeknya. Kolom "pengaman utuh" menyebut pengaman yang **tetap berlaku**
sesudah perubahan itu — bukan yang ditambahkan.

Berkas di luar daftar ini juga berubah (`preflight.py`, `pair_meta.py`,
`portfolio.py`, `reconcile.py`, `dashboard/`, `docs/`), tetapi tidak ada yang
menambah jalur pengiriman order.

---

## 0. Enam pengaman yang tidak boleh dilewati

Satu-satunya tempat order meninggalkan bot: `executor.submit_once`
(`executor.py:45`), dipanggil dari **satu** situs, `engine.on_answer`
(`engine.py:994`). Semua jalur di bawah berakhir di situ atau tidak berakhir
di mana pun.

| # | Pengaman | Di mana ditegakkan | Tes pengunci |
|---|----------|--------------------|--------------|
| 1 | Persetujuan Telegram | `engine.tick` mengirim tombol; `on_answer` hanya lanjut bila `answer == BUTTON_SETUJU`; tidak ada "setujui semua" | `test_on_answer_jawaban_selain_setuju_diabaikan`, `test_confirmation_has_three_buttons_and_no_approve_all` |
| 2 | Mode bayangan | `on_answer` langkah 3: mencatat `setuju` lalu berhenti, transport tidak disentuh | `test_on_answer_bayangan_mencatat_setuju_tanpa_transport`, `test_shadow_writes_nothing_to_exchange` |
| 3 | `safeguards.gates` | Dinilai di `tick` (sebelum konfirmasi) **dan** diulang di `on_answer` (sebelum kirim) | `test_on_answer_pagu_per_aset_diulang_sebelum_kirim`, `test_rejects_above_atap` |
| 4 | Gerbang spread | Dinilai di `tick`, **dan** diulang pada harga saat tombol ditekan | `test_spread_rechecked_at_execution_time`, `test_tick_penolakan_spread_membulatkan_bps_ke_atas` |
| 5 | `permissions_ok` | `tick_cycle` sebelum keputusan apa pun, **dan** `on_answer` tepat sebelum kirim | `test_halt_when_can_withdraw_flips_true`, `test_izin_tarik_dana_menghentikan_tick_sebelum_usulan` |
| 6 | `client_order_id` idempoten | Id hash disimpan di `proposals`; jawaban kedua dikunci `_sudah_dijawab` (proposals **dan** `order_status`) | `test_on_answer_jawaban_kedua_setelah_balasan_hilang_tidak_kirim_ulang`, `test_never_resends_after_lost_reply` |

Pengaman tambahan yang juga tidak pernah dilewati: metadata presisi qty
(`pair_meta.get`), gerbang geser harga 0,5%, gerbang jual memakai saldo
**bebas**, dan rekonsiliasi buku sebelum usulan.

---

## 1. `rules.py` — logika murni

| Fungsi | Perubahan | Alasan | Tes pengunci | Pengaman utuh |
|--------|-----------|--------|--------------|---------------|
| `dca_split` | Tolak bobot target yang tidak berjumlah 1,0 (`9817f08`) | Bobot yang bergeser diam-diam membuat pembagian setoran salah tanpa satu pun galat | `test_target_weights_must_sum_to_one` | `engine` memperlakukan `ValueError` sebagai halt; tidak ada order |
| `dca_split`, `dca_actions` | **Diverifikasi**, tidak diubah: hanya kunci `DEFAULT_TARGET` yang dijumlah dan diiterasi | Bukti untuk keputusan harga non-target (2026-11-15) | `test_split_basic_weights`, `test_collect_actions_menghasilkan_dca_dengan_provenance` | Nilai aset di luar target tidak pernah masuk keputusan |
| `bulatkan_qty_bawah`, `qty_teks_tetap` (baru) | Pembulatan qty **ke bawah** ke presisi bursa; teks titik tetap (`b85cc6f`) | Qty ke atas = uang keluar melebihi yang disetujui; notasi ilmiah ditolak bursa | `test_qty_dibulatkan_ke_bawah_tidak_pernah_ke_atas`, `test_tick_membulatkan_qty_ke_bawah_dan_menghitung_ulang_nominal`, `test_payload_qty_kecil_titik_tetap_bukan_notasi_ilmiah` | `engine.tick` membulatkan **sebelum** gerbang; nominal dihitung ulang dari qty terbulat |
| `take_profit_actions` | Ingat ambang yang sudah dieksekusi (`qty_target`, `qty_terjual`), satu tranche per pair per hari, tidak di-reset saat `avg_cost` berubah (`cb904d5`, `04bb366`) | Dulu ambang yang sama diusulkan berulang setiap tick atau justru hilang setelah DCA | `test_ambang_yang_sudah_dieksekusi_tidak_diusulkan_lagi`, `test_ambang_tidak_direset_saat_avg_cost_berubah_karena_dca`, `test_sisa_tranche_diusulkan_besok_bukan_hari_ini` | Memori hanya ditulis setelah order **terisi**/disetujui bayangan/`unknown` (`engine._catat_take_profit`) |
| `take_profit_actions` | Potong SELL ke `max_order`, sisa dilaporkan `sisa_qty` (`cb904d5`) | Order di atas atap pasti ditolak gerbang; sisa tidak boleh hilang diam-diam | `test_sell_dipotong_ke_atap_dan_sisa_dilaporkan`, `test_take_profit_dipotong_ke_atap_dan_sisa_tampil` | Atap yang sama tetap ditegakkan `safeguards.gates` |
| `take_profit_actions` | Hanya pair `DEFAULT_TARGET` (`c9bf9ae`, keputusan pemilik 2026-11-15) | Aset di luar target tidak pernah dibeli strategi ini | `test_bch_di_luar_target_tidak_pernah_diusulkan` | `engine` tidak pernah melihat aksi untuk aset non-target |
| `dca_actions` | Kembalikan daftar kosong bila `idr_free < monthly` (`fc4e032`) | Usulan yang pasti ditolak gerbang hanya menghasilkan tiga pesan penolakan | `test_dca_actions_tanpa_saldo_cukup_tidak_mengusulkan` | `engine.tick` mengirim satu pesan jujur; bulan **tetap terbuka** |
| `reserve_buy_actions` | **Diverifikasi**: `target.get(pair, 0.0)` → tranche 0 untuk aset non-target | Bukti keputusan harga non-target | `test_reserve_buy_lolos_gerbang_karena_nama_aturan_reserve_buy` | Gerbang saldo reserve buy tetap di `safeguards` |
| `rebalance_actions` | Tidak diubah; **masih** menjumlah nilai aset non-target | Hanya untuk saran dashboard, bukan jalur order | `test_v1_never_calls_rebalance` | `engine.collect_actions` tidak pernah memanggilnya |

Catatan: `rules.py` tidak mengimpor `safeguards` (atap dikirim pemanggil).

---

## 2. `safeguards.py` — gerbang per aksi

| Fungsi | Perubahan | Alasan | Tes pengunci | Pengaman utuh |
|--------|-----------|--------|--------------|---------------|
| `MAX_ORDER_IDR` | Konstanta eksplisit `275_000` (55% setoran), bukan turunan `MONTHLY_IDR` (`f6ba6e9`) | Menaikkan setoran tidak boleh otomatis melonggarkan atap | `test_atap_eksplisit_tidak_ikut_naik_saat_setoran_naik`, `test_max_order_adalah_porsi_tetap_setoran_bulanan` | Atap tetap diperiksa pertama di `gates` |
| `gates` → `gates` + `gates_berkode` (baru) | Versi berkode `(kode, teks)`; `gates` jadi pembungkus teks (`f6ba6e9`) | Dedupe pesan per hari butuh kunci stabil, bukan teks yang memuat angka | `test_penolakan_gerbang_sama_hanya_sekali_per_hari`, `test_dua_kode_alasan_berbeda_tetap_dikirim_pada_hari_sama` | Urutan dan isi pemeriksaan tidak berubah |
| `gates` (gerbang saldo reserve buy) | `(free_idr - reserve_idr) + 0,5 × reserve_idr` (`9817f08`) | `free_idr` sudah termasuk cadangan; rumus lama menghitung cadangan dua kali dan meloloskan order yang menguras >separuh | `test_reserve_buy_does_not_double_count_free_idr`, `test_reserve_buy_cannot_exceed_half_of_reserve_even_if_rule_forgets`, `test_on_answer_reserve_buy_lolos_gerbang_saldo_input_sungguhan` | Batas `max_fraction` tetap ditegakkan di gerbang, bukan hanya di `rules` |
| `permissions_ok` | Tiga keadaan: izin tak terbaca = **halt**; `canWithdraw` aktif = halt, juga di mode live (`9817f08`) | `None`/galat dulu bisa terbaca sebagai "boleh" | `test_permissions_unknown_means_halt`, `test_halt_when_can_withdraw_true_even_in_live_mode`, `test_halt_when_can_trade_disagrees_with_mode` | Dipanggil ulang tepat sebelum kirim |
| `gates` (urutan) | Tidak berubah: atap → pagu per aset/hari → jeda 30 mnt → anti-loop 24 jam → saldo; **SELL tidak pernah** kena gerbang IDR | — | `test_sell_is_never_gated_by_idr_balance`, `test_rejects_within_cooldown_of_last_fill` | Semua batas tetap konstanta bernama |

---

## 3. `proposal.py` — bentuk usulan & pesan konfirmasi

| Fungsi | Perubahan | Alasan | Tes pengunci | Pengaman utuh |
|--------|-----------|--------|--------------|---------------|
| `client_order_id_for` (baru) | Awalan terbaca (≤20) + hash SHA-256 id **penuh**, ≤36 karakter (`0c9f3a2`) | Pemotongan 36 karakter pertama membuat dua id berbeda menjadi satu id kirim → bursa menganggap order kedua duplikat | `test_client_order_id_hash_memisahkan_id_yang_dulu_terpotong_sama`, `test_id_sub_second_distinct_and_client_order_id_safe` | Id disimpan di `proposals.client_order_id` dan dipakai ulang saat kirim/cek status |
| `Proposal` (field baru) | `level`, `basis`, `qty_target`, `sisa_qty`, `revisi`, `client_order_id` (`cb904d5`, `04bb366`, `456c8d0`, `0c9f3a2`) | Memori take-profit, penolakan tombol dari pesan pra-re-quote, idempoten | `test_new_proposal_membawa_client_order_id_hash`, `test_new_proposal_carries_action_and_metadata` | Nilai bawaan aman: `None`/0; usulan lama tetap memakai `id` |
| `DCA_MAX_DRIFT_PCT` | Batas re-quote DCA 3% (`456c8d0`); 0,5%–3% dihitung ulang, >3% hangus | Jendela DCA 24 jam: 0,5% terlalu sempit untuk harga yang bergerak wajar, tetapi persetujuan angka lama tidak boleh dipakai | `test_requote_dca_drift_15_persen_kirim_konfirmasi_baru`, `test_requote_dca_gagal_kirim_konfirmasi_tidak_mengubah_usulan`, `test_take_profit_drift_15_persen_tetap_hangus` | Pengecualian **hanya** untuk rule DCA; take-profit tetap 0,5% |
| `confirmation_text` | Baris "sisa … tranche berikutnya" (`cb904d5`); delapan field lama tetap | Sisa yang dipotong atap tidak boleh tak terlihat | `test_confirmation_text_carries_all_eight_fields` | Angka di pesan = angka yang dikirim (qty terbulat) |
| `price_still_valid`, `is_expired` | Tidak berubah; harga `None` = tidak valid (bukan nol) | `27bdd70`/`456c8d0` mengandalkan fungsi ini | `test_missing_price_is_invalid_not_zero`, `test_on_answer_harga_tidak_terbaca_konfirmasi_hangus` | Konfirmasi hangus, tidak ada order |

---

## 4. `executor.py` — satu order, satu kali

| Fungsi | Perubahan | Alasan | Tes pengunci | Pengaman utuh |
|--------|-----------|--------|--------------|---------------|
| `make_client_order_id` | Memakai `proposal.client_order_id_for` (hash) (`0c9f3a2`) | Id kirim harus unik dan ≤36 karakter | `test_client_order_id_is_stable_charset_and_length`, `test_client_order_id_tidak_memotong_dua_prefix_menjadi_sama` | Format `[0-9A-Za-z_-]`, ≤36 |
| `client_id` (baru) | `p.client_order_id` bila ada, selain itu `p.id` (`0c9f3a2`) | Usulan lama yang mungkin sudah tercatat di bursa tidak boleh di-hash ulang | `test_usulan_lama_tanpa_client_order_id_memakai_id_lama`, `test_on_answer_memakai_client_order_id_tersimpan` | Kirim dan cek status memakai id yang sama |
| `payload_for` | `amount` dikirim sebagai string titik tetap (`b85cc6f`) | `urlencode` float mentah bisa menghasilkan `5e-05` | `test_payload_carries_pair_type_price_amount` | `recv_window` dan `client_order_id` tetap di payload |
| `submit_once`, `resolve_status` | Tidak berubah: satu kirim; balasan hilang → tanya status id yang sama, maks 3×, backoff `(2,5,10)`; `unknown` tidak pernah dilaporkan `cancelled` | — | `test_never_resends_after_lost_reply`, `test_gives_up_unknown_after_three_asks_without_new_id`, `test_unknown_is_never_reported_as_cancelled` | Tidak ada jalur kirim kedua |

---

## 5. `shadow_log.py` — catatan keputusan (bukan order)

| Fungsi | Perubahan | Alasan | Tes pengunci | Pengaman utuh |
|--------|-----------|--------|--------------|---------------|
| `record`, `pending` | Menyimpan `revisi`; `client_order_id` ikut tersimpan (`bf378f4`, `0c9f3a2`) | Tombol dari pesan lama harus bisa ditolak; id kirim harus pulih setelah restart | `test_record_dan_pending_menyimpan_revisi` | Jumlah baris tetap satu per usulan (`_catat_hasil` memperbarui, bukan menambah) |
| `take_profit_state`, `catat_take_profit`, `hapus_take_profit`, `hapus_take_profit_mode` | State per `(pair, level)` dan per mode; migrasi tabel lama; reset hanya saat posisi tutup | Penjualan bayangan tidak boleh menghalangi penjualan nyata; ambang tidak boleh hilang saat DCA mengubah `avg_cost` | `test_state_take_profit_terpisah_per_mode`, `test_migrasi_tabel_lama_tetap_mengingat_ambang`, `test_hapus_mode_shadow_untuk_aktifkan_live` | Ditulis hanya setelah order terisi/disetujui bayangan/`unknown` |
| `perbarui_harga` (baru) | Memperbarui harga/qty/nominal usulan yang menunggu (`456c8d0`) | Re-quote DCA mengubah angka yang disetujui | `test_requote_dca_drift_15_persen_kirim_konfirmasi_baru` | `revisi` naik; tombol pesan lama ditolak |
| `void_pending` (baru) | Menutup semua usulan DCA yang menunggu (`ef6592c`) | "Batal bulan ini" harus benar-benar membatalkan | `test_batal_satu_usulan_dca_membatalkan_semua_yang_menunggu` | Id tak dikenal tidak menyentuh jendela (`F46`) |
| `rejected_count` (baru) | Menghitung penolakan gerbang/spread; bukan `batal`/`proposed` (`f6ba6e9`) | Dasar reset hari bayangan: penolakan berarti sistem belum bersih | `test_rejected_count_is_the_shadow_reset_signal`, `test_rejected_count_tidak_menghitung_batal_atau_proposed` | Hanya membaca; tidak pernah menyalakan sakelar |

---

## 6. `engine.py` — penilaian aksi, konfirmasi, eksekusi

### `collect_actions`

| Perubahan | Alasan | Tes pengunci | Pengaman utuh |
|-----------|--------|--------------|---------------|
| Nama aturan diambil dari asal aksi (`_catat_asal`), bukan dari teks alasan (`f6ba6e9`) | Nama yang salah membuat reserve buy jatuh ke gerbang saldo DCA | `test_rule_name_setiap_aksi_collect_actions_terdaftar`, `test_rule_name_tidak_membaca_nama_dari_kata_alasan`, `test_rule_name_menolak_aksi_tanpa_asal` | Nama tak dikenal = `ValueError` = halt |
| Dua aturan untuk pasangan sama ditolak (`f6ba6e9`) | Dua usulan untuk koin yang sama dalam satu tick | `test_collect_actions_menolak_dua_aturan_untuk_pasangan_sama` | Urutan tetap: take-profit → DCA → reserve buy |

### `tick`

| Perubahan | Alasan | Tes pengunci | Pengaman utuh |
|-----------|--------|--------------|---------------|
| Semua aksi dinilai dulu; konfirmasi dikirim setelah seluruh daftar lolos (`79522fa`) | Halt di tengah dulu meninggalkan tombol untuk usulan yang tidak pernah dikembalikan | `test_tick_halt_spread_tidak_meninggalkan_konfirmasi_yatim` | Spread/gates/metadata tetap dinilai per aksi |
| Saldo berjalan antar aksi (`1fe72bb`) | Dua BUY yang masing-masing muat bisa bersama melebihi saldo | `test_saldo_berjalan_antar_aksi_dalam_satu_tick` | SELL tidak mengurangi saldo |
| Metadata pair + pembulatan qty + minimum **sebelum** gerbang (`b85cc6f`, `86f41ba`) | Presisi ditebak = order ditolak bursa atau qty lebih besar dari persetujuan | `test_dca_metadata_hilang_tidak_menutup_bulan_dan_tidak_pakai_jatah`, `test_dca_minimum_satu_pair_tidak_membatalkan_pair_lain` | Metadata hilang = halt (fail closed), bukan tebakan |
| Peringatan tranche < 2× minimum bursa, order **tetap** diusulkan (`6157c7c`) | Ukuran setoran milik pengguna, bukan keputusan bot | `test_tranche_dca_di_bawah_dua_kali_minimum_hanya_peringatan` | Tidak ada perubahan perilaku order |
| Harga hilang untuk satu aksi = seluruh tick berhenti, `dca_retry=True` | Tidak ada keputusan di atas harga yang tidak ada | `test_on_answer_harga_hilang_berhenti_bukan_menolak` | Bulan DCA tidak ditutup karena percobaan belum tuntas |
| Dedupe pesan penolakan per hari memakai kode stabil (`47d92b2`, `f6ba6e9`) | Teks memuat angka spread/nominal, jadi dedupe berbasis teks bocor tiap tick | `test_penolakan_gerbang_sama_hanya_sekali_per_hari`, `test_penolakan_spread_sama_hanya_sekali_per_hari` | Setiap alasan tetap dikirim minimal sekali |
| DCA hanya diulang untuk pair yang gagal terkirim, maks 3 percobaan, jeda 30 menit, berhenti setelah 21.00 (`9d01242`, `798b4a3`) | Retry seluruh paket mengirim ulang pair yang sudah terkirim | `test_kirim_sebagian_hanya_mengulang_pair_yang_gagal`, `test_retry_dca_berjarak_minimal_30_menit`, `test_retry_dca_tidak_diusulkan_setelah_jam_21` | Jendela 24 jam tidak diperpanjang retry |
| `dca_saldo_kurang`: satu pesan, bulan terbuka, nominal **tidak** dikecilkan (`fc4e032`, `f6ba6e9`) | Saldo di bawah setoran bukan alasan menutup bulan | `test_tick_saldo_kurang_mengirim_satu_pesan_tanpa_usulan_dca` | Tidak ada usulan yang lolos tanpa gerbang |

### `on_answer` (urutan langkah dipertahankan)

| Langkah / perubahan | Alasan | Tes pengunci | Pengaman utuh |
|---------------------|--------|--------------|---------------|
| 0. `_sudah_dijawab` membaca `proposals` **dan** `order_status` (`f6d3f58`, R-B) | Pada balasan hilang tidak ada baris `order_status`, guard lama meleset dan order kedua terkirim dengan id sama | `test_on_answer_jawaban_kedua_diabaikan_tanpa_order_baru`, `test_on_answer_jawaban_kedua_setelah_balasan_hilang_tidak_kirim_ulang` | Berhenti **sebelum** menyentuh transport |
| 1. Bukan `setuju` → `ignored` | — | `test_on_answer_jawaban_selain_setuju_diabaikan` | Tunda/batal tidak pernah mengirim |
| 2. Kedaluwarsa → `ignored` | Angka lama tidak dipakai ulang | `test_expired_proposal_writes_no_order_and_nothing_to_db` | Nol tulisan DB, nol order |
| 2b. Geser harga >0,5% → hangus; DCA 0,5–3% → re-quote dengan konfirmasi baru + `revisi+1` (`456c8d0`, `bf378f4`) | Pengguna tidak boleh menyetujui angka yang sudah berubah, tetapi jendela 24 jam tidak boleh mati karena gerak wajar | `test_on_answer_harga_bergeser_konfirmasi_hangus`, `test_requote_dca_drift_15_persen_kirim_konfirmasi_baru`, `test_setuju_pesan_lama_setelah_requote_ditolak` | Harga tak terbaca = hangus; id/jendela tidak berubah |
| 2b. Gerbang jual memakai `qty_free`, tolak qty > kepemilikan (`23fa2e3`, `f87c72e`) | Koin terkunci tidak bisa dijual; konteks usulan bisa basi | `test_jual_ditolak_bila_koin_terkunci`, `test_jual_melebihi_kepemilikan_ditolak`, `test_selisih_desimal_kecil_tidak_menolak_jual` | Valuasi portofolio tetap memakai total `qty` |
| 3. Mode bayangan mencatat `setuju` lalu berhenti | Tidak ada order di mode bayangan | `test_on_answer_bayangan_mencatat_setuju_tanpa_transport` | Transport tidak pernah disentuh |
| 4. Spread dinilai ulang pada harga sekarang (`f6d3f58`) | Lolos saat usul tidak menjamin lolos saat eksekusi | `test_spread_rechecked_at_execution_time`, `test_on_answer_penolakan_spread_membulatkan_bps_ke_atas` | Harga `None` = berhenti |
| 5. `safeguards.gates` diulang dengan konteks sekarang | Saldo/pagu bisa berubah sejak usulan | `test_on_answer_pagu_per_aset_diulang_sebelum_kirim` | Penolakan = `gate_blocked`, tidak ada order |
| 6. `permissions_ok` dibaca ulang | Izin bursa bisa dicabut di antara usulan dan tombol | `test_halt_when_can_withdraw_flips_true` | Halt = tidak ada order |
| 6b. `setuju` dicatat **tepat sebelum** transport disentuh (`f6d3f58`) | Penolakan spread/gerbang dulu mengunci jawaban ulang | `test_on_answer_penolakan_sebelum_kirim_tidak_mengunci_jawaban_ulang` | Sejak titik ini jawaban kedua terkunci, termasuk saat `unknown` |
| 7–8. `submit_once` sekali; `unknown` → tanya status id sama; DB ditulis hanya setelah status final (`f6d3f58`, `0c9f3a2`) | Balasan hilang tidak boleh berarti kirim ulang | `test_on_answer_balasan_hilang_berhenti_tanpa_kirim_ulang`, `test_writes_db_only_after_exchange_answers`, `test_on_answer_submit_dan_tanya_status_memakai_id_sama` | Nol tulisan DB saat `unknown`; tidak pernah kirim ulang |
| `_catat_hasil` memperbarui baris yang ada | Produksi menulis id sama tiga kali (`proposed`→`setuju`→`executed`) | `test_on_answer_produksi_setelah_tick_tanpa_bentrok_primary_key` | Satu baris per usulan |
| `rekonsiliasi_status`, `_catat_take_profit`, `_hapus_take_profit` (`b964a50`) | `unknown` sudah dihitung selesai (ragu = jangan jual dobel); status final `rejected`/`cancelled` mengembalikan tranche | `test_unknown_setelah_submit_mencatat_take_profit_live`, `test_rekonsiliasi_filled_mempertahankan_catatan_take_profit`, `test_status_final_rejected_setelah_kirim_menghapus_memori_tranche` | Tidak menyentuh jalur order sama sekali |

### `Context`

Field baru `dca_enabled`, `dca_lewati`, `take_profit_sudah`, `dca_retry`,
`dca_saldo_kurang`. Semua **disuntikkan** pemanggil; `rules` tetap tidak
menyimpan state. Tes: `test_konteks_lama_tanpa_field_dca_tetap_terbaca_mati`.

---

## 7. `main_loop.py` — denyut, jawaban, pemulihan

| Fungsi | Perubahan | Alasan | Tes pengunci | Pengaman utuh |
|--------|-----------|--------|--------------|---------------|
| `tick_cycle` | Sakelar DCA dihitung per siklus **sebelum** polling; `dca_lewati` membawa pair yang sudah terkirim/habis | `/ulang` pada siklus yang sama harus memakai keputusan bulan yang sama | `test_siklus_pertama_bulan_mengusulkan_dca_dan_membuka_jendela`, `test_siklus_berikutnya_tidak_mengusulkan_dca_lagi` | Keputusan bulan tetap milik `dca_window` |
| `tick_cycle` | Rekonsiliasi dijalankan **sebelum** usulan | Buku yang tidak bisa dipercaya tidak boleh menghasilkan order | `test_selisih_berhenti_sebelum_ada_usulan` | Selisih = `halted`, nol usulan |
| `tick_cycle` | Metadata pair disegarkan **sebelum** halt izin (`ce177d8`, `f83eb00`) | Halt izin yang lama membekukan cache metadata selamanya | `test_halt_izin_tetap_menyegarkan_metadata_tanpa_order`, `test_refresh_metadata_gagal_dicatat_dan_diberitahu_sekali_sehari` | Penyegaran hanya membaca; tidak membuka jalur order |
| `tick_cycle` | `permissions_ok` sebelum keputusan apa pun | Izin dicabut = tidak ada usulan baru | `test_izin_tarik_dana_menghentikan_tick_sebelum_usulan` | Sama seperti sebelumnya, hanya dipindah lebih awal |
| `tick_cycle` | Urutan: polling → pengingat → `engine.tick`; `/ulang` menutup tick siklus itu | Usulan ulang memakai `now` sama → id kembar menimpa catatan | `test_dua_irama_tick_tiap_heartbeat_polling_tiap_bangun`, `test_ulang_antar_denyut_dinaikkan_jadi_denyut_penuh` | Satu tick per siklus |
| `tick_cycle` | Peringatan harga aset non-target hilang, sekali per hari, denyut jalan (`f0fca0b`) | BCH tanpa harga dulu menghentikan seluruh denyut termasuk DCA BTC/ETH/SOL | `test_harga_bch_hilang_dca_target_tetap_diusulkan` | Pair target tanpa harga tetap `ValueError` = halt |
| `tick_cycle` | `_catat_dca` dengan `tunda` | Bulan tidak boleh ditutup saat konfirmasi belum terkirim atau tick berhenti | `test_dca_gagal_di_gerbang_menutup_bulan_sekali`, `test_dca_saldo_kurang_tidak_menutup_bulan_dan_usul_begitu_saldo_cukup` | Penutupan bulan hanya lewat `dca_window.mark_skip` |
| `baca_konteks` | Hanya pair target wajib punya harga; cache harga maks 120 detik (`27bdd70`, `f0fca0b`) | Harga basi tidak boleh dipakai gerbang 0,5% | `test_baca_konteks_tolak_cache_lebih_dari_120_detik`, `test_polling_tidak_memakai_cache_basi_untuk_gerbang_harga`, `test_baca_konteks_harga_non_target_hilang_tidak_halt` | Gagal baca konteks = jawaban tidak diterapkan |
| `_jawab` | Konteks segar sebelum jawaban pertama; `revisi` diperiksa; jendela DCA ditutup **setelah** hasil final | Saldo bisa berubah sejak denyut; tombol lama tidak boleh mengirim | `test_jawaban_memakai_konteks_segar_sebelum_order`, `test_gagal_baca_konteks_tidak_menulis_setuju_dan_tombol_bisa_dipakai_lagi`, `test_setuju_dca_ditolak_gerbang_tidak_menutup_jendela` | Gagal baca = tidak ada order; jendela tetap terbuka |
| `_terapkan_setuju_dca` (baru) | Tutup jendela hanya bila `sent` atau (bayangan) outcome `setuju` | Penolakan gerbang dulu menutup jendela = bulan hilang tanpa order | `test_setuju_dca_terkirim_menutup_jendela`, `test_setuju_dca_ditolak_gerbang_tidak_menutup_jendela` | Jendela hanya ditutup lewat `dca_window` |
| `_muat_usulan` | Memulihkan usulan menunggu + offset Telegram setelah restart (`1883f7d`, `718e8be`) | Restart dulu membuat tombol yang sudah dikirim jadi "id tak dikenal" | `test_usulan_menunggu_dipulihkan_setelah_restart`, `test_usulan_dca_tidak_dimuat_setelah_jendela_dibatalkan` | Usulan kedaluwarsa tidak dimuat |
| `apply_ulang` | `/ulang` menilai ulang dengan data sekarang; mencatat jendela satu kali | Angka lama tidak boleh dipakai ulang | `test_ulang_mengusulkan_langsung_dengan_harga_terbaru`, `test_ulang_hanya_mencatat_jendela_satu_kali` | Tetap lewat `engine.tick` (semua gerbang) |
| `status_ringkas`, `_status_harian`, `_sapa_awal`, `_sapa_boleh`, `_catat_sapa` | Status harian + commit; dedupe tanggal di `bot_state`; sapa maks 1×/jam; `_COMMIT` dibaca sekali saat start (`ed50fc3`, `3728866`, `6eb0f8d`) | Restart/crash-loop dulu mengirim status dan sapa berulang; proses panjang tidak pernah mengirim status baru | `test_status_harian_tidak_diulang_setelah_restart_hari_yang_sama`, `test_status_harian_dikirim_lagi_besoknya`, `test_sapa_awal_maksimal_sekali_per_jam_setelah_crash_loop`, `test_commit_status_dibaca_sekali_saat_start` | Pesan status/sapa tidak menyentuh jalur order |
| `set_shadow_day`, `aktifkan_live` | Nilai absolut, tidak pernah menyalakan sakelar; baris audit ditulis dalam transaksi yang sama (`5cc580d`, `f6ba6e9`) | Perubahan sakelar tanpa jejak tidak bisa diaudit | `test_set_shadow_day_tidak_pernah_menyalakan_sakelar`, `test_aktifkan_live_menolak_sebelum_hari_7`, `test_audit_sakelar_mencatat_perubahan_shadow_day` | `aktifkan_live` tetap hanya dipanggil manusia |
| `main` | Menolak start bila offset zona mesin bukan +07:00 (`6a9a93c`) | Zona UTC menggeser jam DCA, DND, dan batas 21.00 diam-diam | `test_main_menolak_start_bila_offset_zona_bukan_wib` | Menolak start = tidak ada order |
| `_proses_batch` | Update beracun tidak membuang update saudaranya; konteks dibaca sebelum jawaban pertama (`1fe72bb`) | Satu update rusak dulu menghentikan seluruh batch | `test_update_beracun_tidak_membuang_saudaranya`, `test_pembaca_gagal_denyut_dilewati_polling_ditahan` | Gagal baca konteks = jawaban tidak diterapkan |

---

## 8. `dca_window.py` — jendela bulanan & skema `bot_state`

| Fungsi | Perubahan | Alasan | Tes pengunci | Pengaman utuh |
|--------|-----------|--------|--------------|---------------|
| `ZONA_LOKAL`, `_lokal` (baru) | Waktu eksplisit `Asia/Jakarta`, bukan `time.localtime` (`be42cff`) | VPS UTC menggeser 09.00, DND, dan batas 21.00 | `test_zona_lokal_eksplisit_wib` | Batas jam tetap konstanta bernama |
| `ensure_bot_state` | Migrasi idempoten: `shadow_checked_day`, `telegram_offset`, `status_terakhir_hari`, `sapa_terakhir_ts`; tabel `bot_state_audit`; `sqlite3.Row` (`1883f7d`, `5cc580d`, `3728866`) | DB lama harus bisa dibuka tanpa galat, tanpa kehilangan kolom | `test_ensure_bot_state_idempotent`, `test_kolom_penanda_hari_ditambahkan_idempoten`, `test_ensure_bot_state_menambah_kolom_status_dan_sapa`, `test_ensure_bot_state_membuat_tabel_audit_sakelar` | Satu modul, satu skema; tidak menyentuh tabel warisan `portfolio_store` |
| `_baris` | Jalur baca tidak commit; bootstrap hanya bila tabel belum ada (`be42cff`) | `ensure_bot_state` ber-commit dan dulu terpanggil dari setiap baca | `test_ensure_bot_state_idempotent`, `test_crashed_between_audit_and_status_recovers` — **belum ada tes khusus** untuk "baca tidak commit" | Tulisan state tetap lewat fungsi yang commit |
| `catat_audit_sakelar` (baru) | Tidak commit; pemanggil commit bersama update sakelar (`5cc580d`) | Audit dan perubahan sakelar tidak boleh terpisah | `test_audit_sakelar_mencatat_sakelar_nyala_dan_mati`, `test_crashed_between_audit_and_status_recovers` | Tidak ada perubahan sakelar tanpa baris audit |
| `mark_confirmed`, `mark_missed`, `mark_batal`, `mark_skip`, `dca_state`, `boleh_usul_dca` | Akses kolom lewat nama (`sqlite3.Row`); semantik penolakan tidak berubah | Indeks kolom bergeser diam-diam saat skema bertambah | `test_confirm_during_pending_window_works`, `test_mark_missed_does_not_overwrite_confirmed`, `test_boleh_usul_menolak_jendela_lintas_bulan` | Satu keputusan per bulan; status tak dikenal = `ValueError` |

---

## 9. `telegram_gate.py` — tombol dan polling

| Perubahan | Alasan | Tes pengunci | Pengaman utuh |
|-----------|--------|--------------|---------------|
| Callback membawa nomor `revisi`; `data` ≤64 byte (`bf378f4`) | Tombol dari pesan pra-re-quote tidak boleh menyetujui angka baru | `test_callback_data_maks_64_byte_untuk_id_terpanjang`, `test_parse_callback_button` | Tombol lama (tanpa revisi) dibaca sebagai revisi 0 |
| `parse_callback` lama → revisi 0 | Pesan yang sudah terkirim tetap berfungsi | `test_parse_callback_lama_tanpa_revisi_dianggap_revisi_nol` | Tidak ada tombol tanpa id usulan |
| `live_fetch` melempar pada `ok:false`/HTTP galat | Kegagalan Telegram tidak boleh terlihat seperti "tidak ada jawaban" | `test_live_fetch_balasan_ok_false_melempar_bukan_diam`, `test_live_fetch_galat_status_http_dibawa_melempar` | Galat → jawaban tidak diproses, bukan order |

---

## 10. Yang sengaja **tidak** berubah

- Satu-satunya situs kirim order: `engine.on_answer` → `executor.submit_once`.
- Urutan enam pengaman di `on_answer` (0 → 8) tidak pernah diurutkan ulang.
- `safeguards.MAX_ORDER_IDR`, `MAX_PER_ASSET_PER_DAY`, `COOLDOWN_SECONDS`,
  `RESERVE_MAX_FRACTION` tidak dilonggarkan.
- Parameter strategi (`MONTHLY_IDR`, bobot target, ambang take-profit, `DND`,
  jam kirim) tidak diubah.
- `rules.py` tetap tidak mengimpor `safeguards`; tidak ada modul yang menyimpan
  state order di variabel global.
- Tidak ada jalur baru yang melewati gerbang, termasuk jalur "hanya
  menyegarkan metadata", "hanya status", dan "hanya harga non-target".

## 11. Temuan terbuka (bukan cacat jalur order)

1. `rebalance_actions` masih menjumlah nilai aset non-target di totalnya —
   hanya memengaruhi saran dashboard, tidak pernah dipanggil `engine`.
2. `rules.max_position_ok` tidak punya pemanggil produksi (kode mati).
3. Aset non-target tanpa harga kini tidak tampil sama sekali (kuantitas ikut
   tersembunyi), bukan tampil Rp 0.
