"""on_thr_w / decision_thr_w 自动生成、显式优先、全程留痕与黄/红高亮日志测试集。"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nilm.common.contracts import INFERENCE_RESULT_REL, Status
from nilm.common.logging import _PlainFormatter, red_text, yellow_text
from nilm.pipeline.auto_thresholds import (
    check_offline_decision_drift,
    log_auto_decision_thr_train,
    resolve_auto_decision_thr_w,
    resolve_auto_on_thr_w,
    resolve_infer_thresholds,
    resolve_train_on_thr_w,
)
from nilm.pipeline.batch import run_batch
from nilm.pipeline.user_task import latest_done_dir
from scripts.audit_user_run import main as audit_main
from scripts.auto_run_steps2to5 import ensure_user_config, main as auto_run_main
from tests.conftest import USER_KEY


def _write_bimodal_user_dir(
    root: Path,
    user_key: str,
    mode_dir: str,
    days: int = 15,
    start: str = "2026-01-01",
    standby_w: float = 28.0,
    working_w: float = 680.0,
    bg_bus_kw: float = 220.0,
    seed: int = 42,
) -> Path:
    """构造真实双峰工业负荷数据（含全关日、待机纹波带与白天开机段），用于端到端 auto 阈值验证。"""
    rng = np.random.default_rng(seed)
    device, user = user_key.split("_")
    d = root / mode_dir / user_key
    d.mkdir(parents=True, exist_ok=True)

    n15 = 96 * days
    idx15 = pd.date_range(start, periods=n15, freq="15min")
    p1 = np.zeros(n15, dtype=np.float64)
    for day_i in range(days):
        sl = slice(day_i * 96, (day_i + 1) * 96)
        # 每第 4 天为全关休息日（仅待机纹波），其余天 08:00~18:00 开机
        is_off_day = (day_i % 4 == 2)
        day_arr = np.clip(standby_w * 0.7 + rng.normal(0.0, standby_w * 0.12, 96), 0.5, standby_w)
        if not is_off_day:
            day_arr[32:72] = working_w + rng.normal(0.0, 15.0, 40)
        p1[sl] = day_arr

    branch_df = pd.DataFrame({
        "time": idx15.strftime("%Y-%m-%d %H:%M:%S"),
        "p1": np.round(p1, 3),
        "p2": np.round(rng.uniform(0.0, 2.0, n15), 3),
    })

    # 5min 母线功率（W，经 apply_pt_ct /1000 后与分路 kW 对齐）：背景基荷波动 + p1 分路功率
    n5 = 288 * days
    idx5 = pd.date_range(start, periods=n5, freq="5min")
    p1_5m = np.repeat(p1, 3)
    bg_5m = bg_bus_kw + 35.0 * np.sin(2 * np.pi * (np.arange(n5) % 288) / 288) + rng.normal(0.0, 8.0, n5)
    ptotal_w = np.clip((bg_5m + p1_5m) * 1000.0, 5000.0, None)
    pa = ptotal_w / 3.0
    pb = ptotal_w / 3.0
    pc = ptotal_w - pa - pb
    ua = np.full(n5, 220.0) + rng.normal(0.0, 0.5, n5)
    ub = np.full(n5, 220.0) + rng.normal(0.0, 0.5, n5)
    uc = np.full(n5, 220.0) + rng.normal(0.0, 0.5, n5)
    pf = np.clip(0.92 + rng.normal(0.0, 0.01, n5), 0.7, 0.99)
    bus_df = pd.DataFrame({
        "event_time": idx5.strftime("%Y-%m-%d %H:%M:%S"),
        "load_iden_data0": ua, "load_iden_data1": ub, "load_iden_data2": uc,
        "load_iden_data3": pa / (ua * pf), "load_iden_data4": pb / (ub * pf),
        "load_iden_data5": pc / (uc * pf),
        "load_iden_data6": pa, "load_iden_data7": pb, "load_iden_data8": pc,
        "load_iden_data9": pf, "load_iden_data10": pf, "load_iden_data11": pf,
    })

    s_code, e_code = idx5[0].strftime("%y%m%d"), idx5[-1].strftime("%y%m%d")
    bus_df.to_csv(d / f"e241_{device}_{user}-Ch1-{s_code}-{e_code}.csv", index=False)
    branch_df.to_csv(d / f"{user}-{s_code}-{e_code}.csv", index=False)
    return d


# ---------------------------------------------------------------- 1. ANSI 黄色/红色日志格式化与文件去色
def test_yellow_and_red_ansi_helpers_and_plain_formatter():
    y_msg = yellow_text("[AUTO-ON-THR] on_thr_w=50")
    r_msg = red_text("[AUTO-ON-THR-WARN] 低分离度告警")
    assert y_msg.startswith("\033[93m") and y_msg.endswith("\033[0m")
    assert r_msg.startswith("\033[91m") and r_msg.endswith("\033[0m")

    fmt = _PlainFormatter("%(levelname)s | %(message)s")
    rec_y = logging.LogRecord("nilm.test", logging.WARNING, __file__, 1, "%s", (y_msg,), None)
    rec_r = logging.LogRecord("nilm.test", logging.WARNING, __file__, 1, "%s", (r_msg,), None)
    plain_y = fmt.format(rec_y)
    plain_r = fmt.format(rec_r)
    assert "\033[" not in plain_y and plain_y == "WARNING | [AUTO-ON-THR] on_thr_w=50"
    assert "\033[" not in plain_r and plain_r == "WARNING | [AUTO-ON-THR-WARN] 低分离度告警"


# ---------------------------------------------------------------- 2. on_thr_w="auto" 物理双峰定阈与黄/红日志
def test_resolve_auto_on_thr_w_scales_and_logs(caplog):
    rng = np.random.default_rng(0)

    # (a) 干净零待机大负荷（0~3 kW 关态，700 kW 工况，类 2844/815）-> 10.0 + 黄色日志
    y_clean = np.concatenate([rng.uniform(0.0, 3.0, 600), rng.normal(700.0, 15.0, 400)])
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        thr1, dec1 = resolve_train_on_thr_w(USER_KEY, {"on_thr_w": "auto"}, y_clean)
    assert thr1 == 10.0
    assert dec1["confidence"] == "high"
    assert any("\033[93m" in r.message and "[AUTO-ON-THR]" in r.message for r in caplog.records)

    # (b) 高待机纹波负荷（20~35 kW 待机，700 kW 工况，类 2842）-> 自动抬升至 50.0 + 黄色日志
    y_standby = np.concatenate([rng.uniform(18.0, 35.0, 600), rng.normal(700.0, 20.0, 400)])
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        thr2, dec2 = resolve_train_on_thr_w(USER_KEY, {"on_thr_w": "auto"}, y_standby)
    assert thr2 == 50.0
    assert dec2["confidence"] == "high"
    assert any("\033[93m" in r.message and "on_thr_w=50" in r.message for r in caplog.records)

    # (c) 小功率水泵（0~0.15 kW 待机，6.0 kW 工况）-> 自动下探至 <= 1.5 kW（不被硬编码 10.0 淹没）
    y_small = np.concatenate([rng.uniform(0.0, 0.15, 600), rng.normal(6.0, 0.2, 400)])
    thr3, dec3 = resolve_auto_on_thr_w(y_small)
    assert 0.2 <= thr3 <= 1.8
    assert dec3["confidence"] == "high"

    # (d) 多档位过渡带歧义负荷（0W + 120W 中间辅机占 20% + 700W 主工况）-> confidence=low + 红色告警日志
    y_ambig = np.concatenate([
        rng.uniform(0.0, 5.0, 500),
        rng.normal(120.0, 10.0, 250),
        rng.normal(700.0, 20.0, 250),
    ])
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        thr4, dec4 = resolve_train_on_thr_w(USER_KEY, {"on_thr_w": "auto"}, y_ambig)
    assert dec4["confidence"] == "low"
    assert any(
        "INTERMEDIATE_PLATEAU_SUSPECTED" in r
        or "MULTIMODAL_ON_GEAR_SUSPECTED" in r
        or "LOW_BIMODAL" in r
        for r in dec4["reasons"]
    )
    assert any("\033[91m" in r.message and "[AUTO-ON-THR-WARN]" in r.message for r in caplog.records)

    # (e) 全零死信号 / 24h 常开无停机信号 -> 红色告警日志 + 安全回退
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        thr_zero, dec_zero = resolve_train_on_thr_w(USER_KEY, {"on_thr_w": "auto"}, np.zeros(200))
        thr_on, dec_on = resolve_train_on_thr_w(USER_KEY, {"on_thr_w": "auto"}, np.full(200, 400.0))
    assert thr_zero == 10.0 and dec_zero["confidence"] == "low"
    assert thr_on <= 10.0 and dec_on["confidence"] == "low"
    red_warns = [r.message for r in caplog.records if "\033[91m" in r.message]
    assert len(red_warns) == 2

    # (f) 显式数值配置：100% 显式优先，不发 auto 日志
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        thr_exp, dec_exp = resolve_train_on_thr_w(USER_KEY, {"on_thr_w": 65.0}, y_standby)
    assert thr_exp == 65.0 and dec_exp["stage"] == "explicit"
    assert len(caplog.records) == 0


# ---------------------------------------------------------------- 3. decision_thr_w="auto" 三重护栏校准与黄/红日志
def test_resolve_auto_decision_thr_w_guardrails_and_logs(caplog):
    idx = pd.date_range("2026-03-01", periods=96 * 4, freq="15min")
    # 2 天开机日 + 2 天全关日（真值 0 或 700）
    y_val = np.zeros(len(idx), dtype=np.float64)
    y_val[32:72] = 700.0
    y_val[96 * 2 + 32 : 96 * 2 + 72] = 700.0
    y_train = y_val.copy()

    # (a) 全关日存在 18~42W 回归底噪虚报（类 815）：@10W 大量 FP，自动抬升至 >=45W 消除虚报 + 黄色日志
    rng = np.random.default_rng(7)
    pred_noisy = np.where(y_val >= 10.0, 680.0 + rng.normal(0, 10, len(idx)), rng.uniform(18.0, 42.0, len(idx)))
    dec1, info1 = resolve_auto_decision_thr_w(
        y_train=y_train, y_val=y_val, pred_val=pred_noisy, val_index=idx,
        on_thr_w=10.0, post_min_on=1, post_fill_off=3, calibrated_model="transformer",
    )
    assert 42.0 <= dec1 <= 80.0
    assert info1["val_f1_selected"] > info1["val_f1_at_on_thr"] + 0.15
    assert info1["val_recall_selected"] >= 0.95
    assert info1["val_off_fp_selected"] == 0
    assert info1["confidence"] == "high"

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        log_auto_decision_thr_train(USER_KEY, info1)
    assert any("\033[93m" in r.message and "[AUTO-DEC-THR]" in r.message for r in caplog.records)

    # (b) 护栏 ②（上限 <= 0.55 * P10(y_on) = 330W）与护栏 ①（recall >= 0.95）：绝不盲冲 500W 悬崖
    y_tr_600 = np.where(y_train >= 10.0, 600.0, 0.0)
    pred_high_fp = np.where(y_val >= 10.0, 580.0, 380.0)  # 关态虚报高达 380W > 330W 上限
    dec2, info2 = resolve_auto_decision_thr_w(
        y_train=y_tr_600, y_val=y_val, pred_val=pred_high_fp, val_index=idx,
        on_thr_w=10.0, post_min_on=1, post_fill_off=3, calibrated_model="ridge",
    )
    assert dec2 <= 330.0 + 1e-6
    assert info2["upper_guard_w"] == pytest.approx(330.0)
    assert info2["val_recall_selected"] >= 0.95

    # (c) 护栏 ③（最小增益门 < 0.003）：当基准 on_thr_w 已近满分，保持保守值 on_thr_w 不乱动
    pred_clean = np.where(y_val >= 10.0, 690.0, 1.0)
    dec3, info3 = resolve_auto_decision_thr_w(
        y_train=y_train, y_val=y_val, pred_val=pred_clean, val_index=idx,
        on_thr_w=10.0, post_min_on=1, post_fill_off=3, calibrated_model="transformer",
    )
    assert dec3 == 10.0
    assert "KEEP_ON_THR_NEAR_OPTIMAL" in info3["reasons"]

    # (d) 模型严重低估导致召回率 < 0.95 -> confidence=low + 红色告警日志
    pred_collapsed = np.where(y_val >= 10.0, 4.0, 1.0)
    dec4, info4 = resolve_auto_decision_thr_w(
        y_train=y_train, y_val=y_val, pred_val=pred_collapsed, val_index=idx,
        on_thr_w=10.0, post_min_on=1, post_fill_off=3, calibrated_model="lstm",
    )
    assert info4["confidence"] == "low"
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        log_auto_decision_thr_train(USER_KEY, info4)
    assert any("\033[91m" in r.message and "[AUTO-DEC-THR-WARN]" in r.message for r in caplog.records)


# ---------------------------------------------------------------- 4. 推理继承、显式覆盖冲突告警与离线跨期漂移告警
def test_infer_threshold_inheritance_priority_and_drift_alert(caplog):
    meta = {
        "on_thr_w": 50.0,
        "on_thr_w_configured": "auto",
        "decision_thr_w": 80.0,
        "model_decision_thr_w": {"transformer": 80.0, "ridge": 120.0},
    }

    # (a) auto 继承 meta（按所选模型精确匹配 ridge -> 120.0）+ 黄色提示日志
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        on_a, dec_a, info_a = resolve_infer_thresholds(
            USER_KEY,
            {"on_thr_w": "auto", "decision_thr_w": "auto",
             "_provenance": {"on_thr_w": USER_KEY, "decision_thr_w": "default(硬编码)"}},
            meta,
            model_name="ridge",
        )
    assert on_a == 50.0 and dec_a == 120.0
    assert info_a["decision_thr_w_source"] == "meta.model_decision_thr_w[ridge]"
    yellow_msgs = [r.message for r in caplog.records if "\033[93m" in r.message]
    assert len(yellow_msgs) == 2

    # (b) 显式优先：用户显式指定 decision_thr_w=35.0 覆盖 meta，且显式 on_thr_w=20.0 触发红字不一致提示
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        on_b, dec_b, _ = resolve_infer_thresholds(
            USER_KEY,
            {"on_thr_w": 20.0, "decision_thr_w": 35.0,
             "_provenance": {"on_thr_w": USER_KEY, "decision_thr_w": USER_KEY}},
            meta,
            model_name="transformer",
        )
    assert on_b == 20.0 and dec_b == 35.0
    assert any("\033[91m" in r.message and "[ON-THR-MISMATCH]" in r.message for r in caplog.records)

    # (c) 旧版历史 meta.json 向后兼容（无 decision_thr_w 字段时平滑回退至 on_thr_w）
    on_c, dec_c, info_c = resolve_infer_thresholds(
        USER_KEY,
        {"on_thr_w": 50.0, "decision_thr_w": "auto",
         "_provenance": {"on_thr_w": USER_KEY, "decision_thr_w": "default(硬编码)"}},
        {"on_thr_w": 50.0},
        model_name="ridge",
    )
    assert on_c == 50.0 and dec_c == 50.0
    assert info_c["decision_thr_w_source"] == "legacy_fallback_on_thr_w"

    # (d) 离线跨期基线漂移只读诊断：infer 关态抬升至 140W 导致 @50W 虚报暴增 -> 输出红色漂移告警
    idx_inf = pd.date_range("2026-07-01", periods=96 * 4, freq="15min")
    y_inf = np.zeros(len(idx_inf), dtype=np.float64)
    y_inf[32:72] = 700.0
    y_inf[96 * 2 + 32 : 96 * 2 + 72] = 700.0
    pred_drifted = np.where(y_inf >= 50.0, 680.0, 140.0)
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        advice = check_offline_decision_drift(
            user_key=USER_KEY,
            y_infer_true=y_inf,
            pred_infer=pred_drifted,
            infer_index=idx_inf,
            on_thr_w=50.0,
            current_dec_thr_w=50.0,
            post_min_on=1,
            post_fill_off=3,
            model_name="transformer",
        )
    assert advice is not None and advice["drift_detected"] is True
    assert advice["recommended_decision_thr_w"] >= 145.0
    assert any("\033[91m" in r.message and "[AUTO-DEC-THR-DRIFT]" in r.message for r in caplog.records)


# ---------------------------------------------------------------- 5. 端到端 train+infer 流水线与 audit_user_run 全量对账
def test_end_to_end_auto_thresholds_pipeline_and_audit(tmp_path: Path, base_cfg: dict, caplog):
    import yaml

    full_base = dict(base_cfg)
    full_base["metrics"] = [
        "mae", "rmse", "r2", "sae",
        "f1", "accuracy", "precision", "recall", "tp", "fp", "fn", "tn",
    ]
    base_cfg_full_file = tmp_path / "base_full.yaml"
    base_cfg_full_file.write_text(
        yaml.safe_dump(full_base, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    data_root = tmp_path / "data"
    out_root = tmp_path / "outputs"
    _write_bimodal_user_dir(data_root, USER_KEY, "trains", days=20, start="2026-01-01",
                            standby_w=28.0, working_w=680.0, seed=11)
    _write_bimodal_user_dir(data_root, USER_KEY, "infers", days=8, start="2026-02-01",
                            standby_w=28.0, working_w=680.0, seed=22)

    tf_file = tmp_path / "tf_auto.json"
    tf_file.write_text(json.dumps({
        USER_KEY: {
            "target_col": "p1",
            "on_thr_w": "auto",
            "decision_thr_w": "auto",
            "pbus_mode": "auto",
            "split_ratios": [0.6, 0.2, 0.2],
            "split_strategy": "stratified_day",
            "post_min_on": 1,
            "post_fill_short_off": 3,
        }
    }), encoding="utf-8")

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        info = run_batch(
            tf_file,
            base_config_path=base_cfg_full_file,
            data_root=data_root,
            output_root=out_root,
            stages=("train", "infer"),
            force=True,
        )
    status_df = pd.read_csv(info["status_csv"])
    assert (status_df["status"] == Status.OK).all(), status_df

    # 校验黄色提示日志在 train 与 infer 均已输出
    warn_msgs = [r.message for r in caplog.records]
    assert any("[AUTO-ON-THR]" in m and "\033[93m" in m for m in warn_msgs)
    assert any("[AUTO-DEC-THR]" in m and "\033[93m" in m for m in warn_msgs)
    assert any("[AUTO-ON-THR-INFER]" in m and "\033[93m" in m for m in warn_msgs)
    assert any("[AUTO-DEC-THR-INFER]" in m and "\033[93m" in m for m in warn_msgs)

    # 校验 train/meta.json 全程留痕
    tr_dir = latest_done_dir(out_root / USER_KEY / "train")
    inf_dir = latest_done_dir(out_root / USER_KEY / "infer")
    assert tr_dir is not None and inf_dir is not None
    tr_meta = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8"))
    inf_meta = json.loads((inf_dir / "meta.json").read_text(encoding="utf-8"))

    assert tr_meta["on_thr_w_configured"] == "auto"
    assert tr_meta["decision_thr_w_configured"] == "auto"
    assert 35.0 <= tr_meta["on_thr_w"] <= 60.0  # 跨过 28W 待机纹波
    assert isinstance(tr_meta["decision_thr_w"], float)
    assert "candidates" in tr_meta["decision_thr_w_decision"]
    assert set(tr_meta["model_decision_thr_w"].keys()) == {"history_profile", "proportional", "ridge"}

    # 校验推理产物与训练阈值严格一致
    inf_df = pd.read_csv(inf_dir / INFERENCE_RESULT_REL)
    assert float(inf_df["on_thr_w"].iloc[0]) == pytest.approx(tr_meta["on_thr_w"])
    chosen_model = inf_meta["model"]
    expected_dec = tr_meta["model_decision_thr_w"][chosen_model]
    assert float(inf_df["decision_thr_w"].iloc[0]) == pytest.approx(expected_dec)

    # 运行交付审计脚本 audit_user_run.py：T1~T4 & I1~I10 必须 0 FAIL 全通过
    rc = audit_main(["--run-root", str(out_root / USER_KEY)])
    assert rc == 0


# ---------------------------------------------------------------- 6. auto_run_steps2to5 CLI 与新用户初始化
def test_auto_run_cli_and_ensure_user_config_auto_thresholds(tmp_path: Path):
    tf_path = tmp_path / "time_filters.json"
    tf_path.write_text(json.dumps({"_default": {"pt_ratio": 1.0}}), encoding="utf-8")

    # 1) 新用户未传阈值 -> 自动写入 on_thr_w="auto", decision_thr_w="auto", pbus_mode="auto"
    out_tf = ensure_user_config(USER_KEY, tf_path, target_col="p2")
    assert out_tf == tf_path
    saved = json.loads(tf_path.read_text(encoding="utf-8"))
    assert saved[USER_KEY]["on_thr_w"] == "auto"
    assert saved[USER_KEY]["decision_thr_w"] == "auto"
    assert saved[USER_KEY]["pbus_mode"] == "auto"

    # 2) 已有用户通过 CLI 覆盖数值或 on_thr -> 生成 overlay，原文件保持 "auto" 不被篡改
    ov = ensure_user_config(USER_KEY, tf_path, on_thr=50.0, decision_thr=80.0)
    assert ov != tf_path and "overlay" in ov.name
    ov_cfg = json.loads(ov.read_text(encoding="utf-8"))
    assert ov_cfg[USER_KEY]["on_thr_w"] == 50.0
    assert ov_cfg[USER_KEY]["decision_thr_w"] == 80.0
    orig_cfg = json.loads(tf_path.read_text(encoding="utf-8"))
    assert orig_cfg[USER_KEY]["on_thr_w"] == "auto"
    assert orig_cfg[USER_KEY]["decision_thr_w"] == "auto"

    # 3) CLI 参数解析支持 --on-thr auto --decision-thr auto（skip-train/skip-infer 只做预检）
    rc = auto_run_main([
        "--user-key", USER_KEY,
        "--time-filter-config", str(tf_path),
        "--output-root", str(tmp_path / "out"),
        "--on-thr", "auto",
        "--decision-thr", "on_thr",
        "--stage", "train",
        "--skip-train",
    ])
    assert rc == 0


# ---------------------------------------------------------------- 7. pooled 通用链路 auto 阈值兼容与黄色日志
def test_pooled_train_and_infer_auto_thresholds(tmp_path: Path, base_cfg: dict, caplog):
    from nilm.data_io.discovery import scan_root
    from nilm.pipeline.pooled_task import run_pooled_infer, run_pooled_train

    uk2 = "800080270709_4206602981959"
    data_root = tmp_path / "data"
    out_root = tmp_path / "outputs"
    _write_bimodal_user_dir(data_root, USER_KEY, "trains", days=10, standby_w=26.0, working_w=650.0, seed=1)
    _write_bimodal_user_dir(data_root, uk2, "trains", days=10, standby_w=26.0, working_w=650.0, seed=2)
    _write_bimodal_user_dir(data_root, USER_KEY, "infers", days=5, standby_w=26.0, working_w=650.0, seed=3)

    tr_scans = {r.user_key: r for r in scan_root(data_root / "trains", "train") if r.ok}
    in_scans = {r.user_key: r for r in scan_root(data_root / "infers", "infer") if r.ok}
    u_cfgs = {
        uk: {"target_col": "p1", "on_thr_w": "auto", "decision_thr_w": "auto",
             "post_min_on": 1, "post_fill_short_off": 3}
        for uk in (USER_KEY, uk2)
    }

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        res_tr = run_pooled_train([USER_KEY, uk2], tr_scans, u_cfgs, base_cfg, out_root, target_col="p1")
        assert res_tr.status == Status.OK, res_tr.message
        res_in = run_pooled_infer(USER_KEY, in_scans[USER_KEY], u_cfgs[USER_KEY], base_cfg, out_root, target_col="p1")
        assert res_in.status == Status.OK, res_in.message

    assert any("\033[93m" in r.message and "[AUTO-ON-THR]" in r.message for r in caplog.records)
    assert any("\033[93m" in r.message and "[AUTO-DEC-THR-INFER]" in r.message for r in caplog.records)

