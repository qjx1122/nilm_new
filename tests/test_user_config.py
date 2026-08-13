"""pipeline.user_config：§12 键优先级、保留键、字段校验、映射层。"""

import json

import pytest

from nilm.pipeline.user_config import (UserConfigError, list_user_keys,
                                       resolve_user_config)

UK = "800080270708_4206602981958"


def _cfg(**extra):
    base = {
        UK: {"target_col": "p1+p2", "on_thr_w": 50.0},
        "_default": {"on_thr_w": 10.0, "split_ratios": [0.6, 0.2, 0.2],
                     "split_strategy": "stratified_day"},
        "_note_": "保留键，不得作为用户加载",
    }
    base.update(extra)
    return base


def test_priority_user_key_over_default_over_hardcoded():
    merged = resolve_user_config(UK, _cfg())
    assert merged["on_thr_w"] == 50.0                # user_key 覆盖 _default
    assert merged["split_ratios"] == [0.6, 0.2, 0.2]  # _default 覆盖硬编码
    assert merged["post_min_on"] == 1                 # 硬编码默认（§12.3）
    assert merged["target_col"] == "p1+p2"


def test_reserved_keys_not_users():
    keys = list_user_keys(_cfg())
    assert keys == [UK]
    assert "_default" not in keys and "_note_" not in keys


def test_bare_user_id_needs_explicit_map():
    cfg = {"4206602981958": {"target_col": "p1"}}
    with pytest.raises(UserConfigError, match="禁止隐式猜测|_user_id_map"):
        list_user_keys(cfg)
    cfg["_user_id_map"] = {"4206602981958": UK}
    assert list_user_keys(cfg) == [UK]


@pytest.mark.parametrize("bad,field", [
    ({"on_thr_w": 0.0}, "on_thr_w"),            # < 0.001
    ({"on_thr_w": 6000}, "on_thr_w"),           # > 5000
    ({"split_ratios": [0.5, 0.5, 0.5]}, "split_ratios"),
    ({"split_strategy": "random"}, "split_strategy"),
    ({"post_min_on": -1}, "post_min_on"),
    ({"weather_latitude": 95}, "weather_latitude"),
])
def test_field_validation(bad, field):
    cfg = {UK: bad, "_default": {}}
    with pytest.raises(UserConfigError):
        resolve_user_config(UK, cfg)


def test_sections_passthrough():
    cfg = _cfg()
    cfg[UK]["train"] = {"include": [["2026-01-01", "2026-01-31"]]}
    cfg[UK]["splits"] = {"val": {"include": [["2026-01-30", "2026-01-30"]]}}
    merged = resolve_user_config(UK, cfg)
    assert merged["train"]["include"] == [["2026-01-01", "2026-01-31"]]
    assert "val" in merged["splits"]
