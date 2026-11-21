#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
一键执行 TUNING_GUIDE Step 2~5（训练 → 窗口自检 → 推理 → 阈值扫描）

来源：TUNING_GUIDE.md v1.1 §3 Step 2-5（已对齐 base_optimal lag5[5,1,2,3,4] + 4模型择优）
新增：适配新用户 800080270733_4206673297219（及任意 --user-key），兼容 Windows PowerShell（* 通配由脚本内部展开，已修复 threshold_sweep May-2026-09-19）

用法（仓库根目录）：
  # 新用户 733 一键（默认自动补 time_filters.json 缺省配置 target p1/on10/decision30/day_gate）
  python scripts/auto_run_steps2to5.py --user-key 800080270733_4206673297219
  # 覆盖目标/阈值（脚本参数优先于 time_filters.json：已传且与配置不同则用临时 overlay 跑，不写回原文件）
  python scripts/auto_run_steps2to5.py --user-key 800080270733_4206673297219 --target-col p2 --on-thr 10 --decision-thr 30
  # 未传的参数沿用 time_filters.json 现有值；新用户未传则用默认 p1/10/30（同样走 overlay，原文件不动）
  # 仅推理/仅训练
  python scripts/auto_run_steps2to5.py --user-key 800080270733_4206673297219 --stage infer
  # 强制重跑
  python scripts/auto_run_steps2to5.py --user-key 800080270733_4206673297219 --force

Windows PowerShell (test_gpu)：
  conda activate test_gpu
  python scripts/auto_run_steps2to5.py --user-key 800080270733_4206673297219

产物：outputs/<user>/train/<ts>/{metrics_by_split,chain,train_predictions,branch_sessions,train_window_index,_DONE}
      outputs/<user>/infer/<ts>/{inference_result,offline_metrics,metrics_daily_chain}
"""
from __future__ import annotations
import argparse, json, logging, subprocess, sys, glob
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_TF = ROOT / "configs/time_filters.json"
DEFAULT_BASE = ROOT / "configs/base_optimal.yaml"

def log(s, tag="INFO"):
    print(f"[{tag}] {s}")

def sh(cmd, **kw):
    log(" ".join(cmd) if isinstance(cmd, list) else cmd, "RUN")
    rc = subprocess.run(cmd, shell=False, **kw)
    if rc.returncode != 0:
        log(f"命令失败 exit={rc.returncode}: {' '.join(cmd) if isinstance(cmd,list) else cmd}", "FAIL")
        raise SystemExit(rc.returncode)
    return rc

def _write_overlay(tf_path: Path, cfg: dict, user_key: str) -> Path:
    """把覆盖后的配置写入临时 overlay 文件（已有用户覆盖时用，原文件不动）。"""
    overlay = tf_path.parent / f"{tf_path.stem}.overlay.{user_key}.json"
    overlay.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return overlay

def _write_cfg_with_backup(tf_path: Path, cfg: dict) -> Path:
    """新用户补配置时备份原文件后写回（Windows 下 rename 目标已存在会 FileExistsError(183)，用时间戳+replace 兜底）"""
    bak = tf_path.with_suffix(".bak.auto")
    if bak.exists():
        from datetime import datetime
        bak = tf_path.with_suffix(f".bak.auto.{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        if bak.exists():
            bak.unlink()
    try:
        tf_path.rename(bak)
    except FileExistsError:
        tf_path.replace(bak)
    tf_path.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return bak

def _parse_on_thr_arg(val: str):
    norm = val.strip().lower()
    if norm == "auto":
        return "auto"
    try:
        return float(val)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"--on-thr 须为正数或 'auto': {val!r}") from e


def _parse_decision_thr_arg(val: str):
    norm = val.strip().lower()
    if norm in ("auto", "on_thr"):
        return norm
    try:
        return float(val)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"--decision-thr 须为正数、'auto' 或 'on_thr': {val!r}") from e


def _parse_postproc_arg(val: str):
    norm = val.strip().lower()
    if norm == "auto":
        return "auto"
    try:
        iv = int(val)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"游程参数须为 0..96 整数或 'auto': {val!r}") from e
    if iv < 0 or iv > 96:
        raise argparse.ArgumentTypeError(f"游程参数须在 0..96 范围内: {iv}")
    return iv


def ensure_user_config(user_key: str, tf_path: Path, target_col=None, on_thr=None,
                       decision_thr=None, pbus_mode=None, min_on=None, fill_off=None) -> Path:
    """已有用户：脚本参数优先但不写回（overlay）；新用户：补原文件（带备份）。

    返回本次运行实际使用的配置路径：已有用户无覆盖返回原文件，有覆盖返回
    overlay；新用户写回原文件后返回原文件。target_col/on_thr/decision_thr/pbus_mode/
    min_on/fill_off 为 None 表示未传，沿用配置值；新用户未传则默认 p1 / auto / auto / auto / auto / auto。
    """
    cfg = json.loads(tf_path.read_text(encoding="utf-8"))
    if user_key in cfg:
        cur = cfg[user_key]
        updates = {}
        if target_col is not None and cur.get("target_col") != target_col:
            updates["target_col"] = target_col
        if on_thr is not None and cur.get("on_thr_w") != on_thr:
            updates["on_thr_w"] = on_thr
        if decision_thr is not None and cur.get("decision_thr_w") != decision_thr:
            updates["decision_thr_w"] = decision_thr
        eff_cur_pbus = cur.get("pbus_mode") or "auto"
        if pbus_mode is not None and eff_cur_pbus != pbus_mode:
            updates["pbus_mode"] = pbus_mode
        if min_on is not None and cur.get("post_min_on") != min_on:
            updates["post_min_on"] = min_on
        if fill_off is not None and cur.get("post_fill_short_off") != fill_off:
            updates["post_fill_short_off"] = fill_off
        if not updates:
            log(f"time_filters.json 已含 {user_key} → 沿用（target={cur.get('target_col')} on={cur.get('on_thr_w')} "
                f"dec={cur.get('decision_thr_w')} pbus_mode={eff_cur_pbus} "
                f"min_on={cur.get('post_min_on', 1)} fill_off={cur.get('post_fill_short_off', 3)})")
            return tf_path
        old = {k: cur.get(k) for k in updates}
        cur.update(updates)
        overlay = _write_overlay(tf_path, cfg, user_key)
        log(f"time_filters.json 已含 {user_key} → 脚本参数覆盖（{old} → {updates}），原文件未动，本次用 overlay {overlay.name}", "WARN")
        return overlay
    cfg[user_key] = {
        "target_col": target_col if target_col is not None else "p1",
        "on_thr_w": on_thr if on_thr is not None else "auto",
        "decision_thr_w": decision_thr if decision_thr is not None else "auto",
        "pbus_mode": pbus_mode if pbus_mode is not None else "auto",
        "post_min_on": min_on if min_on is not None else "auto",
        "post_fill_short_off": fill_off if fill_off is not None else "auto",
        "quality": {"day_gate": True, "min_on_day_ratio": 0.2},
        "train": {"include": [["2025-07-10","2026-06-30"]]},
        "infer": {"include": [["2026-07-01","2026-07-31"]]}
    }
    bak = _write_cfg_with_backup(tf_path, cfg)
    log(f"新用户 {user_key} → 已补原 time_filters.json（target={cfg[user_key]['target_col']} on={cfg[user_key]['on_thr_w']} "
        f"dec={cfg[user_key]['decision_thr_w']} pbus_mode={cfg[user_key]['pbus_mode']} "
        f"min_on={cfg[user_key]['post_min_on']} fill_off={cfg[user_key]['post_fill_short_off']}），原文件备份 {bak.name}", "WARN")
    log("  → 请按 TUNING_GUIDE §1 核实 target_col 是否为真实 pN（错则似 OQ-13/800 PAUSED 全返工）", "WARN")
    return tf_path

def step2_train(user_key, tf, base, data_root, out_root, force):
    cmd = [sys.executable, "scripts/run_batch_users.py",
           "--time-filter-config", str(tf),
           "--base-config", str(base),
           "--data-root", str(data_root),
           "--output-root", str(out_root),
           "--user-key", user_key, "--stage", "train"]
    if force: cmd.append("--force")
    sh(cmd)
    outs = sorted((out_root / user_key / "train").glob("*/_DONE")) if (out_root/user_key/"train").exists() else []
    if outs:
        log(f"Step2 训练 OK → {outs[-1].parent}  (共 {len(outs)} 个 _DONE)")
    else:
        log("Step2 警告：未找到 train/_DONE（可能 DATA_QUALITY_FAILED 门禁，查 data_quality_report.html）", "WARN")

def step3_window_check(user_key, out_root):
    import pandas
    pattern = str(out_root / user_key / "train" / "*" / "train_window_index.csv")
    files = sorted(glob.glob(pattern))
    if not files:
        log(f"Step3 跳过：未找到 {pattern}", "WARN")
        return
    f = files[-1]
    w = pandas.read_csv(f)
    s = pandas.to_datetime(w.win_end) - pandas.to_datetime(w.win_start)
    cross = int((s > pandas.Timedelta("23h45m")).sum())
    log(f"Step3 窗口自检 {f} → windows {len(w)} cross_gap {cross} max {s.max()} (期望 cross_gap 0, max 23.75h) {'✓' if cross==0 else '✗ W-1 污染'}")

def step5_infer(user_key, tf, base, data_root, out_root, force):
    cmd = [sys.executable, "scripts/run_batch_users.py",
           "--time-filter-config", str(tf),
           "--base-config", str(base),
           "--data-root", str(data_root),
           "--output-root", str(out_root),
           "--user-key", user_key, "--stage", "infer"]
    if force: cmd.append("--force")
    try:
        sh(cmd)
    except SystemExit as e:
        # 允许缺 infer 目录的空批次（batch.py v2026-09-18 已改为返回 DATA_MISSING 并写 batch_status.csv）
        batch_csvs = sorted((out_root / "batch").glob("*/batch_status.csv"), key=lambda p: p.stat().st_mtime)
        if batch_csvs:
            try:
                import pandas as _pd
                _df = _pd.read_csv(batch_csvs[-1])
                _has_missing = ((_df["user_key"] == user_key) & (_df["mode"] == "infer") & (_df["status"].str.contains("DATA_MISSING"))).any()
                if _has_missing:
                    log(f"Step5 跳过：data/infers/{user_key} 不存在（无推理数据，阈值 infer 链将跳过）— batch_status {batch_csvs[-1]}", "WARN")
                    return
            except Exception:
                pass
        raise
    outs = sorted((out_root / user_key / "infer").glob("*/_DONE")) if (out_root/user_key/"infer").exists() else []
    if outs:
        log(f"Step5 推理 OK → {outs[-1].parent}")
    else:
        log("Step5 未产出 infer/_DONE（可能无 infer 数据或被跳过，查 batch_status.csv）", "WARN")

def _ensure_nilm_console_logger():
    if not logging.getLogger("nilm").handlers and "pytest" not in sys.modules:
        from nilm.common.logging import setup_logging
        setup_logging()


def step_sync_decision_thr(
    user_key: str,
    out_root: Path,
    effective_tf: Path,
    orig_tf: Path,
    stage: str,
    explicit_dec_arg=None,
    explicit_min_on_arg=None,
    explicit_fill_off_arg=None,
    target_col_arg=None,
    pbus_mode_arg=None,
    save_config: bool = False,
) -> dict | None:
    """零重训收口同步 train 与 infer 的 decision_thr_w 与游程参数（支持 auto 双链联合寻优与显式数值同步）。"""
    from nilm.common.logging import get_logger, yellow_text
    from nilm.pipeline.auto_thresholds import (
        calibrate_and_sync_joint_decision_thr,
        sync_decision_thr_artifacts,
    )
    from nilm.pipeline.user_config import load_time_filter_config, resolve_user_config
    from nilm.pipeline.user_task import latest_done_dir

    _ensure_nilm_console_logger()
    ulog = get_logger("cli.auto_run")

    tr_dir = latest_done_dir(out_root / user_key / "train")
    inf_dir = latest_done_dir(out_root / user_key / "infer")

    eff_dec_cfg = explicit_dec_arg
    eff_min_on_cfg = explicit_min_on_arg
    eff_fill_off_cfg = explicit_fill_off_arg
    if effective_tf.exists():
        raw_cfg = load_time_filter_config(effective_tf)
        rcfg = resolve_user_config(user_key, raw_cfg)
        if eff_dec_cfg is None:
            eff_dec_cfg = rcfg.get("decision_thr_w", "auto")
        if eff_min_on_cfg is None:
            eff_min_on_cfg = rcfg.get("post_min_on", 1)
        if eff_fill_off_cfg is None:
            eff_fill_off_cfg = rcfg.get("post_fill_short_off", 3)

    has_explicit_num_dec = isinstance(explicit_dec_arg, (int, float)) and not isinstance(explicit_dec_arg, bool)
    has_explicit_num_mo = isinstance(explicit_min_on_arg, int) and not isinstance(explicit_min_on_arg, bool)
    has_explicit_num_fo = isinstance(explicit_fill_off_arg, int) and not isinstance(explicit_fill_off_arg, bool)

    sync_out: dict | None = None
    if stage == "sync-thr" and (has_explicit_num_dec or has_explicit_num_mo or has_explicit_num_fo):
        if tr_dir is None and inf_dir is None:
            log(f"sync-thr 跳过：未找到 {user_key} 的已完成 train/infer 产物", "WARN")
            return None
        tr_meta_tmp = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8")) if (tr_dir and (tr_dir / "meta.json").exists()) else {}
        inf_meta_tmp = json.loads((inf_dir / "meta.json").read_text(encoding="utf-8")) if (inf_dir and (inf_dir / "meta.json").exists()) else {}
        target_dec = (
            float(explicit_dec_arg)
            if has_explicit_num_dec
            else float(inf_meta_tmp.get("decision_thr_w", tr_meta_tmp.get("decision_thr_w", 10.0)))
        )
        sync_out = sync_decision_thr_artifacts(
            user_key=user_key,
            train_dir=tr_dir,
            infer_dir=inf_dir,
            new_dec_thr=target_dec,
            reason="explicit_sync",
            new_post_min_on=int(explicit_min_on_arg) if has_explicit_num_mo else None,
            new_post_fill_short_off=int(explicit_fill_off_arg) if has_explicit_num_fo else None,
        )
        ulog.warning("%s", yellow_text(
            f"[{user_key}] [DEC-THR-SYNC] 已按显式参数零重训同步刷新双侧产物 -> "
            f"decision_thr_w={target_dec:.4g}, post_min_on={sync_out.get('new_post_min_on')}, "
            f"post_fill_short_off={sync_out.get('new_post_fill_short_off')} "
            f"(train: {sync_out.get('previous_train_decision_thr_w')}->{target_dec:.4g}, "
            f"infer: {sync_out.get('previous_infer_decision_thr_w')}->{target_dec:.4g})"
        ))
    elif eff_dec_cfg == "auto" or eff_min_on_cfg == "auto" or eff_fill_off_cfg == "auto":
        if tr_dir is not None and inf_dir is not None:
            sync_out = calibrate_and_sync_joint_decision_thr(user_key, tr_dir, inf_dir)
        elif stage == "sync-thr":
            log(f"sync-thr(auto) 需同时具备 train 与 infer 已完成产物（当前 train={tr_dir}, infer={inf_dir}）", "WARN")

    if save_config and orig_tf.exists():
        cfg_disk = json.loads(orig_tf.read_text(encoding="utf-8"))
        entry = dict(cfg_disk.get(user_key) or {})
        tr_meta = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8")) if (tr_dir and (tr_dir / "meta.json").exists()) else {}
        inf_meta = json.loads((inf_dir / "meta.json").read_text(encoding="utf-8")) if (inf_dir and (inf_dir / "meta.json").exists()) else {}
        if target_col_arg is not None:
            entry["target_col"] = target_col_arg
        elif tr_meta.get("target_cols") and "target_col" not in entry:
            entry["target_col"] = "+".join(tr_meta["target_cols"])
        if isinstance(tr_meta.get("on_thr_w"), (int, float)):
            entry["on_thr_w"] = float(tr_meta["on_thr_w"])
        final_dec = inf_meta.get("decision_thr_w", tr_meta.get("decision_thr_w"))
        if has_explicit_num_dec:
            final_dec = float(explicit_dec_arg)
        if isinstance(final_dec, (int, float)):
            entry["decision_thr_w"] = float(final_dec)
        final_mo = inf_meta.get("post_min_on", tr_meta.get("post_min_on"))
        if has_explicit_num_mo:
            final_mo = int(explicit_min_on_arg)
        if isinstance(final_mo, int) and not isinstance(final_mo, bool):
            entry["post_min_on"] = int(final_mo)
        final_fo = inf_meta.get("post_fill_short_off", tr_meta.get("post_fill_short_off"))
        if has_explicit_num_fo:
            final_fo = int(explicit_fill_off_arg)
        if isinstance(final_fo, int) and not isinstance(final_fo, bool):
            entry["post_fill_short_off"] = int(final_fo)
        if pbus_mode_arg is not None:
            entry["pbus_mode"] = pbus_mode_arg
        elif tr_meta.get("pbus_mode") and "pbus_mode" not in entry:
            entry["pbus_mode"] = tr_meta["pbus_mode"]
        cfg_disk[user_key] = entry
        bak = _write_cfg_with_backup(orig_tf, cfg_disk)
        ulog.warning("%s", yellow_text(
            f"[{user_key}] [AUTO-SAVE-CONFIG] 已固化阈值与后处理参数至 {orig_tf.name} "
            f"(on_thr_w={entry.get('on_thr_w')}, decision_thr_w={entry.get('decision_thr_w')}, "
            f"post_min_on={entry.get('post_min_on')}, post_fill_short_off={entry.get('post_fill_short_off')}, "
            f"pbus_mode={entry.get('pbus_mode')}；原配置已备份至 {bak.name})"
        ))

    return sync_out


def _detect_sweep_model_cols(out_root: Path, user_key: str, csv_train: str) -> tuple[str, str, str, str]:
    """按 infer/meta.json -> train/meta.json 自动匹配 Step4 扫描模型列与游程参数。"""
    import pandas as _pd
    from nilm.pipeline.user_task import latest_done_dir

    model = "transformer"
    min_on, fill_off = "1", "3"
    tr_dir = latest_done_dir(out_root / user_key / "train")
    inf_dir = latest_done_dir(out_root / user_key / "infer")
    if inf_dir and (inf_dir / "meta.json").exists():
        im = json.loads((inf_dir / "meta.json").read_text(encoding="utf-8"))
        model = str(im.get("model") or model)
        min_on = str(im.get("post_min_on", min_on))
        fill_off = str(im.get("post_fill_short_off", fill_off))
    elif tr_dir and (tr_dir / "meta.json").exists():
        tm = json.loads((tr_dir / "meta.json").read_text(encoding="utf-8"))
        model = str(tm.get("best_model") or model)
        min_on = str(tm.get("post_min_on", min_on))
        fill_off = str(tm.get("post_fill_short_off", fill_off))

    cols = set(_pd.read_csv(csv_train, nrows=1).columns)
    if f"pred_{model}" not in cols:
        avail = [c[len("pred_"):] for c in cols if c.startswith("pred_") and not c.startswith("pred_state_")]
        if avail:
            model = avail[0]
    return f"pred_{model}", f"pred_state_{model}", min_on, fill_off


def step4_threshold(user_key, out_root, pred_col_train=None, state_col_train=None):
    sweep_grid = "10,30,50,100,150,200,250,300,350,400,500"
    pat_train = str(out_root / user_key / "train" / "*" / "predictions" / "train_predictions.csv")
    files = sorted(glob.glob(pat_train))
    min_on, fill_off = "1", "3"
    if files:
        csv_train = files[-1]
        auto_p, auto_s, min_on, fill_off = _detect_sweep_model_cols(out_root, user_key, csv_train)
        p_col = pred_col_train or auto_p
        s_col = state_col_train or auto_s
        log(f"Step4 test链阈值扫描: {csv_train} ({p_col})")
        sh([sys.executable, "scripts/threshold_sweep.py",
            "--csv", csv_train,
            "--pred-col", p_col, "--state-col", s_col,
            "--split", "test",
            "--thresholds", sweep_grid,
            "--min-on", min_on, "--fill-off", fill_off])
    else:
        log(f"Step4 test链跳过：未找到 {pat_train}", "WARN")
    pat_infer = str(out_root / user_key / "infer" / "*" / "predictions" / "inference_result.csv")
    files2 = sorted(glob.glob(pat_infer))
    if files2:
        csv_infer = files2[-1]
        log(f"Step4 infer链阈值扫描: {csv_infer}")
        sh([sys.executable, "scripts/threshold_sweep.py",
            "--csv", csv_infer,
            "--pred-col", "pred", "--state-col", "pred_state",
            "--thresholds", sweep_grid,
            "--min-on", min_on, "--fill-off", fill_off])
    else:
        log(f"Step4 infer链跳过：未找到 {pat_infer}（先完成 Step5 推理）", "WARN")

def main(argv=None):
    ap = argparse.ArgumentParser(description="一键执行 TUNING_GUIDE Step2-5（ train → 窗口 → infer → 双链阈值收口 → 扫描）")
    ap.add_argument("--user-key", default="800080270733_4206673297219", help="用户键 <device>_<user>（默认新户733）")
    ap.add_argument("--time-filter-config", default=str(DEFAULT_TF))
    ap.add_argument("--base-config", default=str(DEFAULT_BASE))
    ap.add_argument("--data-root", default="data")
    ap.add_argument("--output-root", default="outputs")
    ap.add_argument("--target-col", default=None, help="目标通道；已传且与 time_filters.json 不同则用 overlay 覆盖跑（不写回原文件），未传沿用（新用户默认 p1）")
    ap.add_argument("--on-thr", type=_parse_on_thr_arg, default=None,
                    help="on_thr_w（支持正数或 'auto'）；已传且与配置不同则 overlay 覆盖，未传沿用（新用户默认 auto）")
    ap.add_argument("--decision-thr", type=_parse_decision_thr_arg, default=None,
                    help="decision_thr_w（支持正数、'auto' 或 'on_thr'）；已传且与配置不同则 overlay 覆盖，未传沿用（新用户默认 auto）")
    ap.add_argument("--min-on", type=_parse_postproc_arg, default=None,
                    help="post_min_on（支持 0..96 整数或 'auto'）；已传且与配置不同则 overlay 覆盖，未传沿用（新用户默认 auto）")
    ap.add_argument("--fill-off", type=_parse_postproc_arg, default=None,
                    help="post_fill_short_off（支持 0..96 整数或 'auto'）；已传且与配置不同则 overlay 覆盖，未传沿用（新用户默认 auto）")
    ap.add_argument("--pbus-mode", choices=["auto", "raw", "scaled"], default=None,
                    help="pbus_mode（auto=默认先试raw+欠拟合经val双向验收后红色熔断切scaled，默认；raw=锁死未缩放+分摊；scaled=锁死z-score+改学）")
    ap.add_argument("--stage", choices=["all","train","infer","threshold","sync-thr"], default="all",
                    help="all=2+3+5+双链收口+4 完整；threshold=仅 Step4；sync-thr=零重训同步刷新 train+infer 的 decision_thr_w 与游程参数")
    ap.add_argument("--save-config", action="store_true",
                    help="将最终定稿的阈值写回原 time_filters.json（自动备份 .bak.auto），消除 overlay 与原配置差异")
    ap.add_argument("--force", action="store_true", help="--force 忽略 _DONE 重跑")
    ap.add_argument("--skip-train", action="store_true", help="跳过 Step2（已有训练）")
    ap.add_argument("--skip-infer", action="store_true", help="跳过 Step5")
    args = ap.parse_args(argv)

    user_key = args.user_key
    tf = Path(args.time_filter_config)
    base = Path(args.base_config)
    data_root = Path(args.data_root)
    out_root = Path(args.output_root)

    log(f"用户 {user_key}  Stage={args.stage}  TF={tf}  Base={base}  Data={data_root}  Out={out_root}")

    effective_tf = tf
    if args.stage in ("all","train","infer"):
        effective_tf = ensure_user_config(user_key, tf, args.target_col, args.on_thr,
                                          args.decision_thr, args.pbus_mode,
                                          args.min_on, args.fill_off)

    if args.stage in ("all","train") and not args.skip_train:
        step2_train(user_key, effective_tf, base, data_root, out_root, args.force)
    if args.stage in ("all","train"):
        try:
            step3_window_check(user_key, out_root)
        except Exception as e:
            log(f"Step3 异常: {e}", "WARN")
    if args.stage in ("all","infer") and not args.skip_infer:
        step5_infer(user_key, effective_tf, base, data_root, out_root, args.force)

    # 双链联合阈值收口 / 零重训同步刷新（Step5 之后、Step4 扫描之前执行，确保 train 与 infer 100% 一致）
    if args.stage in ("all", "infer", "sync-thr"):
        step_sync_decision_thr(
            user_key=user_key,
            out_root=out_root,
            effective_tf=effective_tf,
            orig_tf=tf,
            stage=args.stage,
            explicit_dec_arg=args.decision_thr,
            explicit_min_on_arg=args.min_on,
            explicit_fill_off_arg=args.fill_off,
            target_col_arg=args.target_col,
            pbus_mode_arg=args.pbus_mode,
            save_config=args.save_config,
        )
    elif args.save_config and args.stage == "train":
        step_sync_decision_thr(
            user_key=user_key,
            out_root=out_root,
            effective_tf=effective_tf,
            orig_tf=tf,
            stage=args.stage,
            explicit_dec_arg=args.decision_thr,
            explicit_min_on_arg=args.min_on,
            explicit_fill_off_arg=args.fill_off,
            target_col_arg=args.target_col,
            pbus_mode_arg=args.pbus_mode,
            save_config=True,
        )

    if args.stage in ("all","threshold"):
        step4_threshold(user_key, out_root)

    log(f"一键完成 {user_key}  Step2-5 → 产出 outputs/{user_key}/{{train,infer}}/<ts>/  后续 Step6 审计见 TUNING_GUIDE §3")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
