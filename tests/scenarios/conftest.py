import pytest

import engine
import main_loop


@pytest.fixture(autouse=True)
def _state_bersih():
    """Keadaan proses (usulan, offset, dedupe) mulai kosong tiap skenario."""
    main_loop.reset_state()
    engine.reset_state()
    yield
    main_loop.reset_state()
    engine.reset_state()
