"""postprocess.state：on_thr_w / post_min_on / post_fill_short_off（§12.3）。"""

import numpy as np

from nilm.postprocess.state import (enforce_min_on, fill_short_off,
                                    postprocess_state, power_to_state)


def test_power_to_state_threshold():
    p = np.array([0, 9.9, 10.0, 50])
    assert list(power_to_state(p, 10.0)) == [False, False, True, True]


def test_enforce_min_on_removes_short_on():
    st = np.array([1, 1, 1, 0, 1, 0, 1, 1, 1], dtype=bool)
    out = enforce_min_on(st, min_on=2)
    assert list(out) == [True, True, True, False, False, False, True, True, True]


def test_fill_short_off_between_on():
    st = np.array([1, 1, 0, 1, 1, 0, 0, 0, 0, 1, 1], dtype=bool)
    out = fill_short_off(st, max_off=1)
    assert list(out) == [True] * 5 + [False, False, False, False] + [True, True]
    out3 = fill_short_off(st, max_off=3)
    assert not out3.all()                     # 长度 4 的关断 > max_off=3，不填充
    out4 = fill_short_off(st, max_off=4)
    assert out4.all()


def test_combined_postprocess():
    p = np.array([100, 100, 5, 100, 100, 0, 100, 100, 100], dtype=float)
    out = postprocess_state(p, on_thr_w=10.0, min_on=1, fill_off=1)
    assert list(out) == [True] * 9
