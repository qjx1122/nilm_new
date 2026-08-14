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
    assert list(res.columns) == ["timestamp", "user_id", "target", "pred", "pred_state"]
    assert (res["user_id"].astype(str) == USER_KEY.split("_")[1]).all()
    assert len(res) > 0

    # 训练产物完备性（§1/§4/§9：配置快照/schema 报告/质量报告/可辨识性/聚合策略）
    train_dir = sorted((out_root / USER_KEY / "train").iterdir())[-1]
    for fname in ["meta.json", "metrics.json", "comparison.csv", "comparison.md",
                  "data_schema_report.json", "data_quality_report.html",
                  "identifiability_report.json", "agg_strategy.json",
                  "train_window_index.csv", "_DONE"]:
        assert (train_dir / fname).exists(), fname
    ident = json.loads((train_dir / "identifiability_report.json").read_text(encoding="utf-8"))
    assert ident["identifiable"] is True            # 合成数据高相关，应可辨识
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
