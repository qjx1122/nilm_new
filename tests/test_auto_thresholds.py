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
    resolve_auto_postprocess_and_decision_thr,
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


# ---------------------------------------------------------------- 10. 任务78：两阶段 post_min_on / post_fill_short_off 自动定参单元测试
def test_stage1_postprocess_physical_bounds_regimes_and_gap_isolation():
    """阶段一真值物理定界：长连续工业班次 vs 间歇/午休短停机负荷 vs 跨日断层隔离 vs 稀疏回退。"""
    from nilm.pipeline.auto_thresholds import resolve_postprocess_physical_bounds

    # 1) 长连续工业班次（每天连续开机 32 窗=8h，无日内短停机）：M_max 放开至 >=6，K_max >=4
    days = 10
    idx_cont = pd.date_range("2026-01-01 00:00:00", periods=days * 96, freq="15min")
    y_cont = np.zeros(days * 96, dtype=np.float64)
    for d in range(days):
        base = d * 96
        y_cont[base + 32: base + 64] = 800.0
    b_cont = resolve_postprocess_physical_bounds(y_cont, idx_cont, on_thr_w=50.0)
    assert b_cont["m_max"] >= 6
    assert b_cont["k_max"] >= 4
    assert b_cont["confidence"] == "high"

    # 2) 间歇循环 / 午休停机负荷（每天开机 6 窗 -> 真停机 2 窗=30min -> 再开机 6 窗）：
    #    自回放 GT_Bridge_Ratio / gt_gap_bridged_rate 必须将 K_max 收窄至 <=1（禁止桥接 2 窗真停机！）
    y_cyc = np.zeros(days * 96, dtype=np.float64)
    for d in range(days):
        base = d * 96
        y_cyc[base + 32: base + 38] = 600.0
        # base+38..base+39 为 2 窗真停机（0W）
        y_cyc[base + 40: base + 46] = 600.0
    b_cyc = resolve_postprocess_physical_bounds(y_cyc, idx_cont, on_thr_w=50.0)
    assert b_cyc["k_max"] <= 1
    assert 3 <= b_cyc["m_max"] <= 6
    assert any("PROTECT_SHORT_TRUE_OFF_CYCLES" in r for r in b_cyc["reasons"])

    # 3) stratified_day 跨日拼接断层防护：非相邻天各自末尾 2 窗与次块开头 2 窗为开态，
    #    不得跨断层合并成 4 窗伪完整游程，且块边界完整游程长度统计严格只在连续 15min 块内进行
    non_adj_days = ["2026-01-01", "2026-01-05", "2026-01-09", "2026-01-14", "2026-01-19", "2026-01-25"]
    ts_parts, y_parts = [], []
    for ds in non_adj_days:
        ts_d = pd.date_range(f"{ds} 00:00:00", periods=96, freq="15min")
        yd = np.zeros(96, dtype=np.float64)
        yd[0:2] = 500.0      # 块首截断游程 2 窗
        yd[40:44] = 500.0    # 块内完整游程 4 窗
        yd[94:96] = 500.0    # 块尾截断游程 2 窗
        ts_parts.append(ts_d)
        y_parts.append(yd)
    idx_gap = pd.DatetimeIndex(np.concatenate([t.to_numpy() for t in ts_parts]))
    y_gap = np.concatenate(y_parts)
    b_gap = resolve_postprocess_physical_bounds(y_gap, idx_gap, on_thr_w=50.0)
    assert b_gap["n_contiguous_blocks"] == len(non_adj_days)
    # 完整块内游程全为 4 窗，而块首尾 2 窗在自回放 enforce_min_on(m>=3) 时会造成 >0.5% 真开态损失，故 M_max 严格锁在 <=2
    assert b_gap["on_run_p10"] == pytest.approx(4.0)
    assert b_gap["m_max"] <= 2

    # 4) 开态游程极少（<3 段）时低置信保守回退 M_max=1, K_max=3
    y_sparse = np.zeros(192, dtype=np.float64)
    y_sparse[20:28] = 500.0
    b_sparse = resolve_postprocess_physical_bounds(
        y_sparse, pd.date_range("2026-01-01", periods=192, freq="15min"), on_thr_w=50.0
    )
    assert b_sparse["m_max"] == 1
    assert b_sparse["k_max"] == 3
    assert b_sparse["confidence"] == "low"


def test_stage2_postprocess_calibration_spike_filter_antibridge_and_decoupling():
    """阶段二协同校准：短尖峰优先提 post_min_on 而非盲抬阈值、全关日防桥接护栏、独立混合配置与惰性保持。"""
    from nilm.pipeline.auto_thresholds import (
        resolve_auto_postprocess_and_decision_thr,
        resolve_postprocess_physical_bounds,
    )

    idx_val = pd.date_range("2026-02-01 00:00:00", periods=6 * 96, freq="15min")
    y_tr = np.zeros(10 * 96, dtype=np.float64)
    idx_tr = pd.date_range("2026-01-01 00:00:00", periods=10 * 96, freq="15min")
    for d in range(10):
        if d % 2 == 0:
            y_tr[d * 96 + 30: d * 96 + 62] = 600.0  # 32 窗长班次
    bounds = resolve_postprocess_physical_bounds(y_tr, idx_tr, on_thr_w=50.0)
    assert bounds["m_max"] >= 4

    # 场景 A：3 个开机日（长班次，其中含一段仅 85W 的低功率开机段，故若把 decision_thr_w 抬高到 >85W 会损召回！）
    # + 3 个全关日（含多处持续 2 窗的 180W 短尖峰干扰）：必须靠自动提升 post_min_on (>=3) 滤除短尖峰且保住低阈值高召回！
    y_val = np.zeros(6 * 96, dtype=np.float64)
    p_val = np.full(6 * 96, 10.0, dtype=np.float64)
    for d in range(6):
        b = d * 96
        if d in (0, 2, 4):  # 开机日
            y_val[b + 30: b + 62] = 600.0
            p_val[b + 30: b + 40] = 85.0     # 轻载开机段（若抬阈值 >85W 会漏检 30/96 > 5% 违反 recall>=0.95！）
            p_val[b + 40: b + 62] = 590.0
        else:               # 全关日：多处 2 窗高幅短尖峰（180W）
            for sp in (12, 36, 60, 80):
                p_val[b + sp: b + sp + 2] = 180.0

    dec_a, mo_a, fo_a, _, pp_info_a = resolve_auto_postprocess_and_decision_thr(
        y_train=y_tr,
        y_val=y_val,
        pred_val=p_val,
        val_index=idx_val,
        on_thr_w=50.0,
        cfg_decision_thr_w="auto",
        cfg_post_min_on="auto",
        cfg_post_fill_off="auto",
        bounds=bounds,
        calibrated_model="patchtst",
        pbus_mode="raw",
    )
    assert mo_a >= 3
    assert dec_a <= 85.0
    assert pp_info_a["val_off_fp_after"] == 0
    assert pp_info_a["val_recall_after"] >= 0.95

    # 场景 B：全关日防桥接硬护栏 —— 全关日存在两处相距 2 窗的 4 窗噪声包（min_on 无法滤掉），
    # k>=2 会把中间 2 窗也桥接成 FP，导致 off_day_fp(c,m,k) > off_day_fp(c,m,0)，必须被硬护栏拦截使 fo_b <= 1！
    y_val_b = np.zeros(4 * 96, dtype=np.float64)
    p_val_b = np.full(4 * 96, 5.0, dtype=np.float64)
    idx_val_b = pd.date_range("2026-02-01 00:00:00", periods=4 * 96, freq="15min")
    for d in (0, 2):
        b = d * 96
        y_val_b[b + 30: b + 62] = 600.0
        p_val_b[b + 30: b + 40] = 75.0
        p_val_b[b + 40: b + 62] = 590.0
        # 开机日班次内部给 1 窗预测塌陷，使 k=1 有正增益而 k>=2 在全关日触发桥接惩罚
        p_val_b[b + 50] = 20.0
    for d in (1, 3):  # 全关日：两段 9 窗噪声（超 M_max=8）相距 2 窗，k>=2 会把中间 2 窗桥接成额外 4 个全关日 FP
        b = d * 96
        p_val_b[b + 20: b + 29] = 120.0
        p_val_b[b + 31: b + 40] = 120.0

    _, _, fo_b, _, _ = resolve_auto_postprocess_and_decision_thr(
        y_train=y_tr,
        y_val=y_val_b,
        pred_val=p_val_b,
        val_index=idx_val_b,
        on_thr_w=50.0,
        cfg_decision_thr_w="auto",
        cfg_post_min_on="auto",
        cfg_post_fill_off="auto",
        bounds=bounds,
        calibrated_model="patchtst",
        pbus_mode="raw",
    )
    assert fo_b <= 1

    # 场景 C：独立解耦配置（显式 post_min_on=2 + post_fill_short_off="auto"），显式维度严格锁死为 2
    _, mo_c, fo_c, _, _ = resolve_auto_postprocess_and_decision_thr(
        y_train=y_tr,
        y_val=y_val,
        pred_val=p_val,
        val_index=idx_val,
        on_thr_w=50.0,
        cfg_decision_thr_w="auto",
        cfg_post_min_on=2,
        cfg_post_fill_off="auto",
        bounds=bounds,
        calibrated_model="patchtst",
        pbus_mode="raw",
    )
    assert mo_c == 2
    assert isinstance(fo_c, int)

    # 场景 D：干净双峰无毛刺，奥卡姆惰性保持门锁定默认锚点 (1, min(3, K_max))
    p_clean = np.where(y_val >= 50.0, 595.0, 5.0)
    _, mo_d, fo_d, _, pp_info_d = resolve_auto_postprocess_and_decision_thr(
        y_train=y_tr,
        y_val=y_val,
        pred_val=p_clean,
        val_index=idx_val,
        on_thr_w=50.0,
        cfg_decision_thr_w="auto",
        cfg_post_min_on="auto",
        cfg_post_fill_off="auto",
        bounds=bounds,
        calibrated_model="patchtst",
        pbus_mode="raw",
    )
    assert mo_d == 1
    assert fo_d == min(3, bounds["k_max"])
    assert any("KEEP_POSTPROC_ANCHOR" in r for r in pp_info_d["reasons"])


# ---------------------------------------------------------------- 11. 任务78：端到端训练+推理继承+显式覆盖+双链收口+零参审计闭环
def test_end_to_end_auto_postprocess_train_infer_sync_and_audit(tmp_path: Path, base_cfg: dict, caplog):
    import yaml

    full_base = dict(base_cfg)
    full_base["metrics"] = [
        "mae", "rmse", "r2", "sae",
        "f1", "accuracy", "precision", "recall", "tp", "fp", "fn", "tn",
    ]
    base_cfg_full_file = tmp_path / "base_full_pp.yaml"
    base_cfg_full_file.write_text(
        yaml.safe_dump(full_base, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )

    data_root = tmp_path / "data"
    out_root = tmp_path / "outputs"
    _write_bimodal_user_dir(data_root, USER_KEY, "trains", days=20, start="2026-01-01",
                            standby_w=28.0, working_w=680.0, seed=31)
    _write_bimodal_user_dir(data_root, USER_KEY, "infers", days=8, start="2026-02-01",
                            standby_w=28.0, working_w=680.0, seed=32)

    # 1) auto_run_steps2to5 新用户模板默认写入 post_min_on="auto", post_fill_short_off="auto"
    tf_file = tmp_path / "tf_auto_pp.json"
    tf_file.write_text(json.dumps({"_default": {"pt_ratio": 1.0}}), encoding="utf-8")
    ensure_user_config(USER_KEY, tf_file, target_col="p1")
    cfg_init = json.loads(tf_file.read_text(encoding="utf-8"))
    assert cfg_init[USER_KEY]["post_min_on"] == "auto"
    assert cfg_init[USER_KEY]["post_fill_short_off"] == "auto"
    # 放宽训练/推理过滤时间窗以便匹配 2026-01 ~ 2026-02 合成数据
    cfg_init[USER_KEY]["train"] = {"include": [["2026-01-01", "2026-01-31"]]}
    cfg_init[USER_KEY]["infer"] = {"include": [["2026-02-01", "2026-02-28"]]}
    cfg_init[USER_KEY]["split_ratios"] = [0.6, 0.2, 0.2]
    cfg_init[USER_KEY]["split_strategy"] = "stratified_day"
    tf_file.write_text(json.dumps(cfg_init, ensure_ascii=False, indent=2), encoding="utf-8")

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

    tr_dir = latest_done_dir(out_root / USER_KEY / "train")
    inf_dir = latest_done_dir(out_root / USER_KEY / "infer")
    assert tr_dir is not None and inf_dir is not None

    tr_meta = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8"))
    inf_meta = json.loads((inf_dir / "meta.json").read_text(encoding="utf-8"))
    assert tr_meta["post_min_on_configured"] == "auto"
    assert tr_meta["post_fill_short_off_configured"] == "auto"
    assert isinstance(tr_meta["post_min_on"], int)
    assert isinstance(tr_meta["post_fill_short_off"], int)
    assert tr_meta["postprocess_bounds"] is not None
    # 零泄漏核查：阶段一统计总点数严格等于 train + val（绝不含 test 折！）
    expected_non_test = tr_meta["split_sizes"]["train"] + tr_meta["split_sizes"]["val"]
    assert tr_meta["postprocess_bounds"]["n_points"] == expected_non_test
    assert "model_postprocess_params" in tr_meta and len(tr_meta["model_postprocess_params"]) >= 1

    # 推理侧自动继承所选模型的游程参数，且零参调用 audit_user_run 100% 全绿
    chosen_m = inf_meta["model"]
    assert inf_meta["post_min_on"] == tr_meta["model_postprocess_params"][chosen_m]["post_min_on"]
    assert inf_meta["post_fill_short_off"] == tr_meta["model_postprocess_params"][chosen_m]["post_fill_short_off"]
    msgs = [r.message for r in caplog.records]
    assert any("[AUTO-POSTPROC]" in m for m in msgs)
    assert any("[AUTO-POSTPROC-INFER]" in m for m in msgs)
    assert audit_main(["--run-root", str(out_root / USER_KEY)]) == 0

    # 2) auto_run_steps2to5 --stage sync-thr --min-on 2 --fill-off 1 --save-config 零重训原子回刷 + 固化写回
    rc_sync = auto_run_main([
        "--user-key", USER_KEY,
        "--time-filter-config", str(tf_file),
        "--base-config", str(base_cfg_full_file),
        "--data-root", str(data_root),
        "--output-root", str(out_root),
        "--stage", "sync-thr",
        "--min-on", "2",
        "--fill-off", "1",
        "--save-config",
    ])
    assert rc_sync == 0
    tr_meta_s = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8"))
    inf_meta_s = json.loads((inf_dir / "meta.json").read_text(encoding="utf-8"))
    assert tr_meta_s["post_min_on"] == 2 and tr_meta_s["post_fill_short_off"] == 1
    assert inf_meta_s["post_min_on"] == 2 and inf_meta_s["post_fill_short_off"] == 1
    saved_tf = json.loads(tf_file.read_text(encoding="utf-8"))
    assert saved_tf[USER_KEY]["post_min_on"] == 2
    assert saved_tf[USER_KEY]["post_fill_short_off"] == 1
    # 零参调用 audit_user_run 自动读取同步后的 (2, 1) 并对账 T1-T4 & I1-I10 全部通过！
    assert audit_main(["--run-root", str(out_root / USER_KEY)]) == 0

    # 3) 显式覆盖推理游程参数（post_min_on=4, post_fill_short_off=0）应输出 [POSTPROC-OVERRIDE]
    saved_tf[USER_KEY]["post_min_on"] = 4
    saved_tf[USER_KEY]["post_fill_short_off"] = 0
    tf_file.write_text(json.dumps(saved_tf, ensure_ascii=False, indent=2), encoding="utf-8")
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        info2 = run_batch(
            tf_file,
            base_config_path=base_cfg_full_file,
            data_root=data_root,
            output_root=out_root,
            stages=("infer",),
            force=True,
        )
    assert (pd.read_csv(info2["status_csv"])["status"] == Status.OK).all()
    assert any("[POSTPROC-OVERRIDE]" in r.message for r in caplog.records)
    assert audit_main(["--run-root", str(out_root / USER_KEY)]) == 0


# ---------------------------------------------------------------- 12. 任务78：3D (val+infer) 双链自动收口游程参数同步验证
def test_joint_val_infer_3d_auto_postprocess_sync(tmp_path: Path, base_cfg: dict, caplog):
    """当 post_min_on='auto' 且推理期出现全关日 2 窗短尖峰时，双链 3D 联合收口自动上调 post_min_on 滤除尖峰并双侧原子同步。"""
    import yaml

    full_base = dict(base_cfg)
    full_base["metrics"] = [
        "mae", "rmse", "r2", "sae",
        "f1", "accuracy", "precision", "recall", "tp", "fp", "fn", "tn",
    ]
    base_file = tmp_path / "base_3d.yaml"
    base_file.write_text(yaml.safe_dump(full_base, allow_unicode=True, sort_keys=False), encoding="utf-8")

    data_root = tmp_path / "data"
    out_root = tmp_path / "outputs"
    _write_bimodal_user_dir(data_root, USER_KEY, "trains", days=20, start="2026-01-01",
                            standby_w=25.0, working_w=680.0, seed=41)
    _write_bimodal_user_dir(data_root, USER_KEY, "infers", days=8, start="2026-02-01",
                            standby_w=25.0, working_w=680.0, seed=42)

    tf_file = tmp_path / "tf_3d.json"
    tf_file.write_text(json.dumps({
        USER_KEY: {
            "target_col": "p1",
            "on_thr_w": 50.0,
            "decision_thr_w": "auto",
            "post_min_on": "auto",
            "post_fill_short_off": "auto",
            "pbus_mode": "raw",
            "split_ratios": [0.6, 0.2, 0.2],
            "split_strategy": "stratified_day",
        }
    }), encoding="utf-8")

    info = run_batch(
        tf_file,
        base_config_path=base_file,
        data_root=data_root,
        output_root=out_root,
        stages=("train", "infer"),
        force=True,
    )
    assert (pd.read_csv(info["status_csv"])["status"] == Status.OK).all()
    tr_dir = latest_done_dir(out_root / USER_KEY / "train")
    inf_dir = latest_done_dir(out_root / USER_KEY / "infer")
    assert tr_dir is not None and inf_dir is not None

    # 注入跨期双链场景：保证训练真值连续长班次（M_max>=4），同时在 infer 全关日注入多处 2 窗短尖峰，
    # 且 infer 开机班次含一段 75W 轻载期（使单纯抬高 decision_thr_w >75W 会损召回，必须靠 post_min_on>=3 滤掉尖峰！）
    tr_meta = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8"))
    tr_meta["postprocess_bounds"]["m_max"] = 6
    tr_meta["postprocess_bounds"]["k_max"] = 3
    (tr_dir / "meta.json").write_text(json.dumps(tr_meta, ensure_ascii=False, indent=2), encoding="utf-8")

    inf_csv = inf_dir / INFERENCE_RESULT_REL
    inf_df = pd.read_csv(inf_csv)
    inf_df["_day"] = inf_df["timestamp"].astype(str).str.slice(0, 10)
    days_sorted = sorted(inf_df["_day"].unique().tolist())
    # 前 2 天强制做成全关日并带 2 窗 220W 短尖峰；其余开机日把每段开态前 20% 置为 75W 轻载预测
    for di, ds in enumerate(days_sorted):
        mask_d = inf_df["_day"] == ds
        idxs = inf_df.index[mask_d].to_numpy()
        if di < 2:
            inf_df.loc[idxs, "target"] = 10.0
            inf_df.loc[idxs, "target_state"] = 0
            inf_df.loc[idxs, "pred"] = 8.0
            for sp in (10, 30, 50, 70):
                if sp + 2 <= len(idxs):
                    inf_df.loc[idxs[sp:sp + 2], "pred"] = 220.0
        else:
            on_sub = idxs[inf_df.loc[idxs, "target_state"] == 1]
            off_sub = idxs[inf_df.loc[idxs, "target_state"] == 0]
            inf_df.loc[off_sub, "pred"] = 8.0
            if len(on_sub) >= 10:
                n_light = max(2, len(on_sub) // 4)
                inf_df.loc[on_sub[:n_light], "pred"] = 75.0
                inf_df.loc[on_sub[n_light:], "pred"] = 660.0
    inf_df.drop(columns=["_day"], inplace=True)
    inf_df.to_csv(inf_csv, index=False)
    sync_decision_thr_artifacts(
        USER_KEY, tr_dir, inf_dir, float(tr_meta["decision_thr_w"]),
        reason="inject_infer_short_spikes", new_post_min_on=1, new_post_fill_short_off=3
    )

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        res_joint = calibrate_and_sync_joint_decision_thr(USER_KEY, tr_dir, inf_dir)

    assert res_joint is not None and res_joint["synced"] is True
    assert res_joint["resolved_post_min_on"] >= 3
    assert res_joint["resolved_decision_thr_w"] <= 75.0
    assert res_joint["infer_after"]["off_day_fp"] == 0
    assert res_joint["infer_after"]["recall"] >= 0.95

    tr_meta_after = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8"))
    inf_meta_after = json.loads((inf_dir / "meta.json").read_text(encoding="utf-8"))
    assert tr_meta_after["post_min_on"] == res_joint["resolved_post_min_on"]
    assert inf_meta_after["post_min_on"] == res_joint["resolved_post_min_on"]
    assert audit_main(["--run-root", str(out_root / USER_KEY)]) == 0


# ---------------------------------------------------------------- 14. 901 原型：三峰寄生待机簇跨越 + 尺度不变性校验
def test_trimodal_buried_standby_promoted_901_archetype(caplog):
    rng = np.random.default_rng(901)
    n = 96 * 20
    idx = pd.date_range("2026-05-01", periods=n, freq="15min")
    y = np.zeros(n, dtype=np.float64)
    # 模拟全开机日三峰分布（无全天关机休息日）：每天含短时彻底断电归零簇 (<2.5W, ~8%)、
    # 四路叠加待机簇 (70~130W, ~6%) 与主工作簇 (720~2050W, ~86%)，令一级对数 Otsu 落在 ~10W 首谷
    for d in range(20):
        s = d * 96
        y[s : s + 8] = rng.uniform(0.0, 2.2, 8)
        y[s + 8 : s + 14] = rng.uniform(70.0, 130.0, 6)
        y[s + 14 : s + 96] = rng.uniform(720.0, 2050.0, 82)
    y_ser = pd.Series(y, index=idx)

    # 模拟 PT*CT=12000 高倍率二次侧母线功率（单位 W）：背景基荷约 3.2W ± 0.035W（折算一次侧 ±420W 波动），
    # 故 90W 分路待机边沿（二次侧仅 0.0075W）完全淹没于母线底噪，而 1400W 主工作簇（二次侧 ~0.12W）清晰可辨
    bg_sec_w = 3.2 + 0.4 * np.sin(2 * np.pi * (np.arange(n) % 96) / 96.0) + rng.normal(0.0, 0.035, n)
    pbus_sec_w = pd.Series(bg_sec_w + y / 12000.0, index=idx)

    thr_sec, info_sec = resolve_auto_on_thr_w(y_ser, pbus_pool=pbus_sec_w)
    assert info_sec["stage"] == "auto_trimodal_standby_promoted"
    assert info_sec["confidence"] == "high"
    assert info_sec["first_valley_thr_w"] <= 25.0
    assert 140.0 <= thr_sec <= 260.0
    assert any("BURIED_STANDBY_CLUSTER_PROMOTED" in r for r in info_sec["reasons"])
    assert info_sec["bus_observability"]["snr_sub"] < 1.0
    assert info_sec["bus_observability"]["snr_thr2"] >= 1.0

    # 尺度不变性验证：将母线乘以 12.0（模拟一次侧 kW 口径），定阈与信噪比完全一致
    pbus_pri_kw = pbus_sec_w * 12.0
    thr_pri, info_pri = resolve_auto_on_thr_w(y_ser, pbus_pool=pbus_pri_kw)
    assert thr_pri == pytest.approx(thr_sec, rel=1e-6)
    assert info_pri["bus_observability"]["snr_sub"] == pytest.approx(
        info_sec["bus_observability"]["snr_sub"], rel=1e-5
    )

    # 训练日志验证：高亮黄字且包含三峰跨越提示
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        tr_thr, _ = resolve_train_on_thr_w(
            USER_KEY,
            {"on_thr_w": "auto", "_provenance": {"on_thr_w": USER_KEY}},
            y_ser,
            pbus_pool=pbus_sec_w,
        )
    assert tr_thr == thr_sec
    assert any(
        "\033[93m" in r.message and "三峰跨越待机底噪" in r.message
        for r in caplog.records
    )


# ---------------------------------------------------------------- 15. 真多档位设备保护：低速档在母线上清晰可辨时严禁误晋升
def test_trimodal_identifiable_low_gear_kept_low_thr_protection():
    rng = np.random.default_rng(2026)
    n = 96 * 16
    idx = pd.date_range("2026-06-01", periods=n, freq="15min")
    y = np.zeros(n, dtype=np.float64)
    # 模拟双速风机/水泵：0~25 关机 (<2W)，25~45 真低速档 (~180W)，45~85 高速档 (~1600W)，85~96 关机
    for d in range(16):
        s = d * 96
        y[s : s + 25] = rng.uniform(0.0, 2.0, 25)
        y[s + 25 : s + 45] = 180.0 + rng.normal(0.0, 6.0, 20)
        y[s + 45 : s + 85] = 1600.0 + rng.normal(0.0, 25.0, 40)
        y[s + 85 : s + 96] = rng.uniform(0.0, 2.0, 11)
    y_ser = pd.Series(y, index=idx)
    # 干净母线（背景抖动仅 ±12W），0 <-> 180W 低速档启停在母线上产生清晰 ~180W 阶跃（SNR >> 1.2）
    pbus_clean = pd.Series(500.0 + y + rng.normal(0.0, 12.0, n), index=idx)

    thr, info = resolve_auto_on_thr_w(y_ser, pbus_pool=pbus_clean)
    assert info["stage"] == "auto_bimodal"
    assert thr < 60.0
    assert any("IDENTIFIABLE_MULTIGEAR_KEPT_LOW_THR" in r for r in info["reasons"])
    assert info["bus_observability"]["snr_sub"] >= 1.2


# ---------------------------------------------------------------- 16. 全开机日（off_days=0）防 TN->0 常开坍缩、MSE 底噪跨越与双链防下坠
def test_all_active_days_anti_collapse_and_mse_floor_crossing(tmp_path: Path):
    rng = np.random.default_rng(9012)
    n_va = 96 * 6
    idx_va = pd.date_range("2026-06-01", periods=n_va, freq="15min")
    y_va = np.zeros(n_va, dtype=np.float64)
    # 每天 10 个停机点 (~90W 待机)、86 个开机点 (~1400W)，即 off_days=0、占空比 89.6%
    for d in range(6):
        s = d * 96
        y_va[s : s + 10] = rng.uniform(75.0, 125.0, 10)
        y_va[s + 10 : s + 96] = rng.uniform(750.0, 1950.0, 86)

    # 模型在真关态 (<130W) 输出 135~190W MSE 回归底噪，另有 ~25% 停机点带 230~265W 尖峰
    off_mask_va = y_va < 130.0
    p_va = np.where(off_mask_va, rng.uniform(135.0, 190.0, n_va), y_va * 0.96)
    off_idx_va = np.flatnonzero(off_mask_va)
    p_va[off_idx_va[::4]] = rng.uniform(230.0, 265.0, len(off_idx_va[::4]))

    # (A) 在 on_thr_w=130W 处（真停机点全部 <130W，共 60 个负样本，但模型底噪 >=135W 致 130W 处 TN=0 常开坍缩，F1 仍有 ~0.945）：
    #     1D 与 3D 校准器均须触发 ANTI_COLLAPSE_SPEC_GUARD 并跨越底噪上沿，严禁停留在 130W
    dec_low_base, info_low_base = resolve_auto_decision_thr_w(
        y_train=y_va,
        y_val=y_va,
        pred_val=p_va,
        val_index=idx_va,
        on_thr_w=130.0,
        post_min_on=1,
        post_fill_off=3,
        calibrated_model="transformer",
    )
    assert "ANTI_COLLAPSE_SPEC_GUARD" in info_low_base["reasons"]
    assert dec_low_base >= 195.0

    dec_3d_low, _, _, dec_info_3d_low, _ = resolve_auto_postprocess_and_decision_thr(
        y_train=y_va,
        y_val=y_va,
        pred_val=p_va,
        val_index=idx_va,
        on_thr_w=130.0,
        cfg_decision_thr_w="auto",
        cfg_post_min_on="auto",
        cfg_post_fill_off="auto",
        train_index=idx_va,
        calibrated_model="transformer",
    )
    assert dec_3d_low >= 195.0
    assert dec_info_3d_low["val_f1_selected"] > 0.985

    # (B) 当 on_thr_w=200W（在 200W 处仍有 >=15 个 230~265W 关态虚报，F1 差值 <0.015）：
    #     高分位加密候选 + 显著消 FP 放行门将阈值抬升至 ~270W+，大幅清除残留 FP
    dec_200, info_200 = resolve_auto_decision_thr_w(
        y_train=y_va,
        y_val=y_va,
        pred_val=p_va,
        val_index=idx_va,
        on_thr_w=200.0,
        post_min_on=1,
        post_fill_off=3,
        calibrated_model="transformer",
    )
    assert dec_200 >= 265.0
    sel_row_200 = min(info_200["candidates"], key=lambda r: abs(r["thr"] - dec_200))
    base_row_200 = min(info_200["candidates"], key=lambda r: abs(r["thr"] - 200.0))
    assert sel_row_200["fp"] <= 3 and base_row_200["fp"] >= 12

    # (C) 双链收口（1D 与 3D）：全开机日下严禁向下滑落造成 TN=0，而应向上收敛至无坍缩高特异区间
    n_inf = 96 * 8
    idx_inf = pd.date_range("2026-07-01", periods=n_inf, freq="15min")
    y_inf = np.zeros(n_inf, dtype=np.float64)
    for d in range(8):
        s = d * 96
        y_inf[s : s + 10] = rng.uniform(75.0, 125.0, 10)
        y_inf[s + 10 : s + 96] = rng.uniform(750.0, 1950.0, 86)
    off_mask_inf = y_inf < 200.0
    p_inf = np.where(off_mask_inf, rng.uniform(130.0, 195.0, n_inf), y_inf * 0.96)
    off_idx_inf = np.flatnonzero(off_mask_inf)
    p_inf[off_idx_inf[::5]] = rng.uniform(225.0, 268.0, len(off_idx_inf[::5]))

    tr_sim_dir = tmp_path / "sim_tr"
    inf_sim_dir = tmp_path / "sim_inf"
    (inf_sim_dir / INFERENCE_RESULT_REL).parent.mkdir(parents=True, exist_ok=True)
    (tr_sim_dir / "predictions").mkdir(parents=True, exist_ok=True)

    pd.DataFrame({
        "timestamp": idx_va.strftime("%Y-%m-%d %H:%M:%S"),
        "split": ["val"] * n_va,
        "target": y_va,
        "pred_transformer": p_va,
    }).to_csv(tr_sim_dir / "predictions" / "train_predictions.csv", index=False)

    pd.DataFrame({
        "timestamp": idx_inf.strftime("%Y-%m-%d %H:%M:%S"),
        "target": y_inf,
        "pred": p_inf,
        "on_thr_w": 200.0,
        "decision_thr_w": 200.0,
        "target_state": (y_inf >= 200.0).astype(int),
        "pred_state": (p_inf >= 200.0).astype(int),
    }).to_csv(inf_sim_dir / INFERENCE_RESULT_REL, index=False)

    for post_cfg in ("explicit", "auto"):
        tr_meta_sim = {
            "best_model": "transformer",
            "on_thr_w": 200.0,
            "decision_thr_w": 200.0,
            "decision_thr_w_configured": "auto",
            "post_min_on": 1,
            "post_fill_short_off": 3,
            "post_min_on_configured": "auto" if post_cfg == "auto" else 1,
            "post_fill_short_off_configured": "auto" if post_cfg == "auto" else 3,
            "postprocess_bounds": {"m_max": 4, "k_max": 3},
            "decision_thr_w_decision": {"y_on_p10": 820.0, "upper_guard_w": 656.0},
        }
        inf_meta_sim = {
            "model": "transformer",
            "on_thr_w": 200.0,
            "decision_thr_w": 200.0,
            "post_min_on": 1,
            "post_fill_short_off": 3,
            "post_min_on_configured": "auto" if post_cfg == "auto" else 1,
            "post_fill_short_off_configured": "auto" if post_cfg == "auto" else 3,
        }
        (tr_sim_dir / "meta.json").write_text(json.dumps(tr_meta_sim), encoding="utf-8")
        (inf_sim_dir / "meta.json").write_text(json.dumps(inf_meta_sim), encoding="utf-8")

        j_thr, j_info = resolve_joint_val_infer_decision_thr_w(
            tr_sim_dir, inf_sim_dir
        )
        assert j_thr is not None and j_thr >= 260.0
        assert j_info["infer_after"]["f1"] >= 0.99
        assert j_info["infer_after"]["fp"] <= 5
        assert j_info["infer_after"]["recall"] >= 0.98


# ---------------------------------------------------------------- 17. 端到端 901 原型全链路回归（四分路合并 + 高倍率母线 + 全开机日 + 审计零异常）
def test_end_to_end_901_trimodal_all_active_days_pipeline_and_audit(tmp_path: Path, base_cfg: dict):
    import yaml

    rng = np.random.default_rng(901901)
    user_901 = "800080270901_4200008005677"
    device, user = user_901.split("_")
    data_root = tmp_path / "data"
    out_root = tmp_path / "outputs"

    for mode_dir, start_dt, days in (("trains", "2026-05-01", 16), ("infers", "2026-05-17", 6)):
        d = data_root / mode_dir / user_901
        d.mkdir(parents=True, exist_ok=True)
        n15 = 96 * days
        idx15 = pd.date_range(start_dt, periods=n15, freq="15min")
        # 构造 p1+p2+p3+p4 四路空调分路：全开机日（无全天关机日），
        # 每日 00:00~01:45 短时断电归零 (<1.8)，01:45~03:30 四路各 22 待机合 ~88，03:30~24:00 主运行 ~1400
        total_br = np.zeros(n15, dtype=np.float64)
        for day_i in range(days):
            s = day_i * 96
            total_br[s : s + 7] = rng.uniform(0.0, 1.8, 7)
            total_br[s + 7 : s + 14] = 88.0 + rng.normal(0.0, 6.0, 7)
            total_br[s + 14 : s + 96] = 1400.0 + 180.0 * np.sin(np.linspace(0, np.pi, 82)) + rng.normal(0.0, 25.0, 82)
        total_br = np.clip(total_br, 0.0, None)

        branch_df = pd.DataFrame({
            "time": idx15.strftime("%Y-%m-%d %H:%M:%S"),
            "p1": np.round(total_br * 0.30, 3),
            "p2": np.round(total_br * 0.25, 3),
            "p3": np.round(total_br * 0.25, 3),
            "p4": np.round(total_br * 0.20, 3),
        })

        # 构造 PT*CT=12000（pt=120, ct=100）二次侧 5min 母线（W）：
        # apply_pt_ct 后一次侧 kW = p_sec_w * 12；令一次侧母线 = 背景 2800 ± 320 + total_br，
        # 使 ~88 待机完全淹没于母线波动而 ~1400 主运行簇清晰可辨！
        n5 = 288 * days
        idx5 = pd.date_range(start_dt, periods=n5, freq="5min")
        br_5m = np.repeat(total_br, 3)
        bg_5m = 2800.0 + 350.0 * np.sin(2 * np.pi * (np.arange(n5) % 288) / 288.0) + rng.normal(0.0, 190.0, n5)
        p_pri_units = np.clip(bg_5m + br_5m, 600.0, None)
        ptotal_sec_w = p_pri_units / 12.0
        pa = ptotal_sec_w / 3.0
        pb = ptotal_sec_w / 3.0
        pc = ptotal_sec_w - pa - pb
        ua = np.full(n5, 57.7) + rng.normal(0.0, 0.1, n5)
        ub = np.full(n5, 57.7) + rng.normal(0.0, 0.1, n5)
        uc = np.full(n5, 57.7) + rng.normal(0.0, 0.1, n5)
        pf = np.clip(0.93 + rng.normal(0.0, 0.01, n5), 0.75, 0.99)
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

    full_base = dict(base_cfg)
    full_base["metrics"] = [
        "mae", "rmse", "r2", "sae",
        "f1", "accuracy", "precision", "recall", "tp", "fp", "fn", "tn",
    ]
    base_file = tmp_path / "base.yaml"
    base_file.write_text(yaml.safe_dump(full_base, allow_unicode=True, sort_keys=False), encoding="utf-8")
    tf_file = tmp_path / "user_configs.json"
    tf_file.write_text(json.dumps({
        "_default": {"pt_ratio": 1.0, "ct_ratio": 1.0},
        user_901: {
            "target_col": "p1+p2+p3+p4",
            "pt_ratio": 120.0,
            "ct_ratio": 100.0,
            "on_thr_w": "auto",
            "decision_thr_w": "auto",
            "post_min_on": "auto",
            "post_fill_short_off": "auto",
            "pbus_mode": "auto",
            "split_ratios": [0.65, 0.20, 0.15],
            "split_strategy": "stratified_day",
        },
    }), encoding="utf-8")

    info = run_batch(
        tf_file,
        base_config_path=base_file,
        data_root=data_root,
        output_root=out_root,
        stages=("train", "infer"),
        force=True,
    )
    assert (pd.read_csv(info["status_csv"])["status"] == Status.OK).all()
    tr_dir = latest_done_dir(out_root / user_901 / "train")
    inf_dir = latest_done_dir(out_root / user_901 / "infer")
    assert tr_dir is not None and inf_dir is not None

    tr_meta = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8"))
    # 验证第一道防线：on_thr_w 自动跨越 ~88W 待机簇（P99≈100W）晋升至主工作簇下沿前走廊（115~380）
    assert tr_meta["on_thr_w_decision"]["stage"] == "auto_trimodal_standby_promoted"
    assert 115.0 <= float(tr_meta["on_thr_w"]) <= 380.0

    # 验证第二道防线：推理端绝不发生 pred_state 全 1 坍缩（TN > 0 且 F1 优异）
    calibrate_and_sync_joint_decision_thr(user_901, tr_dir, inf_dir)
    inf_res = pd.read_csv(inf_dir / INFERENCE_RESULT_REL)
    tot_neg = int((inf_res["target_state"] == 0).sum())
    tn = int(((inf_res["target_state"] == 0) & (inf_res["pred_state"] == 0)).sum())
    assert tot_neg >= 20
    assert tn >= int(0.60 * tot_neg)
    assert audit_main(["--run-root", str(out_root / user_901)]) == 0


# ---------------------------------------------------------------- 18. 实测 901 复现闭环：TARGET_EDGE_BURIED_IN_BUS + 四路合并窄走廊 + 13 天坏天剔除 + scaled 救援门
def test_901_buried_bus_4branch_corridor_and_13_bad_days_day_gate_refresh(
    tmp_path: Path, base_cfg: dict, caplog
):
    """精准复现 901 实测日志全部棘手条件：
    1) 总厂母线波动完全淹没全分路（pt=1, ct=1 二次侧微弱值，snr_thr2 < 1.0 触发 TARGET_EDGE_BURIED_IN_BUS）；
    2) 四路合并走廊“4 台待机 (~155W) vs 1 台单开轻载 (~470W)”曾令 0.35*P05(C2) < 1.15*P98(C01) 倒挂；
    3) 原始训练目录含 13 天缺测全零坏天（得分 <70 被 day_gate 剔除，剔除后 20 个双达标天全为开机日 off_days=0）；
    4) 验证 _accept_scaled_candidate 在 901 实测 R2/RMSE 显著改善（train R2 0.6079->0.9499, val R2 0.6059->0.6512,
       val RMSE 479.1->450.7）但 val MAE 持平时成功通过救援门，且不破坏 778 F1 退化拦截。
    """
    import yaml
    from nilm.pipeline.user_task import _accept_scaled_candidate

    # --- (A) 验证 _accept_scaled_candidate 对 901 实测日志指标的 R2+RMSE 救援与对 778 的严格拦截 ---
    bands_901 = {"transformer": {"train": 1500.0, "val": 1200.0}}
    raw_901 = {
        "transformer": {
            "train": {"r2": {"macro": 0.6079}, "mae": {"macro": 315.6675}, "rmse": {"macro": 424.7081}, "f1": {"macro": 0.8957}},
            "val": {"r2": {"macro": 0.6059}, "mae": {"macro": 390.0289}, "rmse": {"macro": 479.1151}, "f1": {"macro": 0.8131}},
        }
    }
    scaled_901 = {
        "transformer": {
            "train": {"r2": {"macro": 0.9499}, "mae": {"macro": 103.5899}, "rmse": {"macro": 151.8329}, "f1": {"macro": 0.9477}},
            "val": {"r2": {"macro": 0.6512}, "mae": {"macro": 389.9385}, "rmse": {"macro": 450.7100}, "f1": {"macro": 0.8131}},
        }
    }
    ok_901_sc, notes_901_sc = _accept_scaled_candidate(
        raw_901, bands_901, scaled_901, bands_901,
        on_thr=180.0, triggered_reasons=["transformer_UNDERFIT_VS_BASELINE"],
    )
    assert ok_901_sc is True
    assert any("VAL_R2_RMSE_RESCUE_PASS" in n for n in notes_901_sc)

    # 若 F1 退化超限（模拟 778 漂移），即使 R2/RMSE 改善也必须一票否决
    scaled_f1_drop = {
        "transformer": {
            "train": {"r2": {"macro": 0.95}, "mae": {"macro": 103.0}, "rmse": {"macro": 150.0}, "f1": {"macro": 0.95}},
            "val": {"r2": {"macro": 0.70}, "mae": {"macro": 389.9}, "rmse": {"macro": 440.0}, "f1": {"macro": 0.7800}},
        }
    }
    ok_drop, notes_drop = _accept_scaled_candidate(
        raw_901, bands_901, scaled_f1_drop, bands_901,
        on_thr=180.0, triggered_reasons=["transformer_UNDERFIT_VS_BASELINE"],
    )
    assert ok_drop is False and any("F1_DEGRADED" in n for n in notes_drop)

    # --- (A2) 独立验证“四路合并窄走廊”在三峰晋升态（24% 零点）与双峰态（8% 零点）下均稳定落入 [175W, 280W] ---
    rng_unit = np.random.default_rng(90104)
    pbus_buried = 150.0 + rng_unit.normal(0.0, 45.0, 2000)  # 完全淹没分路跳变的大厂母线
    y_tri_4br = np.concatenate([
        rng_unit.uniform(0.0, 1.5, 500),       # 25% 断电零点
        rng_unit.uniform(65.0, 158.0, 220),    # 11% 四路同时待机（P98≈155W）
        rng_unit.uniform(465.0, 515.0, 200),   # 10% 单台轻载（P05(C2)≈475W，旧版 0.35*475=166 < 1.15*155=178 倒挂！）
        rng_unit.uniform(1250.0, 2150.0, 1080),
    ])
    thr_tri, dec_tri = resolve_auto_on_thr_w(y_tri_4br, fallback_thr_w=10.0, pbus_pool=pbus_buried)
    assert dec_tri["stage"] == "auto_trimodal_standby_promoted"
    assert 175.0 <= thr_tri <= 280.0

    y_bi_4br = np.concatenate([
        rng_unit.uniform(0.0, 1.5, 160),       # 8% 断电零点（一级 Otsu 直接跨到次谷）
        rng_unit.uniform(65.0, 158.0, 340),    # 17% 四路待机
        rng_unit.uniform(465.0, 515.0, 200),   # 10% 单台轻载（旧版 0.30*480=144W 倒挂切穿待机 P99=154W！）
        rng_unit.uniform(1250.0, 2150.0, 1300),
    ])
    thr_bi, dec_bi = resolve_auto_on_thr_w(y_bi_4br, fallback_thr_w=10.0, pbus_pool=pbus_buried)
    assert 175.0 <= thr_bi <= 280.0

    # --- (B) 端到端验证：TARGET_EDGE_BURIED_IN_BUS + 13 天坏天被 day_gate 剔除后重刷新 + 四路窄走廊晋升 ---
    rng = np.random.default_rng(20261121)
    user_901 = "800080270901_4200008005677"
    device, user = user_901.split("_")
    data_root = tmp_path / "data"
    out_root = tmp_path / "outputs"

    # 训练 33 天（前 13 天为母线大面积缺测 + 异常停机/谷底毛刺坏天，后 20 天为双达标全开机日）
    for mode_dir, start_dt, days, n_bad_days in (
        ("trains", "2026-04-01", 33, 13),
        ("infers", "2026-05-05", 8, 0),
    ):
        d = data_root / mode_dir / user_901
        d.mkdir(parents=True, exist_ok=True)
        n15 = 96 * days
        idx15 = pd.date_range(start_dt, periods=n15, freq="15min")
        total_br = np.zeros(n15, dtype=np.float64)
        for day_i in range(days):
            s = day_i * 96
            if day_i < n_bad_days:
                # 13 天坏天：停机全 0W 夹杂通讯冻结残值 (180~360W)，填平 day_gate 前次谷令预估卡在 15W
                total_br[s : s + 64] = 0.0
                total_br[s + 64 : s + 96] = rng.uniform(180.0, 360.0, 32)
            else:
                # 合格全开机日（严格复刻 901 实测占比：24% 零点 + 10% 四路待机 + 10% 单台轻载 + 56% 多台满载）：
                total_br[s : s + 23] = rng.uniform(0.0, 1.8, 23)
                total_br[s + 23 : s + 33] = rng.uniform(65.0, 158.0, 10)
                total_br[s + 33 : s + 43] = rng.uniform(465.0, 515.0, 10)
                total_br[s + 43 : s + 96] = rng.uniform(1250.0, 2150.0, 53)

        branch_df = pd.DataFrame({
            "time": idx15.strftime("%Y-%m-%d %H:%M:%S"),
            "p1": np.round(total_br * 0.28, 3),
            "p2": np.round(total_br * 0.27, 3),
            "p3": np.round(total_br * 0.23, 3),
            "p4": np.round(total_br * 0.22, 3),
        })

        # 构造 pt=1, ct=1 二次侧微弱母线（约 140W ± 75W 二次侧 -> /1000 后 0.14 ± 0.075 kW），
        # 分路仅耦合 1/12000（~0.12W 二次侧），令主簇与待机簇在母线上全部满足 snr < 1.0 (TARGET_EDGE_BURIED_IN_BUS)！
        n5 = 288 * days
        idx5 = pd.date_range(start_dt, periods=n5, freq="5min")
        br_5m = np.repeat(total_br, 3)
        ptotal_sec_w = np.clip(
            145.0 + 45.0 * np.sin(2 * np.pi * (np.arange(n5) % 288) / 288.0)
            + rng.normal(0.0, 42.0, n5) + br_5m / 12000.0,
            25.0,
            None,
        )
        pa = ptotal_sec_w / 3.0
        pb = ptotal_sec_w / 3.0
        pc = ptotal_sec_w - pa - pb
        ua = np.full(n5, 57.7) + rng.normal(0.0, 0.1, n5)
        ub = np.full(n5, 57.7) + rng.normal(0.0, 0.1, n5)
        uc = np.full(n5, 57.7) + rng.normal(0.0, 0.1, n5)
        pf = np.clip(0.92 + rng.normal(0.0, 0.01, n5), 0.75, 0.99)
        bus_df = pd.DataFrame({
            "event_time": idx5.strftime("%Y-%m-%d %H:%M:%S"),
            "load_iden_data0": ua, "load_iden_data1": ub, "load_iden_data2": uc,
            "load_iden_data3": pa / (ua * pf), "load_iden_data4": pb / (ub * pf),
            "load_iden_data5": pc / (uc * pf),
            "load_iden_data6": pa, "load_iden_data7": pb, "load_iden_data8": pc,
            "load_iden_data9": pf, "load_iden_data10": pf, "load_iden_data11": pf,
        })
        if n_bad_days > 0:
            # 将前 13 天中每小时删除 45 分钟母线行（缺失率 75%），使逐天质量分 < 70 被 day_gate 精准剔除
            bad_5m_end = 288 * n_bad_days
            keep_mask = np.ones(n5, dtype=bool)
            for k in range(bad_5m_end):
                if (k % 12) >= 3:
                    keep_mask[k] = False
            bus_df = bus_df.loc[keep_mask].reset_index(drop=True)

        s_code, e_code = idx5[0].strftime("%y%m%d"), idx5[-1].strftime("%y%m%d")
        bus_df.to_csv(d / f"e241_{device}_{user}-Ch1-{s_code}-{e_code}.csv", index=False)
        branch_df.to_csv(d / f"{user}-{s_code}-{e_code}.csv", index=False)

    full_base = dict(base_cfg)
    full_base["quality"] = {
        "max_missing_rate": 0.9,
        "min_coverage": 0.15,
        "min_score": 70,
        "gate_scope": "full",
        "day_gate": True,
        "min_days": 3,
        "max_daily_missing_rate": 0.95,
    }
    full_base["metrics"] = [
        "mae", "rmse", "r2", "sae",
        "f1", "accuracy", "precision", "recall", "tp", "fp", "fn", "tn",
    ]
    base_file = tmp_path / "base_901_real.yaml"
    base_file.write_text(yaml.safe_dump(full_base, allow_unicode=True, sort_keys=False), encoding="utf-8")

    tf_file = tmp_path / "tf_901_real.json"
    tf_file.write_text(json.dumps({
        "_default": {"pt_ratio": 1.0, "ct_ratio": 1.0},
        user_901: {
            "target_col": "p1+p2+p3+p4",
            "pt_ratio": 1.0,
            "ct_ratio": 1.0,
            "on_thr_w": "auto",
            "decision_thr_w": "auto",
            "post_min_on": "auto",
            "post_fill_short_off": "auto",
            "pbus_mode": "auto",
            "split_ratios": [0.60, 0.20, 0.20],
            "split_strategy": "stratified_day",
        },
    }), encoding="utf-8")

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="nilm"):
        info = run_batch(
            tf_file,
            base_config_path=base_file,
            data_root=data_root,
            output_root=out_root,
            stages=("train", "infer"),
            force=True,
        )
    assert (pd.read_csv(info["status_csv"])["status"] == Status.OK).all()

    tr_dir = latest_done_dir(out_root / user_901 / "train")
    inf_dir = latest_done_dir(out_root / user_901 / "infer")
    assert tr_dir is not None and inf_dir is not None

    # 1) 确认真实触发了 TARGET_EDGE_BURIED_IN_BUS 且 day_gate 剔除了 13 天坏天
    ident_rep = json.loads((tr_dir / "identifiability_report.json").read_text(encoding="utf-8"))
    assert "TARGET_EDGE_BURIED_IN_BUS" in ident_rep.get("risk", [])
    dg_rep = json.loads((tr_dir / "day_gate.json").read_text(encoding="utf-8"))
    assert len(dg_rep["excluded_unqualified_days"]) == 13
    assert dg_rep["off_days"] == 0 and dg_rep["on_days"] == 20

    # 2) 确认 on_thr_w 在 day_gate 后完成重校准，且成功跨越 ~158W 待机簇晋升至 [175W, 300W]（绝不再卡死 15W！）
    tr_meta = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8"))
    on_dec = tr_meta["on_thr_w_decision"]
    assert on_dec.get("refreshed_after_day_filter") is True
    assert on_dec.get("excluded_unqualified_day_count") == 13
    assert on_dec["stage"] == "auto_trimodal_standby_promoted"
    assert 175.0 <= float(tr_meta["on_thr_w"]) <= 300.0

    # 3) 确认物理护栏上限 upper_guard_w 彻底解锁（不再被待机拉低至 96W，而是 >=360W）
    assert float(tr_meta["decision_thr_w_decision"]["upper_guard_w"]) >= 360.0

    # 4) 双链收口与审计全绿通过
    calibrate_and_sync_joint_decision_thr(user_901, tr_dir, inf_dir)
    assert audit_main(["--run-root", str(out_root / user_901)]) == 0


# ---------------------------------------------------------------- 21. 显式 auto 定参写回门禁契约（须同时传 --force + --save-config）
def test_auto_run_explicit_auto_requires_force_and_save_config_to_update_orig_json(
    tmp_path: Path, base_cfg: dict, caplog
):
    """验证 auto_run_steps2to5 显式传 auto 选项时：
    1) 仅传 --force（未传 --save-config）-> 原 time_filters.json 固定值保持不变；
    2) 仅传 --save-config（未传 --force）-> 原 time_filters.json 固定值保持不变，输出 [AUTO-SAVE-SKIPPED] 告警；
    3) 同时显式传 --force + --save-config（--stage train，且磁盘存在陈旧 infer 产物干扰）->
       不被陈旧 infer 污染，5 项参数（含 pbus_mode 决议值）同步更新至原 time_filters.json，清理临时 overlay；
       随后单独执行 run_batch --stage infer 读原 time_filters.json，训推 5 项参数 100% 对齐无 OVERRIDE 告警；
    4) 同时显式传 --force + --save-config（--stage all）-> 原 JSON 固定值同步更新为双链新自动值。
    """
    import yaml

    full_base = dict(base_cfg)
    full_base["metrics"] = [
        "mae", "rmse", "r2", "sae",
        "f1", "accuracy", "precision", "recall", "tp", "fp", "fn", "tn",
    ]
    base_file = tmp_path / "base_force_save.yaml"
    base_file.write_text(yaml.safe_dump(full_base, allow_unicode=True, sort_keys=False), encoding="utf-8")

    data_root = tmp_path / "data"
    out_root = tmp_path / "outputs"
    _write_bimodal_user_dir(
        data_root, USER_KEY, "trains", days=16, start="2026-04-01",
        standby_w=20.0, working_w=700.0, bg_bus_kw=200.0, seed=101,
    )
    _write_bimodal_user_dir(
        data_root, USER_KEY, "infers", days=8, start="2026-07-01",
        standby_w=20.0, working_w=700.0, bg_bus_kw=200.0, seed=102,
    )

    fixed_orig = {
        "target_col": "p1",
        "on_thr_w": 85.0,
        "decision_thr_w": 55.0,
        "post_min_on": 6,
        "post_fill_short_off": 5,
        "pbus_mode": "scaled",
        "split_ratios": [0.6, 0.2, 0.2],
        "split_strategy": "stratified_day",
    }
    tf_file = tmp_path / "time_filters.json"
    tf_file.write_text(json.dumps({USER_KEY: dict(fixed_orig)}, indent=2), encoding="utf-8")

    # 先按旧固定值跑一次 train + infer，在磁盘制造陈旧 _DONE 产物（模拟老户 2842 已有旧 infer 目录）
    run_batch(
        tf_file,
        base_config_path=base_file,
        data_root=data_root,
        output_root=out_root,
        stages=("train", "infer"),
        force=True,
    )
    stale_inf_dir = latest_done_dir(out_root / USER_KEY / "infer")
    assert stale_inf_dir is not None

    auto_args_base = [
        "--user-key", USER_KEY,
        "--time-filter-config", str(tf_file),
        "--base-config", str(base_file),
        "--data-root", str(data_root),
        "--output-root", str(out_root),
        "--target-col", "p1",
        "--on-thr", "auto",
        "--decision-thr", "auto",
        "--min-on", "auto",
        "--fill-off", "auto",
        "--pbus-mode", "auto",
    ]

    # 1) 显式 auto + 仅 --force（无 --save-config）-> 原 JSON 不变
    rc1 = auto_run_main(auto_args_base + ["--stage", "train", "--force"])
    assert rc1 == 0
    assert json.loads(tf_file.read_text(encoding="utf-8"))[USER_KEY] == fixed_orig

    # 2) 显式 auto + 仅 --save-config（无 --force）-> 原 JSON 不变，且输出 [AUTO-SAVE-SKIPPED]
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        rc2 = auto_run_main(auto_args_base + ["--stage", "train", "--save-config"])
    assert rc2 == 0
    assert json.loads(tf_file.read_text(encoding="utf-8"))[USER_KEY] == fixed_orig
    assert any("[AUTO-SAVE-SKIPPED]" in r.message for r in caplog.records)

    # 3) 显式 auto + --stage train + --force + --save-config -> 5 项新自动值同步更新至原 JSON（不被陈旧 infer 污染）
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        rc3 = auto_run_main(auto_args_base + ["--stage", "train", "--force", "--save-config"])
    assert rc3 == 0
    assert any("[AUTO-SAVE-CONFIG]" in r.message for r in caplog.records)
    overlay_path = tmp_path / f"time_filters.overlay.{USER_KEY}.json"
    assert not overlay_path.exists()

    tr_dir_new = latest_done_dir(out_root / USER_KEY / "train")
    tr_meta_new = json.loads((tr_dir_new / "meta.json").read_text(encoding="utf-8"))
    saved_after_train = json.loads(tf_file.read_text(encoding="utf-8"))[USER_KEY]
    assert saved_after_train["on_thr_w"] == pytest.approx(float(tr_meta_new["on_thr_w"]))
    assert saved_after_train["on_thr_w"] != pytest.approx(85.0)
    assert saved_after_train["decision_thr_w"] == pytest.approx(float(tr_meta_new["decision_thr_w"]))
    assert saved_after_train["post_min_on"] == int(tr_meta_new["post_min_on"])
    assert saved_after_train["post_min_on"] != 6
    assert saved_after_train["post_fill_short_off"] == int(tr_meta_new["post_fill_short_off"])
    assert saved_after_train["pbus_mode"] in ("raw", "scaled")
    assert saved_after_train["pbus_mode"] == tr_meta_new["pbus_mode"]

    # 紧接着单独执行 run_batch --stage infer --force 读原 time_filters.json：训推参数 100% 对齐，零 OVERRIDE 告警
    # 若 best_model 与校准模型不同，锁定校准模型以验证阈值/游程对齐
    cal_model = (tr_meta_new.get("decision_thr_w_decision") or {}).get("calibrated_model")
    if cal_model:
        cfg_tmp = json.loads(tf_file.read_text(encoding="utf-8"))
        cfg_tmp[USER_KEY]["infer_model"] = cal_model
        tf_file.write_text(json.dumps(cfg_tmp, indent=2), encoding="utf-8")

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="nilm"):
        inf_res = run_batch(
            tf_file,
            base_config_path=base_file,
            data_root=data_root,
            output_root=out_root,
            stages=("infer",),
            force=True,
        )
    assert (pd.read_csv(inf_res["status_csv"])["status"] == Status.OK).all()
    warn_msgs = [r.message for r in caplog.records]
    assert not any("[DEC-THR-OVERRIDE]" in m for m in warn_msgs)
    assert not any("[POSTPROC-OVERRIDE]" in m for m in warn_msgs)
    assert not any("[ON-THR-MISMATCH]" in m for m in warn_msgs)
    inf_dir_new = latest_done_dir(out_root / USER_KEY / "infer")
    inf_meta_new = json.loads((inf_dir_new / "meta.json").read_text(encoding="utf-8"))
    assert inf_meta_new["on_thr_w"] == pytest.approx(float(tr_meta_new["on_thr_w"]))
    assert inf_meta_new["decision_thr_w"] == pytest.approx(float(tr_meta_new["decision_thr_w"]))
    assert inf_meta_new["post_min_on"] == int(tr_meta_new["post_min_on"])
    assert inf_meta_new["post_fill_short_off"] == int(tr_meta_new["post_fill_short_off"])
    assert audit_main(["--run-root", str(out_root / USER_KEY)]) == 0

    # 4) 将原 JSON 再次重置为旧固定值，验证 --stage all --force --save-config 同样完整更新 5 项新自动值
    tf_file.write_text(json.dumps({USER_KEY: dict(fixed_orig)}, indent=2), encoding="utf-8")
    rc4 = auto_run_main(auto_args_base + ["--stage", "all", "--force", "--save-config"])
    assert rc4 == 0
    saved_after_all = json.loads(tf_file.read_text(encoding="utf-8"))[USER_KEY]
    tr_dir_all = latest_done_dir(out_root / USER_KEY / "train")
    inf_dir_all = latest_done_dir(out_root / USER_KEY / "infer")
    tr_meta_all = json.loads((tr_dir_all / "meta.json").read_text(encoding="utf-8"))
    inf_meta_all = json.loads((inf_dir_all / "meta.json").read_text(encoding="utf-8"))
    assert saved_after_all["on_thr_w"] == pytest.approx(float(tr_meta_all["on_thr_w"]))
    assert saved_after_all["decision_thr_w"] == pytest.approx(float(inf_meta_all["decision_thr_w"]))
    assert saved_after_all["post_min_on"] == int(inf_meta_all["post_min_on"])
    assert saved_after_all["post_fill_short_off"] == int(inf_meta_all["post_fill_short_off"])
    assert saved_after_all["pbus_mode"] in ("raw", "scaled")
    assert saved_after_all["pbus_mode"] == tr_meta_all["pbus_mode"]
    assert audit_main(["--run-root", str(out_root / USER_KEY)]) == 0
