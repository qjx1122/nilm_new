"""任务⑯：全关日样本加权（off_day_weight）——单元（权重语义）+ e2e（参数流）。

背景：2844 day_gate 试点 infer 实录（2026-09-14）定位失效模式=全关天虚报
（07-01~04 假期停产日 fp 139/258，占全月 70% 虚报）；机理=训练池开机天占比 71%
→ 全关天 MSE 占比过小。治理=按日型加权训练损失（off_day_weight），默认 1.0
（与历史行为逐位一致）。
"""

import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from nilm.common.contracts import Status
from nilm.models.seq_models import TransformerDisaggregator
from nilm.pipeline.batch import run_batch

from conftest import USER_KEY, write_user_dir

OTHER_KEY = "800080252842_4206894986488"


def _two_day_index() -> pd.DatetimeIndex:
    return pd.date_range("2026-01-01", periods=192, freq="15min")   # 恰 2 天


def test_off_day_weights_day_level():
    """全关日（日峰值<thr）整日加权、开机日不加权、均值归一 1.0、权重比=boost。"""
    m = TransformerDisaggregator(off_day_weight=3.0, off_day_thr_w=10.0)
    y = np.concatenate([np.full(96, 50.0), np.zeros(96)]).astype(np.float32)
    w = m._off_day_weights(y, _two_day_index())
    assert np.allclose(w[:96], w[0])                    # 开机日逐点同权
    assert np.allclose(w[96:], w[96])                   # 全关日逐点同权
    assert w[96] > w[0]
    assert w[96] / w[0] == pytest.approx(3.0)           # 相对权重=boost
    assert w.mean() == pytest.approx(1.0, abs=1e-6)     # 整体损失量级不变
    assert w.dtype == np.float32


def test_off_day_weights_default_noop():
    """默认 off_day_weight=1.0：恒全 1，与历史行为逐位一致（回归锚）。"""
    m = TransformerDisaggregator()
    y = np.concatenate([np.full(96, 50.0), np.zeros(96)]).astype(np.float32)
    assert np.allclose(m._off_day_weights(y, _two_day_index()), 1.0)
    assert np.allclose(m._off_day_weights(y, None), 1.0)


def test_off_day_weights_peak_day_not_off():
    """日峰值≥thr 的天不算全关日——日级判定不被夜间低负荷点稀释（对比点级判定）。"""
    m = TransformerDisaggregator(off_day_weight=3.0, off_day_thr_w=10.0)
    y = np.concatenate([np.full(95, 1.0), [200.0], np.zeros(96)]).astype(np.float32)
    w = m._off_day_weights(y, _two_day_index())
    assert w[0] == w[95]                                # 第 1 天有 200W 峰值→整日不加权
    assert w[96] > w[0]                                 # 第 2 天全零→整日加权


def test_off_day_weights_no_index_point_level():
    """index 缺省退化点级判定（管线恒提供索引，正常不达此分支）。"""
    m = TransformerDisaggregator(off_day_weight=2.0, off_day_thr_w=10.0)
    y = np.array([5.0, 5.0, 50.0, 50.0], np.float32)
    w = m._off_day_weights(y, None)
    assert w[0] > w[2]
    assert w.mean() == pytest.approx(1.0, abs=1e-6)


def test_e2e_model_params_override_off_day_weight(tmp_path, base_cfg):
    """e2e：用户级 model_params 按模型名合并到 base models.params——transformer
    带 off_day_weight 完成训练；未覆盖用户保持默认 1.0（参数流闭环验证）。"""
    data_root = tmp_path / "data"
    write_user_dir(data_root, USER_KEY, days=21)
    write_user_dir(data_root, OTHER_KEY, days=21, seed=7)
    write_user_dir(data_root, USER_KEY, days=21, mode_dir="infers")
    write_user_dir(data_root, OTHER_KEY, days=21, seed=7, mode_dir="infers")

    base = dict(base_cfg)
    base["models"] = base["models"] + [{
        "name": "transformer",
        "params": {"window": 96, "epochs": 2, "batch_size": 64, "d_model": 16,
                   "nhead": 2, "num_layers": 1, "dim_feedforward": 32,
                   "patience": 2}}]
    base_p = tmp_path / "base.yaml"
    base_p.write_text(yaml.safe_dump(base, allow_unicode=True, sort_keys=False),
                      encoding="utf-8")
    tcfg = {USER_KEY: {"target_col": "p1", "split_strategy": "time",
                       "model_params": {"transformer": {"off_day_weight": 3.0}}},
            OTHER_KEY: {"target_col": "p1", "split_strategy": "time"}}
    t_p = tmp_path / "tf.json"
    t_p.write_text(json.dumps(tcfg), encoding="utf-8")

    info = run_batch(t_p, base_config_path=base_p, data_root=data_root,
                     output_root=tmp_path / "outputs", stages=("train",))
    table = pd.read_csv(info["status_csv"])
    for uk in (USER_KEY, OTHER_KEY):
        r = table[(table["user_key"] == uk) & (table["mode"] == "train")].iloc[0]
        assert r["status"] == Status.OK, r["message"]

    def load_pkl(uk: str):
        d = sorted((tmp_path / "outputs" / uk / "train").rglob("transformer.pkl"))
        assert d, f"{uk} 无 transformer 产物"
        with open(d[-1], "rb") as f:
            return pickle.load(f)

    m1, m2 = load_pkl(USER_KEY), load_pkl(OTHER_KEY)
    assert m1.params["off_day_weight"] == 3.0           # 用户级覆盖生效
    assert m2.params["off_day_weight"] == 1.0           # 未覆盖用户不受影响


def test_user_config_model_params_merge_semantics():
    """model_params 跨层逐键合并（W-3 教训）：_default 与用户级同名模型逐键融合，
    非同名模型互不覆盖；非法结构报 UserConfigError。"""
    from nilm.pipeline.user_config import UserConfigError, resolve_user_config

    cfg = {"_default": {"model_params": {"transformer": {"off_day_weight": 2.0,
                                                         "epochs": 5}}},
           USER_KEY: {"model_params": {"transformer": {"off_day_weight": 3.0},
                                       "ridge": {"alpha": 2.0}}}}
    merged = resolve_user_config(USER_KEY, cfg)
    assert merged["model_params"]["transformer"] == {"off_day_weight": 3.0,
                                                     "epochs": 5}   # 逐键融合
    assert merged["model_params"]["ridge"] == {"alpha": 2.0}
    bad = {USER_KEY: {"model_params": {"transformer": 3.0}}}
    with pytest.raises(UserConfigError):
        resolve_user_config(USER_KEY, bad)
