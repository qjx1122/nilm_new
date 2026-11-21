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
    calibrate_and_sync_joint_decision_thr,
    check_offline_decision_drift,
    log_auto_decision_thr_train,
    resolve_auto_decision_thr_w,
    resolve_auto_on_thr_w,
    resolve_infer_thresholds,
    resolve_joint_val_infer_decision_thr_w,
    resolve_train_on_thr_w,
    sync_decision_thr_artifacts,
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
    warmup_points: int = 0,
    warmup_w: float = 18.0,
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
            if warmup_points > 0:
                day_arr[32:32 + warmup_points] = warmup_w
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

    # (b) 护栏 ②（上限 <= 0.80 * P10(y_on) 兼容多路求和局部启停）：
    #     对 733 原型 P10(y_on)=555W -> 上限 444W，放行 400W 最优档，同时拦住 510W 断崖虚报
    y_tr_555 = np.where(y_train >= 10.0, 555.0, 0.0)
    pred_high_fp = np.where(y_val >= 10.0, 580.0, 510.0)  # 关态虚报高达 510W > 444W 上限
    dec2, info2 = resolve_auto_decision_thr_w(
        y_train=y_tr_555, y_val=y_val, pred_val=pred_high_fp, val_index=idx,
        on_thr_w=10.0, post_min_on=1, post_fill_off=3, calibrated_model="ridge",
    )
    assert dec2 <= 444.0 + 1e-6
    assert info2["upper_guard_w"] == pytest.approx(444.0)
    assert info2["val_recall_selected"] >= 0.95

    #     验证 733 原型关态虚报 360W 时，0.80 * P10(555W)=444W 能顺利放行 ~380-400W 档位消除全部虚报
    pred_733_like = np.where(y_val >= 10.0, 620.0, 360.0)
    dec_733, info_733 = resolve_auto_decision_thr_w(
        y_train=y_tr_555, y_val=y_val, pred_val=pred_733_like, val_index=idx,
        on_thr_w=60.0, post_min_on=1, post_fill_off=3, calibrated_model="transformer",
    )
    assert 360.0 < dec_733 <= 444.0
    assert info_733["val_off_fp_selected"] == 0

    # (b2) pbus_mode="scaled" 平台期抗漂移裕度：val 在 250~350W 同分满分时，scaled 自动向安全上沿偏移
    pred_plateau = np.where(y_val >= 10.0, 650.0, 240.0)
    dec_raw, _ = resolve_auto_decision_thr_w(
        y_train=y_tr_555, y_val=y_val, pred_val=pred_plateau, val_index=idx,
        on_thr_w=60.0, post_min_on=1, post_fill_off=3, calibrated_model="transformer",
        pbus_mode="raw",
    )
    dec_scaled, info_scaled = resolve_auto_decision_thr_w(
        y_train=y_tr_555, y_val=y_val, pred_val=pred_plateau, val_index=idx,
        on_thr_w=60.0, post_min_on=1, post_fill_off=3, calibrated_model="transformer",
        pbus_mode="scaled",
    )
    assert dec_scaled > dec_raw
    assert any("SCALED_ANTI_DRIFT_MARGIN" in r for r in info_scaled["reasons"])

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

    # (b) 显式优先：用户显式指定 decision_thr_w=35.0 覆盖 meta 触发黄字 [DEC-THR-OVERRIDE]，
    #     且显式 on_thr_w=20.0 触发红字 [ON-THR-MISMATCH]
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
    assert any("\033[93m" in r.message and "[DEC-THR-OVERRIDE]" in r.message for r in caplog.records)

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

    # (e) 高占空比场景漂移检测（类 733：@250W F1 已高达 0.97+，但存在 24 个 320W 盛夏漂移 FP；
    #     抬至 400W 砍掉 >80% FP 且 F1 提升 >=0.004）-> 同样触发红字 [AUTO-DEC-THR-DRIFT]
    idx_hd = pd.date_range("2026-08-01", periods=96 * 12, freq="15min")
    y_hd = np.full(len(idx_hd), 720.0, dtype=np.float64)
    # 留出 60 个真关态点，其中 24 个因盛夏基荷抬升预测为 330W
    y_hd[:60] = 0.0
    pred_hd = np.where(y_hd >= 60.0, 700.0, 20.0)
    pred_hd[:24] = 330.0
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        adv_hd = check_offline_decision_drift(
            user_key=USER_KEY,
            y_infer_true=y_hd,
            pred_infer=pred_hd,
            infer_index=idx_hd,
            on_thr_w=60.0,
            current_dec_thr_w=250.0,
            post_min_on=1,
            post_fill_off=3,
            model_name="transformer",
        )
    assert adv_hd is not None and adv_hd["drift_detected"] is True
    assert adv_hd["current_infer_chain_f1"] >= 0.95  # 旧门限漏网的高占空比高 F1 场景
    assert adv_hd["current_infer_fp"] >= 15 and adv_hd["recommended_infer_fp"] == 0
    assert adv_hd["recommended_decision_thr_w"] >= 335.0
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


# ---------------------------------------------------------------- 8. 双链 (val + infer) 联合收口、零重训同步器与 --save-config / --stage sync-thr
def test_joint_val_infer_calibration_and_zero_retrain_sync_closed_loop(tmp_path: Path, base_cfg: dict, caplog):
    """复现 733 原型跨季节漂移（train 期定低阈 -> infer 期基荷右移 -> 双链收口同步刷新双侧产物且 audit 全绿）。"""
    import yaml

    full_base = dict(base_cfg)
    full_base["metrics"] = [
        "mae", "rmse", "r2", "sae",
        "f1", "accuracy", "precision", "recall", "tp", "fp", "fn", "tn",
    ]
    base_file = tmp_path / "base_sync.yaml"
    base_file.write_text(yaml.safe_dump(full_base, allow_unicode=True, sort_keys=False), encoding="utf-8")

    data_root = tmp_path / "data"
    out_root = tmp_path / "outputs"
    _write_bimodal_user_dir(data_root, USER_KEY, "trains", days=16, start="2026-04-01",
                            standby_w=20.0, working_w=700.0, bg_bus_kw=200.0, seed=31)
    _write_bimodal_user_dir(data_root, USER_KEY, "infers", days=8, start="2026-08-01",
                            standby_w=20.0, working_w=700.0, bg_bus_kw=200.0, seed=32)

    tf_file = tmp_path / "time_filters.json"
    tf_file.write_text(json.dumps({
        USER_KEY: {
            "target_col": "p1",
            "on_thr_w": "auto",
            "decision_thr_w": "auto",
            "pbus_mode": "raw",
            "split_ratios": [0.6, 0.2, 0.2],
            "split_strategy": "stratified_day",
            "post_min_on": 1,
            "post_fill_short_off": 3,
        }
    }), encoding="utf-8")

    run_batch(
        tf_file,
        base_config_path=base_file,
        data_root=data_root,
        output_root=out_root,
        stages=("train", "infer"),
        force=True,
    )
    tr_dir = latest_done_dir(out_root / USER_KEY / "train")
    inf_dir = latest_done_dir(out_root / USER_KEY / "infer")
    assert tr_dir is not None and inf_dir is not None

    # 模拟 8 月盛夏基荷漂移：将 infer 关态预测抬升至 355W（验证 0.80*P10 放行 ~400W 且双链联合校准生效）
    inf_csv = inf_dir / INFERENCE_RESULT_REL
    inf_df = pd.read_csv(inf_csv)
    off_mask = inf_df["target_state"].to_numpy(dtype=int) == 0
    inf_df.loc[off_mask, "pred"] = 355.0
    inf_df.to_csv(inf_csv, index=False)

    # 1) 直接校验 resolve_joint_val_infer_decision_thr_w 只读计算（不碰 test 折）
    j_thr, j_info = resolve_joint_val_infer_decision_thr_w(tr_dir, inf_dir)
    assert j_thr is not None and 355.0 < j_thr <= j_info["upper_guard_w"]
    assert j_info["infer_after"]["fp"] == 0
    assert j_info["infer_after"]["recall"] >= 0.95
    assert j_info["val_after"]["recall"] >= 0.95

    # 2) 执行 calibrate_and_sync_joint_decision_thr 原子同步 + 红字告警 + audit 全绿
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        sync_res = calibrate_and_sync_joint_decision_thr(USER_KEY, tr_dir, inf_dir)

    assert sync_res is not None and sync_res["synced"] is True
    synced_thr = sync_res["resolved_decision_thr_w"]
    assert synced_thr == pytest.approx(j_thr)
    assert synced_thr > 355.0
    # 大跨度偏移触发红字 [AUTO-DEC-THR-SYNC]
    assert any("\033[91m" in r.message and "[AUTO-DEC-THR-SYNC]" in r.message for r in caplog.records)

    # 双侧 meta.json 与 CSV 的 decision_thr_w 完全一致，且保留 val 初值留痕
    tr_meta = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8"))
    inf_meta = json.loads((inf_dir / "meta.json").read_text(encoding="utf-8"))
    assert tr_meta["decision_thr_w"] == pytest.approx(synced_thr)
    assert inf_meta["decision_thr_w"] == pytest.approx(synced_thr)
    assert "decision_thr_w_val_initial" in tr_meta
    inf_after = pd.read_csv(inf_csv)
    assert float(inf_after["decision_thr_w"].iloc[0]) == pytest.approx(synced_thr)

    # 零重训同步后必须 100% 通过 audit_user_run（含 T1~T4, I1~I10 逐位重放与 sigmoid 恒等校验）
    assert audit_main(["--run-root", str(out_root / USER_KEY)]) == 0

    # 再次执行双链同步（阈值已收敛一致）-> 输出黄字 [AUTO-DEC-THR-SYNC]
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        sync_res_2 = calibrate_and_sync_joint_decision_thr(USER_KEY, tr_dir, inf_dir)
    assert sync_res_2 is not None and sync_res_2["resolved_decision_thr_w"] == pytest.approx(synced_thr)
    assert any("\033[93m" in r.message and "[AUTO-DEC-THR-SYNC]" in r.message for r in caplog.records)

    # 3) 测试无分路真值（纯盲推）场景：双链校准优雅跳过，保持原 train 阈值一致
    inf_blind = inf_after.copy()
    inf_blind["target"] = np.nan
    inf_blind["target_state"] = np.nan
    inf_blind.to_csv(inf_csv, index=False)
    blind_thr, blind_info = resolve_joint_val_infer_decision_thr_w(tr_dir, inf_dir)
    assert blind_thr is None and blind_info.get("skipped") == "NO_INFER_GROUND_TRUTH"
    # 恢复真值表继续测 CLI
    inf_after.to_csv(inf_csv, index=False)

    # 4) 测试 CLI --stage sync-thr --decision-thr 320 --save-config：零重训显式改阈 + 自动备份写回 time_filters.json
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        rc_cli = auto_run_main([
            "--user-key", USER_KEY,
            "--time-filter-config", str(tf_file),
            "--output-root", str(out_root),
            "--stage", "sync-thr",
            "--decision-thr", "320",
            "--save-config",
        ])
    assert rc_cli == 0
    assert any("\033[93m" in r.message and "[DEC-THR-SYNC]" in r.message for r in caplog.records)
    assert any("\033[93m" in r.message and "[AUTO-SAVE-CONFIG]" in r.message for r in caplog.records)

    saved_tf = json.loads(tf_file.read_text(encoding="utf-8"))
    assert saved_tf[USER_KEY]["decision_thr_w"] == pytest.approx(320.0)
    assert isinstance(saved_tf[USER_KEY]["on_thr_w"], float)
    assert any(p.name.startswith("time_filters.bak.auto") for p in tmp_path.iterdir())
    assert audit_main(["--run-root", str(out_root / USER_KEY)]) == 0


def test_2844_archetype_cross_season_heteropeak_override_and_upper_guard(
    tmp_path: Path, base_cfg: dict, caplog
):
    """回归 2844 原型：
    1) 历史季节 val 召回紧贴 0.95（>=30kW 时 val_recall < 0.95），仅放行非空窄缝 [6, 20]；
    2) 推理期盛夏基荷抬升产生高幅关态虚报（~465kW），且短期推理子集 P10(y_infer_on)=550kW
       （若不透传训练集 upper_guard_w=547.2kW，子集 0.80*P10=440kW 会漏扫 500kW）；
    3) 验证升级 ②：check_offline_decision_drift 透传 train_upper_guard_w 后准确扫到 ~500kW；
    4) 验证升级 ①：双链收口器检测到跨季双链异峰（val 卡在 20 vs infer 最优 500），
       自动解除旧季节 val 窄缝否决，输出红字 [AUTO-DEC-THR-SYNC]，将 train+infer 原子同步至 ~500kW，
       并 100% 通过 audit_user_run 对账门禁。
    """
    import yaml

    full_base = dict(base_cfg)
    full_base["metrics"] = [
        "mae", "rmse", "r2", "sae",
        "f1", "accuracy", "precision", "recall", "tp", "fp", "fn", "tn",
    ]
    base_file = tmp_path / "base_2844.yaml"
    base_file.write_text(yaml.safe_dump(full_base, allow_unicode=True, sort_keys=False), encoding="utf-8")

    data_root = tmp_path / "data"
    out_root = tmp_path / "outputs"
    # 在训练集注入每日早班前 3 个点（3/40=7.5% < 10%）的 18W 预热段：
    # - P10(y_train_on) 仍位于 ~680W 主峰（train_upper_guard_w = 0.80*P10 ≈ 544W > 500W）；
    # - 但模型在 val 折该 7.5% 预热点预测约 18W，使 thr>=25W 时 val_recall=0.925 < 0.95，完美复刻 2844 窄缝！
    _write_bimodal_user_dir(
        data_root, USER_KEY, "trains", days=16, start="2026-04-01",
        standby_w=3.0, working_w=700.0, bg_bus_kw=10.0, seed=71,
        warmup_points=3, warmup_w=18.0,
    )

    # 推理集使用 working_w=550W（自身 0.80*P10 ≈ 430W < 500W），检验必须依赖透传的 train_upper_guard_w
    _write_bimodal_user_dir(
        data_root, USER_KEY, "infers", days=8, start="2026-07-01",
        standby_w=3.0, working_w=550.0, bg_bus_kw=10.0, seed=72,
    )

    tf_file = tmp_path / "time_filters.json"
    tf_file.write_text(json.dumps({
        USER_KEY: {
            "target_col": "p1",
            "on_thr_w": 10.0,
            "decision_thr_w": "auto",
            "pbus_mode": "scaled",
            "split_ratios": [0.6, 0.2, 0.2],
            "split_strategy": "stratified_day",
            "post_min_on": 1,
            "post_fill_short_off": 3,
        }
    }), encoding="utf-8")

    run_batch(
        tf_file,
        base_config_path=base_file,
        data_root=data_root,
        output_root=out_root,
        stages=("train", "infer"),
        force=True,
    )
    tr_dir = latest_done_dir(out_root / USER_KEY / "train")
    inf_dir = latest_done_dir(out_root / USER_KEY / "infer")
    assert tr_dir is not None and inf_dir is not None

    # 校验 train/meta.json：训练单跑因 val 预热段召回约束停在低位（<=20W），但物理上限 >500W
    tr_meta_init = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8"))
    best_model = tr_meta_init["best_model"]
    train_guard = float(tr_meta_init["train_upper_guard_w"])
    val_init_thr = float(tr_meta_init["decision_thr_w"])
    assert train_guard > 500.0
    assert val_init_thr <= 20.0

    # 模拟 2844 7 月盛夏基荷抬升：关态出现大量 465W 虚报（高于推理子集自身 0.80*P10≈430W，低于训练物理上限 ~544W），开机预测 530W
    inf_csv = inf_dir / INFERENCE_RESULT_REL
    inf_df = pd.read_csv(inf_csv)
    inf_on_mask = inf_df["target_state"].to_numpy(dtype=int) == 1
    inf_off_idx = inf_df.index[~inf_on_mask].to_numpy()
    inf_df.loc[inf_on_mask, "pred"] = 530.0
    inf_df.loc[inf_off_idx, "pred"] = 5.0
    n_hot_fp = min(len(inf_off_idx), max(40, len(inf_off_idx) // 2))
    inf_df.loc[inf_off_idx[:n_hot_fp], "pred"] = 465.0
    inf_df.to_csv(inf_csv, index=False)
    sync_decision_thr_artifacts(USER_KEY, tr_dir, inf_dir, val_init_thr, reason="inject_summer_drift")

    # 验证升级 ②：check_offline_decision_drift 透传 train_guard (>500W) 时能突破推理子集 ~430W 上限扫到 >=470~500W
    inf_ts = pd.DatetimeIndex(pd.to_datetime(inf_df["timestamp"]))
    drift_sub_only = check_offline_decision_drift(
        user_key=USER_KEY,
        y_infer_true=inf_df["target"].to_numpy(dtype=float),
        pred_infer=inf_df["pred"].to_numpy(dtype=float),
        infer_index=inf_ts,
        on_thr_w=10.0,
        current_dec_thr_w=val_init_thr,
        post_min_on=1,
        post_fill_off=3,
        model_name=best_model,
        upper_guard_w=None,
    )
    drift_with_train_guard = check_offline_decision_drift(
        user_key=USER_KEY,
        y_infer_true=inf_df["target"].to_numpy(dtype=float),
        pred_infer=inf_df["pred"].to_numpy(dtype=float),
        infer_index=inf_ts,
        on_thr_w=10.0,
        current_dec_thr_w=val_init_thr,
        post_min_on=1,
        post_fill_off=3,
        model_name=best_model,
        upper_guard_w=train_guard,
    )
    assert drift_sub_only is not None and drift_sub_only["drift_detected"] is False
    assert (
        drift_with_train_guard is not None
        and drift_with_train_guard["drift_detected"] is True
        and drift_with_train_guard["recommended_decision_thr_w"] >= 470.0
    )

    # 验证升级 ①：双链收口器解除历史 val 窄缝（<=20W）一票否决，采用当期 infer 最优（~500W），红字告警并同步双侧
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        sync_res = calibrate_and_sync_joint_decision_thr(USER_KEY, tr_dir, inf_dir)

    assert sync_res is not None
    assert sync_res["synced"] is True
    assert sync_res["heteropeak_override"] is True
    assert sync_res["val_capped_candidate_w"] <= 20.0
    resolved_thr = sync_res["resolved_decision_thr_w"]
    assert 470.0 <= resolved_thr <= train_guard
    assert any("CROSS_SEASON_INFER_DOMINANT_OVERRIDE" in r for r in sync_res["reasons"])
    assert any(
        "\033[91m" in r.message and "[AUTO-DEC-THR-SYNC]" in r.message and "检测到跨季双链异峰" in r.message
        for r in caplog.records
    )

    # 双侧 train + infer 产物已原子同步为同一个 resolved_thr，保留训练期 val 初值留痕，且 audit 全绿
    tr_meta_final = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8"))
    inf_meta_final = json.loads((inf_dir / "meta.json").read_text(encoding="utf-8"))
    assert tr_meta_final["decision_thr_w"] == pytest.approx(resolved_thr)
    assert inf_meta_final["decision_thr_w"] == pytest.approx(resolved_thr)
    assert tr_meta_final["decision_thr_w_val_initial"] == pytest.approx(val_init_thr)
    assert audit_main(["--run-root", str(out_root / USER_KEY)]) == 0



