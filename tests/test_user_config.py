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


# ---------------------------------------------------------------- 顶级全局 infer_model
def test_global_infer_model_applies_to_all_users():
    """顶级 infer_model：配置后作为所有用户的全局默认推理模型。"""
    cfg = _cfg(infer_model="ridge")
    merged = resolve_user_config(UK, cfg)
    assert merged["infer_model"] == "ridge"
    assert merged["_provenance"]["infer_model"] == "global(顶级)"


def test_global_infer_model_absent_keeps_original_logic():
    """未配置顶级 infer_model：维持硬编码默认 None（推理走 best_model 原逻辑）。"""
    merged = resolve_user_config(UK, _cfg())
    assert merged["infer_model"] is None


def test_user_level_overrides_global_infer_model():
    """优先级：用户级 > _default > 顶级全局。"""
    cfg = _cfg(infer_model="ridge")
    cfg[UK]["infer_model"] = "history_profile"
    merged = resolve_user_config(UK, cfg)
    assert merged["infer_model"] == "history_profile"
    assert merged["_provenance"]["infer_model"] == UK

    cfg2 = _cfg(infer_model="ridge")
    cfg2["_default"]["infer_model"] = "proportional"
    merged2 = resolve_user_config(UK, cfg2)
    assert merged2["infer_model"] == "proportional"
    assert merged2["_provenance"]["infer_model"] == "_default"


def test_global_infer_model_not_a_user_key():
    """顶级 infer_model 是全局配置键，不得被当作用户数据名加载。"""
    keys = list_user_keys(_cfg(infer_model="ridge"))
    assert keys == [UK]


def test_global_infer_model_must_be_string():
    with pytest.raises(UserConfigError, match="infer_model"):
        resolve_user_config(UK, _cfg(infer_model=123))


# ---------------------------------------------------------------- 用户级 pbus_mode
def test_pbus_mode_default_and_override():
    """全局默认 auto；支持 _default 与用户级覆盖为 raw/scaled/auto；显式 null 回退 auto；非法值报错。"""
    # 1) 未配置默认 auto
    m0 = resolve_user_config(UK, _cfg())
    assert m0["pbus_mode"] == "auto"

    # 2) _default 配置 scaled，用户级未配 -> 继承 scaled
    c1 = _cfg()
    c1["_default"]["pbus_mode"] = "scaled"
    m1 = resolve_user_config(UK, c1)
    assert m1["pbus_mode"] == "scaled"
    assert m1["_provenance"]["pbus_mode"] == "_default"

    # 3) 用户级覆盖 _default（raw / auto 均可）
    c2 = _cfg()
    c2["_default"]["pbus_mode"] = "scaled"
    c2[UK]["pbus_mode"] = "raw"
    m2 = resolve_user_config(UK, c2)
    assert m2["pbus_mode"] == "raw"
    assert m2["_provenance"]["pbus_mode"] == UK

    c2b = _cfg()
    c2b["_default"]["pbus_mode"] = "raw"
    c2b[UK]["pbus_mode"] = "auto"
    assert resolve_user_config(UK, c2b)["pbus_mode"] == "auto"

    # 4) 显式 null 回退全局默认 auto
    c3 = _cfg()
    c3[UK]["pbus_mode"] = None
    assert resolve_user_config(UK, c3)["pbus_mode"] == "auto"

    # 5) 非法取值拒绝
    for bad in ("zscore", "linear", 123, True):
        c_bad = _cfg()
        c_bad[UK]["pbus_mode"] = bad
        with pytest.raises(UserConfigError, match="pbus_mode"):
            resolve_user_config(UK, c_bad)


# ---------------------------------------------------------------- on_thr_w / decision_thr_w auto 与显式优先
def test_on_thr_and_decision_thr_auto_contract():
    """验证 on_thr_w 与 decision_thr_w 支持 'auto'、向后兼容默认值及显式优先覆盖。"""
    # 1) 硬编码默认：完全缺省时 on_thr_w=10.0（向后兼容），decision_thr_w='auto'
    m0 = resolve_user_config(UK, {UK: {}})
    assert m0["on_thr_w"] == 10.0
    assert m0["decision_thr_w"] == "auto"

    # 2) 支持大小写无关的 "auto" 与 decision_thr_w="on_thr"
    c1 = _cfg()
    c1[UK]["on_thr_w"] = "AUTO"
    c1[UK]["decision_thr_w"] = "Auto"
    m1 = resolve_user_config(UK, c1)
    assert m1["on_thr_w"] == "auto"
    assert m1["decision_thr_w"] == "auto"

    c1b = _cfg()
    c1b[UK]["decision_thr_w"] = "ON_THR"
    assert resolve_user_config(UK, c1b)["decision_thr_w"] == "on_thr"

    # 3) 显式数值优先于 _default 的 "auto"，且归一化为 float
    c2 = _cfg()
    c2["_default"]["on_thr_w"] = "auto"
    c2["_default"]["decision_thr_w"] = "auto"
    c2[UK]["on_thr_w"] = 50
    c2[UK]["decision_thr_w"] = 30
    m2 = resolve_user_config(UK, c2)
    assert m2["on_thr_w"] == 50.0 and isinstance(m2["on_thr_w"], float)
    assert m2["decision_thr_w"] == 30.0 and isinstance(m2["decision_thr_w"], float)
    assert m2["_provenance"]["on_thr_w"] == UK
    assert m2["_provenance"]["decision_thr_w"] == UK

    # 4) 显式写 null 回退硬编码默认
    c3 = _cfg()
    c3[UK]["on_thr_w"] = None
    c3[UK]["decision_thr_w"] = None
    m3 = resolve_user_config(UK, c3)
    assert m3["on_thr_w"] == 10.0
    assert m3["decision_thr_w"] == "auto"

    # 5) 非法取值拒绝（含布尔、非法字符串、越界数值）
    for bad_on in ("invalid", "on_thr", 0, -5.0, 9999.0, True, False):
        cb = _cfg()
        cb[UK]["on_thr_w"] = bad_on
        with pytest.raises(UserConfigError, match="on_thr_w"):
            resolve_user_config(UK, cb)

    for bad_dec in ("invalid", "raw", 0, -1.0, 6000.0, True, False):
        cb = _cfg()
        cb[UK]["decision_thr_w"] = bad_dec
        with pytest.raises(UserConfigError, match="decision_thr_w"):
            resolve_user_config(UK, cb)


# ---------------------------------------------------------------- post_min_on / post_fill_short_off auto 与向后兼容
def test_postprocess_params_auto_and_explicit_contract():
    """验证 post_min_on 与 post_fill_short_off 支持 'auto'、独立混合配置、null 回退及严格整数校验。"""
    # 1) 未写时保持历史硬编码默认 1 / 3（100% 向后兼容）
    m0 = resolve_user_config(UK, {UK: {}})
    assert m0["post_min_on"] == 1 and isinstance(m0["post_min_on"], int)
    assert m0["post_fill_short_off"] == 3 and isinstance(m0["post_fill_short_off"], int)

    # 2) 支持大小写无关的 "auto"，以及一个显式整数 + 一个 "auto" 独立解耦配置
    c1 = _cfg()
    c1[UK]["post_min_on"] = "AUTO"
    c1[UK]["post_fill_short_off"] = 0
    m1 = resolve_user_config(UK, c1)
    assert m1["post_min_on"] == "auto"
    assert m1["post_fill_short_off"] == 0 and isinstance(m1["post_fill_short_off"], int)

    c1b = _cfg()
    c1b["_default"]["post_min_on"] = "auto"
    c1b["_default"]["post_fill_short_off"] = "Auto"
    c1b[UK]["post_min_on"] = 4
    m1b = resolve_user_config(UK, c1b)
    assert m1b["post_min_on"] == 4
    assert m1b["post_fill_short_off"] == "auto"
    assert m1b["_provenance"]["post_min_on"] == UK
    assert m1b["_provenance"]["post_fill_short_off"] == "_default"

    # 3) 显式 null 回退硬编码默认 1 / 3
    c2 = _cfg()
    c2[UK]["post_min_on"] = None
    c2[UK]["post_fill_short_off"] = None
    m2 = resolve_user_config(UK, c2)
    assert m2["post_min_on"] == 1
    assert m2["post_fill_short_off"] == 3

    # 4) 拒绝布尔、非整浮点、越界整数（<0 或 >96）与未知字符串
    for key in ("post_min_on", "post_fill_short_off"):
        for bad in ("invalid", "on_thr", -1, 97, 1.5, True, False):
            cb = _cfg()
            cb[UK][key] = bad
            with pytest.raises(UserConfigError, match=key):
                resolve_user_config(UK, cb)



