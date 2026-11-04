"""Smoke tests for the stable `core` facade."""
import sys

import core


def test_core_exports_production_objects():
    from dca_window import mark_pending as production_mark_pending
    from engine import on_answer as production_on_answer
    from strategy_mean_reversion import MeanReversionStrategy

    assert core.on_answer is production_on_answer
    assert core.MeanReversionStrategy is MeanReversionStrategy
    assert core.mark_pending is production_mark_pending


def test_main_is_lazy():
    assert "core.main_loop" not in sys.modules
    assert callable(core.main)
