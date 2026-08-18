"""端到端：多用户批量 + 单用户模式 + 失败隔离 + 断点续跑（指南 §12/§13）。"""

import json
from pathlib import Path

import pandas as pd

from nilm.common.contracts import INFERENCE_RESULT_REL, Status
from nilm.pipeline.batch import run_batch

from conftest import USER_KEY, write_user_dir

OTHER_KEY = "800080252842_4206894986488"


def _setup(tmp_path: Path):
    """两个合法用户 + 一个非法目录 + 一个缺总线用户（trains/infers 各一份）。"""
    data_root = tmp_path / "data"
    write_user_dir(data_root, USER_KEY, days=21)
    write_user_dir(data_root, OTHER_KEY, days=21, seed=7)
    write_user_dir(data_root, USER_KEY, days=21, mode_dir="infers")
    write_user_dir(data_root, OTHER_KEY, days=21, seed=7, mode_dir="infers")
    (data_root / "trains" / "bad-name").mkdir(parents=True)          # INVALID_USER_DIR
    d = data_root / "trains" / "111_222"                             # DATA_MISSING_BUS
    d.mkdir(parents=True)
    return data_root


def test_batch_multi_user_isolation_and_resume(tmp_path, base_cfg_file, time_filter_file):
    data_root = _setup(tmp_path)
    out_root = tmp_path / "outputs"

    # —— 首次执行：train + infer ——
    info = run_batch(time_filter_file, base_config_path=base_cfg_file,
                     data_root=data_root, output_root=out_root,
                     stages=("train", "infer"))
    table = pd.read_csv(info["status_csv"])

    def row(uk, mode):
        sel = table[(table["user_key"] == uk) & (table["mode"] == mode)]
        assert len(sel) == 1, (uk, mode, table)
        return sel.iloc[0]

    # 两个合法用户训练 + 推理都成功；user_id 字段按接口输出（§0）
    for uk in (USER_KEY, OTHER_KEY):
        r_tr, r_in = row(uk, "train"), row(uk, "infer")
        assert r_tr["status"] == Status.OK, r_tr["message"]
        assert r_in["status"] == Status.OK, r_in["message"]
        assert r_tr["user_id"] == uk.split("_")[1]

    # 失败用户状态码正确，且不阻塞其他用户（§13）
    assert row("bad-name", "train")["status"] == Status.INVALID_USER_DIR
    assert row("111_222", "train")["status"] == Status.DATA_MISSING_BUS

    # 输出契约：predictions/inference_result.csv（§2.3）
    infer_dirs = sorted((out_root / USER_KEY / "infer").iterdir())
    result_csv = infer_dirs[-1] / INFERENCE_RESULT_REL
    assert result_csv.exists()
    res = pd.read_csv(result_csv)
    assert list(res.columns) == ["timestamp", "user_id", "target", "target_state",
                                 "pred", "pred_state", "pred_prob"]
    assert (res["user_id"].astype(str) == USER_KEY.split("_")[1]).all()
    assert len(res) > 0
    # 概率取值域 + 与 on_thr_w 决策边界一致（p>=0.5 ⟺ pred>=thr）
    assert res["pred_prob"].between(0.0, 1.0).all()
    # 状态真值：有 target 处为 0/1，与二值化口径一致
    have = res.dropna(subset=["target"])
    if len(have):
        assert set(have["target_state"].unique()) <= {0, 1}

    # 训练产物完备性（§1/§4/§9：配置快照/schema 报告/质量报告/可辨识性/聚合策略）
    train_dir = sorted((out_root / USER_KEY / "train").iterdir())[-1]
    for fname in ["meta.json", "metrics.json", "comparison.csv", "comparison.md",
                  "data_schema_report.json", "data_quality_report.html",
                  "identifiability_report.json", "agg_strategy.json",
                  "train_window_index.csv", "metrics_by_split.csv",
                  "metrics_daily.csv", "branch_sessions.csv", "_DONE"]:
        assert (train_dir / fname).exists(), fname
    ident = json.loads((train_dir / "identifiability_report.json").read_text(encoding="utf-8"))
    assert ident["identifiable"] is True            # 合成数据高相关，应可辨识
    # 质量报告带清洗后数据统计（总天数/全关天数量/全关天清单）
    meta_q = json.loads((train_dir / "meta.json").read_text(encoding="utf-8"))["quality"]
    for kind in ("bus", "branch"):
        cs = meta_q[kind]["cleaned_stats"]
        assert cs["total_days"] > 0
        assert cs["all_off_days"] == len(cs["all_off_dates"])
    # branch 报告=有效通道（目标通道）口径 + 训练切分级统计（目标功率口径）
    assert meta_q["branch"]["target_cols"]                 # 记录了配置目标分路
    ss = meta_q["branch"]["split_stats"]
    assert set(ss) == {"train", "val", "test"}
    for k in ("train", "val", "test"):
        assert ss[k]["total_days"] > 0
        assert ss[k]["all_off_days"] == len(ss[k]["all_off_dates"])
    html = (train_dir / "data_quality_report.html").read_text(encoding="utf-8")
    assert "清洗后数据统计" in html
    assert "branch·train" in html and "branch·test" in html
    # 逐天质量表 + 双达标统计 + 建议（新增产物与 HTML 段）
    assert "每天数据质量情况" in html and "同时达标天数" in html
    assert "训练数据集划分与模型训练建议" in html
    dq = pd.read_csv(train_dir / "daily_quality.csv")
    assert {"date", "bus_score", "branch_score", "score_threshold",
            "qualified"} <= set(dq.columns)
    assert set(dq["qualified"].unique()) <= {0, 1}
    assert meta_q["branch"]["both_qualified_days"] == int((dq["qualified"] == 1).sum())
    assert (train_dir / "quality_advice.json").exists()
    # 无效通道（非目标）已丢弃：清洗产物与开机分析只含目标通道
    tcols = json.loads((train_dir / "meta.json").read_text(encoding="utf-8"))["target_cols"]
    sessions = pd.read_csv(train_dir / "branch_sessions.csv")
    assert set(sessions["branch"].unique()) <= set(tcols)
    cleaned_br = pd.read_csv(train_dir / "cleaned" / "branch_cleaned.csv")
    assert set(c for c in cleaned_br.columns if c != "timestamp") == set(tcols)

    # 推理侧质量报告（有分路数据时产出，与训练同构 + infer 切分统计）
    infer_dir2 = sorted((out_root / USER_KEY / "infer").iterdir())[-1]
    assert (infer_dir2 / "data_quality_report.html").exists()
    meta_i = json.loads((infer_dir2 / "meta.json").read_text(encoding="utf-8"))
    qi = meta_i["quality"]
    assert qi["bus"]["cleaned_stats"]["total_days"] > 0
    assert qi["branch"]["split_stats"]["infer"]["total_days"] > 0
    html_i = (infer_dir2 / "data_quality_report.html").read_text(encoding="utf-8")
    assert "清洗后数据统计" in html_i and "branch·infer" in html_i
    sessions_i = pd.read_csv(infer_dir2 / "branch_sessions.csv")
    assert set(sessions_i["branch"].unique()) <= set(tcols)
    cleaned_br_i = pd.read_csv(infer_dir2 / "cleaned" / "branch_cleaned.csv")
    assert set(c for c in cleaned_br_i.columns if c != "timestamp") == set(tcols)
    meta = json.loads((train_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["best_model"] in meta["models"]

    # —— 断点续跑：再次执行全部跳过（§13）——
    info2 = run_batch(time_filter_file, base_config_path=base_cfg_file,
                      data_root=data_root, output_root=out_root,
                      stages=("train", "infer"))
    table2 = pd.read_csv(info2["status_csv"])
    ok_rows = table2[table2["user_key"].isin([USER_KEY, OTHER_KEY])]
    assert (ok_rows["status"] == Status.SKIPPED_RESUME).all()

    # —— 原始数据目录不得被改动（§13）——
    assert sorted(p.name for p in (data_root / "trains" / USER_KEY).iterdir()) == \
        sorted(p.name for p in (data_root / "infers" / USER_KEY).iterdir())


def test_force_rerun_ignores_done(tmp_path, base_cfg_file, time_filter_file):
    """force=True：已有 _DONE 产物仍强制重新训练+推理，产物写入新时间戳目录。"""
    data_root = _setup(tmp_path)
    out_root = tmp_path / "outputs"

    # 首次执行完成（产生 _DONE）
    run_batch(time_filter_file, base_config_path=base_cfg_file,
              data_root=data_root, output_root=out_root, stages=("train", "infer"),
              user_keys=[USER_KEY])
    n_train_1 = len(list((out_root / USER_KEY / "train").iterdir()))
    n_infer_1 = len(list((out_root / USER_KEY / "infer").iterdir()))

    # force 重跑：不得出现 SKIPPED_RESUME，全部重新执行为 OK
    info = run_batch(time_filter_file, base_config_path=base_cfg_file,
                     data_root=data_root, output_root=out_root, stages=("train", "infer"),
                     user_keys=[USER_KEY], force=True)
    table = pd.read_csv(info["status_csv"])
    assert (table["status"] == Status.OK).all(), table
    assert not (table["status"] == Status.SKIPPED_RESUME).any()

    # 产物新增时间戳目录（历史产物不被覆盖删除）
    assert len(list((out_root / USER_KEY / "train").iterdir())) == n_train_1 + 1
    assert len(list((out_root / USER_KEY / "infer").iterdir())) == n_infer_1 + 1


def test_force_overrides_resume_default(tmp_path, base_cfg_file, time_filter_file):
    """force 优先级高于 resume：resume=True + force=True 仍重跑。"""
    data_root = _setup(tmp_path)
    out_root = tmp_path / "outputs"
    run_batch(time_filter_file, base_config_path=base_cfg_file,
              data_root=data_root, output_root=out_root, stages=("train",),
              user_keys=[USER_KEY])
    info = run_batch(time_filter_file, base_config_path=base_cfg_file,
                     data_root=data_root, output_root=out_root, stages=("train",),
                     user_keys=[USER_KEY], resume=True, force=True)
    table = pd.read_csv(info["status_csv"])
    r = table[(table["user_key"] == USER_KEY) & (table["mode"] == "train")].iloc[0]
    assert r["status"] == Status.OK, r["message"]


def test_split_and_daily_metrics_csv(tmp_path, base_cfg_file, time_filter_file):
    """训练：三阶段（train/val/test）指标 + 每模型每天指标落盘；推理：日级指标落盘。"""
    data_root = _setup(tmp_path)
    out_root = tmp_path / "outputs"
    run_batch(time_filter_file, base_config_path=base_cfg_file,
              data_root=data_root, output_root=out_root, stages=("train", "infer"),
              user_keys=[USER_KEY])
    train_dir = sorted((out_root / USER_KEY / "train").iterdir())[-1]
    infer_dir = sorted((out_root / USER_KEY / "infer").iterdir())[-1]
    meta = json.loads((train_dir / "meta.json").read_text(encoding="utf-8"))
    models = meta["models"]

    # —— 三阶段汇总：每模型 train/val/test 各一行，指标列非空
    by_split = pd.read_csv(train_dir / "metrics_by_split.csv")
    assert set(by_split["model"]) == set(models)
    for m in models:
        assert set(by_split[by_split["model"] == m]["split"]) == {"train", "val", "test"}
    assert {"mae", "rmse", "r2", "sae"} <= set(by_split.columns)
    assert by_split["mae"].notna().all()

    # —— 训练日级指标：model × split × date；每天点数总和 = 各切分样本数
    daily = pd.read_csv(train_dir / "metrics_daily.csv")
    assert {"model", "split", "date", "n_points"} <= set(daily.columns)
    assert set(daily["model"]) == set(models)
    for s, n in meta["split_sizes"].items():
        one_model = daily[(daily["model"] == models[0]) & (daily["split"] == s)]
        assert one_model["n_points"].sum() == n, (s, n)
    # 日级与整段一致性：test 全段 mae 应落在该模型各日 mae 的 [min, max] 区间
    t = daily[(daily["model"] == models[0]) & (daily["split"] == "test")]
    whole = by_split[(by_split["model"] == models[0]) &
                     (by_split["split"] == "test")]["mae"].iloc[0]
    assert t["mae"].min() - 1e-9 <= whole <= t["mae"].max() + 1e-9

    # —— 推理日级指标：单模型 × date
    idaily = pd.read_csv(infer_dir / "metrics_daily.csv")
    assert {"model", "date", "n_points"} <= set(idaily.columns)
    assert idaily["model"].nunique() == 1
    assert len(idaily) == idaily["date"].nunique()


def test_branch_sessions_artifact(tmp_path, base_cfg_file, time_filter_file):
    """训练与推理前的分路开机分析产物：结构、状态取值、整天关机行覆盖整天。"""
    data_root = _setup(tmp_path)
    out_root = tmp_path / "outputs"
    run_batch(time_filter_file, base_config_path=base_cfg_file,
              data_root=data_root, output_root=out_root, stages=("train", "infer"),
              user_keys=[USER_KEY])
    for mode in ("train", "infer"):
        d = sorted((out_root / USER_KEY / mode).iterdir())[-1]
        f = d / "branch_sessions.csv"
        assert f.exists(), f
        df = pd.read_csv(f)
        assert {"branch", "date", "session_id", "state", "start_time", "end_time",
                "duration_min", "p_min_w", "p_mean_w", "p_max_w",
                "energy_kwh", "n_points"} <= set(df.columns)
        assert set(df["state"].unique()) <= {0, 1}
        assert (df["duration_min"] > 0).all()
        assert (df["p_max_w"] >= df["p_mean_w"]).all()
        assert (df["p_mean_w"] >= df["p_min_w"]).all()
        # 开机段行 session_id 从 1 起；整天关机行 session_id=0
        assert (df.loc[df["state"] == 1, "session_id"] >= 1).all()
        assert (df.loc[df["state"] == 0, "session_id"] == 0).all()


def test_infer_result_state_prob_semantics(tmp_path, base_cfg_file, time_filter_file):
    """推理结果语义：pred_prob 与 on_thr_w 决策边界一致；target_state 与真值二值化一致。"""
    data_root = _setup(tmp_path)
    out_root = tmp_path / "outputs"
    run_batch(time_filter_file, base_config_path=base_cfg_file,
              data_root=data_root, output_root=out_root, stages=("train", "infer"),
              user_keys=[USER_KEY])
    infer_dir = sorted((out_root / USER_KEY / "infer").iterdir())[-1]
    res = pd.read_csv(infer_dir / INFERENCE_RESULT_REL)
    cfg = json.loads(Path(time_filter_file).read_text(encoding="utf-8"))
    thr = float(cfg.get(USER_KEY, {}).get("on_thr_w",
                cfg.get("_default", {}).get("on_thr_w", 10.0)))
    # 概率单调 & 决策边界：pred >= thr ⟺ prob >= 0.5
    assert ((res["pred"] >= thr) == (res["pred_prob"] >= 0.5)).all()
    # target_state 与 target 二值化一致（有真值处）
    have = res.dropna(subset=["target"])
    if len(have):
        assert (have["target_state"] == (have["target"] >= thr).astype(int)).all()


def test_cleaned_csv_saved(tmp_path, base_cfg_file, time_filter_file):
    """清洗后数据落盘：train 保存 bus+branch，infer 保存 bus+branch（离线评估侧）。"""
    data_root = _setup(tmp_path)
    out_root = tmp_path / "outputs"
    run_batch(time_filter_file, base_config_path=base_cfg_file,
              data_root=data_root, output_root=out_root, stages=("train", "infer"),
              user_keys=[USER_KEY])

    train_dir = sorted((out_root / USER_KEY / "train").iterdir())[-1]
    infer_dir = sorted((out_root / USER_KEY / "infer").iterdir())[-1]
    for d, names in ((train_dir, ["bus", "branch"]), (infer_dir, ["bus", "branch"])):
        for name in names:
            f = d / "cleaned" / f"{name}_cleaned.csv"
            assert f.exists(), f
            df = pd.read_csv(f)
            assert df.columns[0] == "timestamp"
            assert len(df) > 0

    # 清洗语义抽查：功率列非负（clip_negative=True）、时间戳无重复
    bus = pd.read_csv(train_dir / "cleaned" / "bus_cleaned.csv")
    p_cols = [c for c in bus.columns if c.startswith("p") and not c.startswith("pf")]
    assert p_cols and (bus[p_cols].fillna(0) >= 0).all().all()
    assert bus["timestamp"].is_unique


def test_invalid_days_excluded_from_training_and_metrics(tmp_path, base_cfg,
                                                         time_filter_file):
    """总线全天缺失/缺失率超阈值的天：不参与训练，且不出现在日级评估指标中。"""
    import numpy as np
    import yaml

    base_cfg["quality"]["max_daily_missing_rate"] = 0.9
    cfg_file = tmp_path / "base_daily.yaml"
    cfg_file.write_text(yaml.safe_dump(base_cfg, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")
    data_root = tmp_path / "data"
    d = write_user_dir(data_root, USER_KEY, days=21)
    write_user_dir(data_root, USER_KEY, days=21, mode_dir="infers")

    # 把训练总线 CSV 的第 8 天（2026-01-08）改为全 NaN（哨兵值→NaN 路径同效，直接置空）
    bus_files = [p for p in d.iterdir() if p.name.startswith("e241_")]
    df = pd.read_csv(bus_files[0])
    ts = pd.to_datetime(df["event_time"])
    df.loc[ts.dt.strftime("%Y-%m-%d") == "2026-01-08",
           [c for c in df.columns if c != "event_time"]] = np.nan
    df.to_csv(bus_files[0], index=False)

    out_root = tmp_path / "outputs"
    info = run_batch(time_filter_file, base_config_path=cfg_file,
                     data_root=data_root, output_root=out_root,
                     stages=("train", "infer"), user_keys=[USER_KEY])
    table = pd.read_csv(info["status_csv"])
    assert (table["status"] == Status.OK).all(), table

    train_dir = sorted((out_root / USER_KEY / "train").iterdir())[-1]
    excl = json.loads((train_dir / "excluded_days.json").read_text(encoding="utf-8"))
    assert "2026-01-08" in excl["excluded_days"]
    # 该天不出现在任何模型/阶段的日级指标里（未参与训练与评估）
    daily = pd.read_csv(train_dir / "metrics_daily.csv")
    assert "2026-01-08" not in set(daily["date"])
    # 质量报告实际天数 = 总天数 − 全天缺失天
    meta_q = json.loads((train_dir / "meta.json").read_text(encoding="utf-8"))["quality"]
    cs = meta_q["bus"]["cleaned_stats"]
    assert cs["actual_days"] == cs["total_days"] - cs["missing_days"]
    # 全关天清单不含全天缺失天
    assert set(cs["all_off_dates"]).isdisjoint(set(cs.get("missing_dates", [])))
    # infer 侧 excluded_days.json 存在（本例推理段无坏天，清单可为空）
    infer_dir = sorted((out_root / USER_KEY / "infer").iterdir())[-1]
    assert (infer_dir / "excluded_days.json").exists()


def test_cleaned_csv_disabled_by_config(tmp_path, base_cfg, time_filter_file):
    """preprocess.save_cleaned_csv=false 时不产出 cleaned/ 目录。"""
    import yaml
    base_cfg["preprocess"]["save_cleaned_csv"] = False
    cfg_file = tmp_path / "base_off.yaml"
    cfg_file.write_text(yaml.safe_dump(base_cfg, allow_unicode=True, sort_keys=False),
                        encoding="utf-8")
    data_root = _setup(tmp_path)
    out_root = tmp_path / "outputs"
    run_batch(time_filter_file, base_config_path=cfg_file,
              data_root=data_root, output_root=out_root, stages=("train", "infer"),
              user_keys=[USER_KEY])
    train_dir = sorted((out_root / USER_KEY / "train").iterdir())[-1]
    infer_dir = sorted((out_root / USER_KEY / "infer").iterdir())[-1]
    assert not (train_dir / "cleaned").exists()
    assert not (infer_dir / "cleaned").exists()


def test_single_user_mode(tmp_path, base_cfg_file, time_filter_file):
    """单用户执行 = users=[一个 key] 的批量（同一代码路径）。"""
    data_root = _setup(tmp_path)
    info = run_batch(time_filter_file, base_config_path=base_cfg_file,
                     data_root=data_root, output_root=tmp_path / "outputs",
                     stages=("train",), user_keys=[USER_KEY])
    table = pd.read_csv(info["status_csv"])
    assert set(table["user_key"]) == {USER_KEY}
    assert (table["status"] == Status.OK).all()


def test_infer_without_train_gives_model_not_found(tmp_path, base_cfg_file, time_filter_file):
    data_root = tmp_path / "data"
    write_user_dir(data_root, USER_KEY, days=21, mode_dir="infers")  # 只有 infer 侧
    info = run_batch(time_filter_file, base_config_path=base_cfg_file,
                     data_root=data_root, output_root=tmp_path / "outputs",
                     stages=("infer",))
    table = pd.read_csv(info["status_csv"])
    r = table[table["user_key"] == USER_KEY].iloc[0]
    assert r["status"] == Status.MODEL_NOT_FOUND     # §13：不得借用他人模型
