"""
Background Updater untuk mengambil data market real-time dan menyimpannya ke SharedState.
Menggunakan strategy_mean_reversion untuk kalkulasi z-score.
"""

import sqlite3
import threading
import time
import logging
from typing import Dict, Any, List, Optional

import scanner
import strategy_mean_reversion as mr

from dashboard.utils.shared_state import SharedState

try:
    from streamlit.runtime.scriptrunner import add_script_run_ctx
    STREAMLIT_AVAILABLE = True
except ImportError:
    add_script_run_ctx = None
    STREAMLIT_AVAILABLE = False

logger = logging.getLogger(__name__)


# Mapping pair_id ke symbol Tokocrypto
PAIR_TO_SYMBOL = {
    "btc_idr": "BTCIDR",
    "eth_idr": "ETHIDR",
    "usdt_idr": "USDTIDR",
    "bnb_idr": "BNBIDR",
    "doge_idr": "DOGEIDR",
    "ada_idr": "ADAIDR",
    "xrp_idr": "XRPIDR",
    "sol_idr": "SOLIDR",
    "avax_idr": "AVAXIDR",
    "arb_idr": "ARBIDR",
    "ondo_idr": "ONDOIDR",
    "hbar_idr": "HBARIDR",
    "wld_idr": "WLDIDR",
}


class BackgroundUpdater:
    """
    Background thread yang mengambil harga dari exchange, 
    menghitung z-score via strategy, dan menyimpan ke SharedState.
    """

    def __init__(
        self,
        shared_state: SharedState,
        strategy: mr.MeanReversionStrategy,
        pairs: List[str],
        interval: int = 3,
        db_path: str = "trades.db"
    ):
        self.state = shared_state
        self.strategy = strategy
        self.pairs = pairs
        self.interval = interval
        self.db_path = db_path
        self._running = False
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Mulai background thread."""
        if self._running:
            logger.warning("BackgroundUpdater sudah berjalan")
            return
        
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        if STREAMLIT_AVAILABLE and add_script_run_ctx:
            add_script_run_ctx(self._thread)
        self._thread.start()
        logger.info(f"BackgroundUpdater started for {len(self.pairs)} pairs, interval={self.interval}s")
        print("[DEBUG] BackgroundUpdater thread started: {0}, alive={1}".format(self._thread.name, self._thread.is_alive()))

    def stop(self) -> None:
        """Hentikan background thread."""
        if not self._running:
            return
        
        self._running = False
        if self._thread:
            self._thread.join(timeout=10)
        logger.info("BackgroundUpdater stopped")

    def _run(self) -> None:
        """Main loop background thread.

        Sengaja tidak pernah melempar keluar: kegagalan mengambil harga (mis.
        HTTP 429) harus terlihat di SharedState, bukan mematikan thread diam-
        diam — kasus nyata 2026-10-19: backoff 429 di dalam loop pair membuat
        iterasi memakan belasan menit dan terminal hanya memunculkan "HTTP
        429".
        """
        print("[DEBUG] BackgroundUpdater _run started")
        while self._running:
            start_time = time.time()

            try:
                market_data = self._fetch_all_pairs()
                self.state.update({
                    "market_data": market_data,
                    "last_update": time.time(),
                    "last_error": None
                })

            except Exception as e:
                error_msg = f"{type(e).__name__}: {e}"
                logger.error("BackgroundUpdater error: {0}".format(error_msg))
                self.state.set("last_error", error_msg)

            # Sleep hingga interval selesai
            elapsed = time.time() - start_time
            sleep_time = max(0, self.interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _fetch_all_pairs(self) -> Dict[str, Any]:
        """Ambil data untuk semua pairs; pair gagal pakai harga lama + tanda basi.

        Tanpa penyimpanan ini, satu pair gagal karena rate limit lenyap dari
        dashboard dan tombolnya berubah jadi "Error" — walau harga lama masih
        layak ditampilkan sambil menunggu.
        """
        hasil_baru: Dict[str, Any] = {}
        for pair in self.pairs:
            try:
                data = self._fetch_pair_data(pair)
            except Exception as e:
                logger.warning(f"Error fetching {pair}: {e}")
                data = None
            if data:
                hasil_baru[pair] = data

        market_data: Dict[str, Any] = {}
        for pair in self.pairs:
            if pair in hasil_baru:
                market_data[pair] = hasil_baru[pair]
                continue
            lama = self.state.get("market_data", {}).get(pair)
            if lama and "error" not in lama:
                lama["basi"] = True   # harga terakhir, tapi sudah kadaluarsa
                market_data[pair] = lama
            else:
                market_data[pair] = {"error": "gagal mengambil harga (rate limit/koneksi)"}

        return market_data

    def _fetch_pair_data(self, pair: str) -> Optional[Dict[str, Any]]:
        """
        Ambil data untuk satu pair.
        """
        # 1. Dapatkan harga dari kedua exchange
        symbol = PAIR_TO_SYMBOL.get(pair)
        if not symbol:
            return None
        
        try:
            bid_i, ask_i = scanner.indodax_price(pair)
            bid_t, ask_t = scanner.tokocrypto_price(symbol)
        except Exception as e:
            logger.warning(f"Failed to fetch prices for {pair}: {e}")
            return None

        # Kedua harga wajib ada sebelum dihitung; `None` berarti rate limit /
        # koneksi gagal — kembalikan None supaya _fetch_all_pairs pakai data
        # lama, jangan sampai memicu TypeError yang mematikan seluruh iterasi.
        if bid_i is None or bid_t is None:
            logger.warning(f"Harga tidak lengkap untuk {pair} (rate limit/koneksi)")
            return None
        
        # 2. Hitung spread
        spread_I_to_T, spread_T_to_I = mr.MeanReversionStrategy.calculate_spread(
            bid_i, ask_i, bid_t, ask_t
        )
        
        # 3. Update strategy history (internal rolling window)
        self.strategy.update_history(pair, spread_I_to_T, spread_T_to_I)
        
        # 4. Hitung z-score
        z_I_to_T = self.strategy.compute_zscore(pair, "I_to_T")
        z_T_to_I = self.strategy.compute_zscore(pair, "T_to_I")
        
        # 5. Cek sinyal entry (gunakan default filter values)
        # Note: buy_ratio, volume, momentum butuh data dari analyzer
        # Untuk sekarang gunakan default yang permissive
        signal = self.strategy.check_signal(
            pair=pair,
            bid_i=bid_i, ask_i=ask_i,
            bid_t=bid_t, ask_t=ask_t,
            buy_ratio=0.6,      # default permissive
            total_vol=1000,     # placeholder
            median_vol=500,     # placeholder
            change_pct=0.0      # placeholder
        )
        
        # 6. Build result
        result = {
            "pair": pair,
            "symbol": symbol,
            "z_I_to_T": round(z_I_to_T, 4),
            "z_T_to_I": round(z_T_to_I, 4),
            "spread_I_to_T": round(spread_I_to_T, 2),
            "spread_T_to_I": round(spread_T_to_I, 2),
            "bid_i": bid_i,
            "ask_i": ask_i,
            "bid_t": bid_t,
            "ask_t": ask_t,
            "signal": signal,
            "timestamp": time.time(),
        }
        
        return result

    def is_running(self) -> bool:
        """Cek apakah updater sedang berjalan."""
        return self._running


def create_background_updater(
    shared_state: SharedState,
    strategy: mr.MeanReversionStrategy,
    pairs: Optional[List[str]] = None,
    interval: int = 3,
    db_path: str = "trades.db"
) -> BackgroundUpdater:
    """
    Factory function untuk membuat BackgroundUpdater dengan default pairs.
    """
    if pairs is None:
        pairs = [p[1] for p in scanner.PAIRS]
    
    return BackgroundUpdater(
        shared_state=shared_state,
        strategy=strategy,
        pairs=pairs,
        interval=interval,
        db_path=db_path
    )