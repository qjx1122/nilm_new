"""data_io.merge：两级合并（先内源后跨源）、重叠告警跳过、结构复刻（需求文档 §3–§6）。"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from nilm.data_io.merge import BusFile, discover_source, ranges_overlap, run_merge

DEV, USR, UK = "8001", "9001", "8001_9001"


def _write_bus_csv(d: Path, ch: int, s: str, e: str, rows: list[str],
                   suffix: str = "", device: str = DEV, user: str = USR) -> Path:
    """写一个 RE_BUS 命名的总线 CSV（event_time 时间列）。"""
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"e241_{device}_{user}-Ch{ch}-{s}-{e}{suffix}.csv"
    p.write_text("event_time,val\n" + "\n".join(f"{t},{i}" for i, t in enumerate(rows)) + "\n",
                 encoding="utf-8")
    return p


def _write_branch_csv(d: Path, user: str, s: str, e: str, rows: list[str],
                      suffix: str = "") -> Path:
    """写一个严格格式的分路用户数据 CSV（<用户号>-<起>-<止>.csv，time 时间列）。"""
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{user}-{s}-{e}{suffix}.csv"
    p.write_text("time,p1\n" + "\n".join(f"{t},{i}" for i, t in enumerate(rows)) + "\n",
                 encoding="utf-8")
    return p


def _day_rows(start: str, n: int) -> list[str]:
    return [f"{start} {h:02d}:00:00" for h in range(n)]


def _setup_sources(tmp_path: Path) -> tuple[Path, Path, Path]:
    """srcA: userX 两组(Ch1 可合并/Ch2 单文件) + userY 重叠组
    srcB: userX Ch1（与 srcA 无重叠，可跨源合并）
    srcC: userX Ch1（与 srcA 跨源重叠 → 告警跳过）"""
    srcA, srcB, srcC = tmp_path / "srcA", tmp_path / "srcB", tmp_path / "srcC"
    # srcA userX Ch1：两段无重叠 260101-260110 / 260111-260120
    _write_bus_csv(srcA / UK, 1, "260101", "260110", _day_rows("2026-01-01", 5))
    _write_bus_csv(srcA / UK, 1, "260111", "260120", _day_rows("2026-01-11", 5))
    # srcA userX Ch2：单文件（无需合并，保留）
    _write_bus_csv(srcA / UK, 2, "260101", "260105", _day_rows("2026-01-01", 3))
    # srcA userX 分路文件（严格格式 <用户号>-<起>-<止>.csv，同为合并目标）
    _write_branch_csv(srcA / UK, USR, "260101", "260120", _day_rows("2026-01-01", 4))
    # srcA userY：同通道时间重叠 → 整组告警跳过（文件名身份与目录一致）
    _write_bus_csv(srcA / "8002_9002", 1, "260101", "260110", _day_rows("2026-01-01", 4),
                   device="8002", user="9002")
    _write_bus_csv(srcA / "8002_9002", 1, "260105", "260115", _day_rows("2026-01-05", 4),
                   device="8002", user="9002")
    # srcB userX Ch1：260201-260210，与 srcA 合并结果无重叠
    _write_bus_csv(srcB / UK, 1, "260201", "260210", _day_rows("2026-02-01", 5))
    # srcC userX Ch1：260115-260125，与 srcA 的 260101-260120 重叠
    _write_bus_csv(srcC / UK, 1, "260115", "260125", _day_rows("2026-01-15", 5))
    return srcA, srcB, srcC


def test_ranges_overlap():
    from datetime import date
    d = lambda s: date(*map(int, s.split("-")))
    assert ranges_overlap(d("2026-01-01"), d("2026-01-10"), d("2026-01-10"), d("2026-01-20"))
    assert not ranges_overlap(d("2026-01-01"), d("2026-01-10"), d("2026-01-11"), d("2026-01-20"))


def test_discover_source(tmp_path):
    srcA, _, _ = _setup_sources(tmp_path)
    found, skipped = discover_source(srcA)
    assert len(found) == 6                     # 5 个总线严格格式 + 1 个分路严格格式
    assert len(skipped) == 0
    assert all(isinstance(f, BusFile) for f in found)
    assert {f.group_key for f in found} == {(UK, 1), (UK, 2), (UK, "branch"), ("8002_9002", 1)}
    br = [f for f in found if f.kind == "branch"][0]
    assert br.ch is None and br.user == USR and br.group_label == "branch"


def test_discover_rejects_suffixed_files(tmp_path):
    """带 -1/-infer 后缀的文件不符合合并严格格式，必须排除在合并对象之外。"""
    src = tmp_path / "srcS"
    uk = src / UK
    # 合规：严格格式
    _write_bus_csv(uk, 1, "260101", "260110", _day_rows("2026-01-01", 3))
    # 不合规：同区间但带后缀（若误入合并会造成重复数据）
    _write_bus_csv(uk, 1, "260111", "260120", _day_rows("2026-01-11", 3), suffix="-1")
    _write_bus_csv(uk, 2, "260101", "260105", _day_rows("2026-01-01", 2), suffix="-infer")

    found, skipped = discover_source(src)
    assert len(found) == 1                     # 只有严格格式文件是合并对象
    assert found[0].ch == 1 and found[0].start.strftime("%y%m%d") == "260101"
    assert len(skipped) == 2                   # 两个带后缀文件被跳过
    assert all("-1.csv" in p.name or "-infer.csv" in p.name for p in skipped)

    # 合并只消费严格格式文件：Ch1 组内只剩单文件，不会与 -1 后缀文件合并
    out = tmp_path / "merged"
    report = run_merge([src], output_root=out)
    groups = report["phase1_intra_source"]["srcS"]
    assert all(g["status"] == "OK" for g in groups)
    merged_names = [p.name for p in (out / "srcS" / UK).glob("*.csv")]
    assert merged_names == ["e241_8001_9001-Ch1-260101-260110.csv"]  # 原名保留，无合并产物


def test_two_level_merge_end_to_end(tmp_path):
    srcA, srcB, srcC = _setup_sources(tmp_path)
    out = tmp_path / "merged"
    report = run_merge([srcA, srcB, srcC], output_root=out)

    # —— 阶段一：内源合并，复刻「数据源/用户目录」层级 ——
    merged_ch1 = out / "srcA" / UK / f"e241_{DEV}_{USR}-Ch1-260101-260120.csv"
    assert merged_ch1.exists()                       # 无重叠 → 合并，起止取最早/最晚
    df = pd.read_csv(merged_ch1)
    assert len(df) == 10                             # 5 + 5 行
    assert df["event_time"].is_monotonic_increasing

    kept_ch2 = out / "srcA" / UK / f"e241_{DEV}_{USR}-Ch2-260101-260105.csv"
    assert kept_ch2.exists()                         # 单文件组直接保留原名

    # 2026-09-23 更新：重叠不再跳过，改为告警+剔除重复时间戳后合并
    overlapped_intra = out / "srcA" / "8002_9002" / "e241_8002_9002-Ch1-260101-260115.csv"
    assert overlapped_intra.exists()                  # 原跳过现合并（去重后 8 行，无重复时间戳）
    assert len(pd.read_csv(overlapped_intra)) == 8
    g_intra = [g for g in report["phase1_intra_source"]["srcA"] if g.get("user_key") == "8002_9002"][0]
    assert g_intra["status"] == "OK" and g_intra.get("overlap_handled")

    # —— 阶段二：跨源合并（srcA+srcB 无重叠；srcC 重叠告警后去重合并）——
    assert report["warnings"] >= 2                   # userY 内源重叠 + userX 跨源重叠
    cross_groups = report["phase2_cross_source"][UK]
    statuses = {(g["ch"], g["status"]) for g in cross_groups}
    assert (1, "OK") in statuses                      # srcC 触发跨源重叠 → 告警后去重合并为 OK
    assert (2, "OK") in statuses                     # Ch2 仅 srcA 有，无需跨源
    cross_merged = out / "cross_source" / UK / f"e241_{DEV}_{USR}-Ch1-260101-260210.csv"
    assert cross_merged.exists()                      # 重叠已去重合并，生成跨源文件（10+5+5 去重后 20 行）
    g_cross = [g for g in cross_groups if g["ch"] == 1][0]
    assert g_cross.get("overlap_handled") and g_cross["status"] == "OK"
    assert len(pd.read_csv(cross_merged)) == 20

    # —— 告警日志：精准记录用户目录/文件名/冲突区间（§6.2）——
    wlog = (out / "logs" / "merge_warnings.log").read_text(encoding="utf-8")
    assert "8002_9002" in wlog and "2026-01-05" in wlog     # 内源重叠记录
    assert "srcC" in wlog or "跨源" in wlog                 # 跨源重叠记录
    assert "剔除重复" in wlog or "去重" in wlog
    assert (out / "logs" / "merge_run.log").exists()
    assert (out / "logs" / "merge_report.json").exists()


def test_cross_source_merge_when_no_overlap(tmp_path):
    """去掉 srcC 后，跨源合并应成功并更新起止时间（§4.2）。"""
    srcA, srcB, _ = _setup_sources(tmp_path)
    out = tmp_path / "merged"
    report = run_merge([srcA, srcB], output_root=out)
    cross_out = out / "cross_source" / UK / f"e241_{DEV}_{USR}-Ch1-260101-260210.csv"
    assert cross_out.exists()                        # 260101~260120 + 260201~260210
    df = pd.read_csv(cross_out)
    assert len(df) == 15                             # 10 + 5
    g = [g for g in report["phase2_cross_source"][UK] if g["ch"] == 1][0]
    assert g["status"] == "OK" and g["action"] == "merged"


def test_branch_files_merge_same_rules(tmp_path):
    """分路格式文件（<用户号>-<起>-<止>.csv）合并规则与总线一致：
    内源迭代合并、重叠告警+去重合并、跨源合并、单源透传。"""
    srcA, srcB = tmp_path / "srcA", tmp_path / "srcB"
    # userX 分路：srcA 两段无重叠（内源合并）；srcB 一段（跨源合并）
    _write_branch_csv(srcA / UK, USR, "260101", "260110", _day_rows("2026-01-01", 4))
    _write_branch_csv(srcA / UK, USR, "260111", "260120", _day_rows("2026-01-11", 4))
    _write_branch_csv(srcB / UK, USR, "260201", "260210", _day_rows("2026-02-01", 4))
    # userY 分路：时间重叠 → 告警后去重合并（2026-09-23 更新）
    _write_branch_csv(srcA / "8002_9002", "9002", "260101", "260110", _day_rows("2026-01-01", 3))
    _write_branch_csv(srcA / "8002_9002", "9002", "260105", "260115", _day_rows("2026-01-05", 3))

    out = tmp_path / "merged"
    report = run_merge([srcA, srcB], output_root=out)

    # 内源：两段合并为 <用户号>-260101-260120.csv（仅更新起止时间，命名规范不变）
    intra_merged = out / "srcA" / UK / f"{USR}-260101-260120.csv"
    assert intra_merged.exists()
    assert len(pd.read_csv(intra_merged)) == 8
    # userY 重叠组告警后去重合并（原跳过现合并 6 行）
    intra_y = out / "srcA" / "8002_9002" / "9002-260101-260115.csv"
    assert intra_y.exists()
    assert len(pd.read_csv(intra_y)) == 6
    # 跨源：srcA 合并结果 + srcB → <用户号>-260101-260210.csv
    cross_merged = out / "cross_source" / UK / f"{USR}-260101-260210.csv"
    assert cross_merged.exists()
    assert len(pd.read_csv(cross_merged)) == 12
    # 报告：分路组以 "branch" 标识；告警含 userY 分路重叠
    g = [g for g in report["phase2_cross_source"][UK] if g["ch"] == "branch"][0]
    assert g["status"] == "OK" and g["action"] == "merged"
    wlog = (out / "logs" / "merge_warnings.log").read_text(encoding="utf-8")
    assert "8002_9002" in wlog and "branch" in wlog
    assert "剔除重复" in wlog or "去重" in wlog
    assert report["warnings"] == 1


def test_branch_single_source_passthrough(tmp_path):
    """分路文件单源独有：直接作为合并后用户数据文件。"""
    srcA, srcB = tmp_path / "srcA", tmp_path / "srcB"
    _write_branch_csv(srcA / UK, USR, "260301", "260305", _day_rows("2026-03-01", 2))
    _write_bus_csv(srcB / UK, 1, "260201", "260210", _day_rows("2026-02-01", 2))  # srcB 只有总线

    out = tmp_path / "merged"
    report = run_merge([srcA, srcB], output_root=out)
    passed = out / "cross_source" / UK / f"{USR}-260301-260305.csv"
    assert passed.exists()                       # 原名原样透传
    assert len(pd.read_csv(passed)) == 2
    g = [g for g in report["phase2_cross_source"][UK] if g["ch"] == "branch"][0]
    assert g["action"] == "copied_single_source" and g["sources"] == ["srcA"]


def test_branch_suffixed_rejected(tmp_path):
    """带后缀的分路文件不符合严格格式，不参与合并。"""
    src = tmp_path / "srcS"
    _write_branch_csv(src / UK, USR, "260101", "260110", _day_rows("2026-01-01", 2))
    _write_branch_csv(src / UK, USR, "260111", "260120", _day_rows("2026-01-11", 2), suffix="-1")
    found, skipped = discover_source(src)
    assert len(found) == 1 and len(skipped) == 1
    assert "-1.csv" in skipped[0].name


def test_single_source_user_passthrough_to_merged_dir(tmp_path):
    """用户目录仅在 1 个源中存在（另一源无此用户目录）→ 其文件直接作为合并后用户数据文件。"""
    srcA, srcB = tmp_path / "srcA", tmp_path / "srcB"
    # userX：两源都有（走跨源合并）
    _write_bus_csv(srcA / UK, 1, "260101", "260110", _day_rows("2026-01-01", 4))
    _write_bus_csv(srcB / UK, 1, "260201", "260210", _day_rows("2026-02-01", 4))
    # userB：仅 srcA 有（srcB 中不存在该用户目录）
    _write_bus_csv(srcA / "8003_9003", 1, "260301", "260305", _day_rows("2026-03-01", 3),
                   device="8003", user="9003")

    out = tmp_path / "merged"
    report = run_merge([srcA, srcB], output_root=out)

    # userB 的文件原样直接进入合并后用户数据目录（原名、内容不变）
    passed = out / "cross_source" / "8003_9003" / "e241_8003_9003-Ch1-260301-260305.csv"
    assert passed.exists()
    assert len(pd.read_csv(passed)) == 3
    g = [g for g in report["phase2_cross_source"]["8003_9003"] if g["ch"] == 1][0]
    assert g["status"] == "OK" and g["action"] == "copied_single_source"
    assert g["sources"] == ["srcA"]

    # userX 仍是真正的跨源合并
    assert (out / "cross_source" / UK / f"e241_{DEV}_{USR}-Ch1-260101-260210.csv").exists()
    gx = [g for g in report["phase2_cross_source"][UK] if g["ch"] == 1][0]
    assert gx["action"] == "merged" and gx["sources"] == ["srcA", "srcB"]


def test_single_source_passthrough_with_no_keep_original(tmp_path):
    """--no-keep-original 时单源独有用户仍必须出现在合并后目录（透传不依赖单源保留选项）。"""
    srcA, srcB = tmp_path / "srcA", tmp_path / "srcB"
    _write_bus_csv(srcA / UK, 1, "260101", "260110", _day_rows("2026-01-01", 2))
    _write_bus_csv(srcB / UK, 1, "260201", "260210", _day_rows("2026-02-01", 2))
    _write_bus_csv(srcA / "8003_9003", 2, "260301", "260305", _day_rows("2026-03-01", 2),
                   device="8003", user="9003")

    out = tmp_path / "merged"
    run_merge([srcA, srcB], output_root=out, keep_original=False)
    # 单源独有用户（且为单文件组）依然透传到合并后目录
    assert (out / "cross_source" / "8003_9003" / "e241_8003_9003-Ch2-260301-260305.csv").exists()
    # 跨源合并照常
    assert (out / "cross_source" / UK / f"e241_{DEV}_{USR}-Ch1-260101-260210.csv").exists()


def test_original_sources_readonly(tmp_path):
    """原始数据源只读：合并后源目录文件清单不变（§5/指南 §13）。"""
    srcA, srcB, _ = _setup_sources(tmp_path)
    before = sorted(str(p.relative_to(tmp_path)) for p in srcA.rglob("*") if p.is_file())
    run_merge([srcA, srcB], output_root=tmp_path / "merged")
    after = sorted(str(p.relative_to(tmp_path)) for p in srcA.rglob("*") if p.is_file())
    assert before == after


def test_no_keep_original_option(tmp_path):
    srcA, _, _ = _setup_sources(tmp_path)
    out = tmp_path / "merged"
    run_merge([srcA], output_root=out, keep_original=False)
    # 单文件组（Ch2）不保留；多文件合并结果仍输出
    assert not (out / "srcA" / UK / f"e241_{DEV}_{USR}-Ch2-260101-260105.csv").exists()
    assert (out / "srcA" / UK / f"e241_{DEV}_{USR}-Ch1-260101-260120.csv").exists()


def test_overlap_with_duplicate_timestamps_deduped_and_warned(tmp_path):
    """2026-09-23 新增：重叠区间先告警，再剔除重复时间戳后合并（去重保留先出现者）。"""
    src = tmp_path / "srcD"
    uk = src / UK
    # 两文件时间区间重叠 260101-260110 vs 260105-260115，且内容时间戳 2026-01-05~07 重复 3 行
    rows_a = _day_rows("2026-01-01", 5) + _day_rows("2026-01-05", 3)  # 01-01~01-04 + 01-05 00-02
    rows_b = _day_rows("2026-01-05", 3) + _day_rows("2026-01-10", 4)  # 01-05 00-02 重复 + 01-10 00-03
    _write_bus_csv(uk, 1, "260101", "260110", rows_a)
    _write_bus_csv(uk, 1, "260105", "260115", rows_b)
    out = tmp_path / "merged"
    report = run_merge([src], output_root=out)
    merged = out / "srcD" / UK / "e241_8001_9001-Ch1-260101-260115.csv"
    assert merged.exists()
    df = pd.read_csv(merged)
    # 5+3 + 3+4 -3重复 =12 行
    assert len(df) == 12
    assert df["event_time"].is_monotonic_increasing
    assert df["event_time"].duplicated().sum() == 0
    # 报告标记重叠已处理，去重计数 3
    g = [g for g in report["phase1_intra_source"]["srcD"] if g.get("user_key") == UK][0]
    assert g.get("overlap_handled") and g.get("duplicates_removed") == 3
    assert report["warnings"] >= 1
    wlog = (out / "logs" / "merge_warnings.log").read_text(encoding="utf-8")
    assert "重叠" in wlog and "剔除重复" in wlog
    # cross_source 无合并数据时直接拷贝（单源透传）
    cross = out / "cross_source" / UK / "e241_8001_9001-Ch1-260101-260115.csv"
    assert cross.exists()
    assert len(pd.read_csv(cross)) == 12


def test_no_merged_data_fallback_copy(tmp_path):
    """2026-09-23 新增：如无合并数据（单文件组），直接拷贝当前数据到目标文件夹。"""
    src = tmp_path / "srcE"
    uk = src / UK
    # 单文件组，无需合并
    _write_bus_csv(uk, 1, "260301", "260305", _day_rows("2026-03-01", 2))
    _write_branch_csv(uk, USR, "260301", "260305", _day_rows("2026-03-01", 2))
    out = tmp_path / "merged"
    report = run_merge([src], output_root=out)
    # 阶段一直接拷贝
    assert (out / "srcE" / UK / "e241_8001_9001-Ch1-260301-260305.csv").exists()
    assert (out / "srcE" / UK / "9001-260301-260305.csv").exists()
    # 阶段二单源透传亦拷贝到 cross_source（无合并数据直接拷贝语义）
    assert (out / "cross_source" / UK / "e241_8001_9001-Ch1-260301-260305.csv").exists()
    assert (out / "cross_source" / UK / "9001-260301-260305.csv").exists()
    # 报告为 OK
    assert all(g["status"] == "OK" for g in report["phase1_intra_source"]["srcE"])
    assert all(g["status"] == "OK" for gs in report["phase2_cross_source"].values() for g in gs)
