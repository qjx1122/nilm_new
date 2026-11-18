#!/usr/bin/env python3
"""PT/CT 有效变比诊断（模式B专用，无引号参数）。

用法：
    python scripts/diag_ptct.py

目的：区分「铭牌变比」与「管线有效变比」。
管线已在 CsvBusLoader 按 bus_field_map multiplier=0.001 换算一次，
再叠加 pt*ct。若铭牌 pt=100/ct=120 直接填入，电压 220V*100=22000V
必超 validator.BOUNDS(0,1000)V，bus_score 归零 -> 无双达标天。

本脚本强制 pt/ct=1.0 加载，打印总线/分路量级与 implied_bus_scale，
供估算有效 pt*ct（开机沿中位比，方法A）。
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from nilm.analysis import identifiability_report  # noqa: E402
from nilm.common.schema import bus_total  # noqa: E402
from nilm.data_io.csv_source import CsvBranchLoader, CsvBusLoader  # noqa: E402
from nilm.data_io.discovery import scan_root  # noqa: E402
from nilm.pipeline.user_config import load_time_filter_config, resolve_user_config  # noqa: E402
from nilm.preprocess.align import align_frames, resample_bus  # noqa: E402
from nilm.preprocess.clean import Cleaner  # noqa: E402
from nilm.preprocess.target import build_target, resolve_target_cols  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
USERS = ["800080252844_4206894986488", "800080270778_4200903422131"]


def main() -> int:
    base_cfg = yaml.safe_load(open(ROOT / "configs" / "base_universal.yaml", encoding="utf-8"))
    tcfg = load_time_filter_config(ROOT / "configs" / "time_filters.json")
    scans = {r.user_key: r for r in scan_root(ROOT / "data" / "trains", "train") if r.ok}
    pp = base_cfg.get("preprocess", {})
    dcfg = base_cfg.get("data", {})
    sentinels = dcfg.get("sentinel_values")
    cleaner = Cleaner(clip_negative=not bool(pp.get("allow_negative_power", False)),
                      max_gap_interp=int(pp.get("max_gap_interp", 2)))
    for uk in USERS:
        if uk not in scans:
            print(f"{uk}: trains无可用数据，跳过")
            continue
        scan = scans[uk]
        ucfg = resolve_user_config(uk, tcfg)
        print(f"\n=== {uk} (配置 pt={ucfg.get('pt_ratio')} ct={ucfg.get('ct_ratio')} "
              f"on_thr={ucfg.get('on_thr_w')} target={ucfg.get('target_col')}) ===")
        field_map = ucfg.get("bus_field_map") or base_cfg.get("bus_field_map")
        bus_raw, _ = CsvBusLoader().load(scan.bus_files, field_map, sentinels=sentinels,
                                         derive_phase_from_ptotal=bool(dcfg.get("derive_phase_from_ptotal", False)))
        bus_c = cleaner.transform(bus_raw)  # 强制1.0，不校正
        branch_raw, _ = CsvBranchLoader().load(scan.branch_files, sentinels=sentinels)
        branch_c = cleaner.transform(branch_raw)
        tc = ucfg.get("target_col") or "p2"
        try:
            target_cols = resolve_target_cols(tc, branch_c)
            branch_c = branch_c[target_cols]
            target = build_target(branch_c, target_cols)
        except Exception as e:  # noqa: BLE001
            print(f"  分路解析失败: {e}")
            continue
        bus15, _ = resample_bus(bus_c, strategy=pp.get("agg_strategy"))
        bus_al, branch_al = align_frames(bus15, branch_c, min_overlap=float(pp.get("min_overlap", 0.3)))
        target = target.loc[branch_al.index]
        pbus = bus_total(bus_al)
        print(f"  总线(1.0): ua中位={bus_al['ua'].median():.1f}V ia中位={bus_al['ia'].median():.2f}A "
              f"pbus中位={pbus.median():.1f}W p95={pbus.quantile(0.95):.1f}W max={pbus.max():.1f}W")
        print(f"  分路: target中位={target.median():.1f}W max={target.max():.1f}W "
              f"on_rate={(target >= float(ucfg['on_thr_w'])).mean():.3f}")
        try:
            rep = identifiability_report(bus_al, target, on_thr_w=float(ucfg["on_thr_w"]))
            print(f"  implied_bus_scale={rep.get('implied_bus_scale')} "
                  f"bus_edge_snr={rep.get('bus_edge_snr')} n_edges={rep.get('n_on_edges')}")
            print(f"  建议有效 pt*ct ≈ implied_bus_scale（铭牌12000远大于此即为重复换算）")
        except Exception as e:  # noqa: BLE001
            print(f"  可辨识性失败: {e}")
    print("\n=== 诊断完 ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
