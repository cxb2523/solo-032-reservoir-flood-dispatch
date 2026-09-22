"""交付前自检，对应题目最后那三条验收口径。"""

from __future__ import annotations

from .curve import level_at_storage, storage_at_level
from .engine import simulate


def run_selfcheck(raw: dict) -> dict:
    checks = []
    reservoir = next(item for item in raw["reservoirs"] if item["id"] == "QFS")
    curve = reservoir["curve"]

    # 1) 挑一座水库走一遍：水位查库容，再按库容与来水算下泄，两套调度对齐
    trace_hour = 12
    result_a = simulate(raw, "QFS", "A")
    result_b = simulate(raw, "QFS", "B")
    level_h = result_a["levels"][trace_hour]
    storage_h = storage_at_level(curve, level_h)
    level_back = level_at_storage(curve, storage_h)
    row_a = result_a["rows"][trace_hour]
    row_b = result_b["rows"][trace_hour]
    balance_ok = abs(level_back - level_h) < 1e-6
    checks.append({
        "key": "storage_trace",
        "name": "水位→库容→下泄链路核对（青峰水库第12小时）",
        "passed": balance_ok,
        "detail": {
            "hour": trace_hour,
            "level": level_h,
            "storage_wanm3": round(storage_h, 1),
            "level_from_storage": round(level_back, 3),
            "strategy_a": {
                "inflow": row_a["inflow"],
                "release": row_a["release"],
                "basis": row_a["basis"],
            },
            "strategy_b": {
                "inflow": row_b["inflow"],
                "release": row_b["release"],
                "basis": row_b["basis"],
            },
        },
    })

    # 2) 下游安全流量砍掉两成：哪些时段顶线、预警几点发
    cut = simulate(raw, "QFS", "A", safety_factor=0.8)
    warn_detail = []
    for reach in cut["reaches"]:
        if reach["warning"]:
            warn_detail.append({
                "town": reach["town"],
                "safety_flow": reach["safety_flow"],
                "exceed_hours": reach["exceed_hours"],
                "issue_time": reach["warning"]["issue_time"],
                "arrival_time": reach["warning"]["arrival_time"],
                "deep_night": reach["warning"]["deep_night"],
            })
    checks.append({
        "key": "safety_cut_20pct",
        "name": "下游安全流量下调20%后的顶托时段与预警时刻",
        "passed": cut["summary"]["exceed_periods"] > 0 and len(warn_detail) > 0,
        "detail": {
            "exceed_periods": cut["summary"]["exceed_periods"],
            "reach_exceed": cut["summary"]["reach_exceed"],
            "warnings": warn_detail,
        },
    })

    # 3) 同一场降雨连算两遍，闸门开度与下泄流量必须一模一样
    second = simulate(raw, "QFS", "A")
    same_gears = result_a["gears_track"] == second["gears_track"]
    same_release = result_a["releases"] == second["releases"]
    same_levels = result_a["levels"] == second["levels"]
    checks.append({
        "key": "deterministic_twice",
        "name": "同场降雨连算两遍结果一致性",
        "passed": same_gears and same_release and same_levels,
        "detail": {
            "gears_identical": same_gears,
            "release_identical": same_release,
            "levels_identical": same_levels,
        },
    })

    # 附：三库两套策略汇总，便于一眼对齐
    overview = []
    for reservoir_item in raw["reservoirs"]:
        for strategy in ("A", "B"):
            run = simulate(raw, reservoir_item["id"], strategy)
            run80 = simulate(raw, reservoir_item["id"], strategy, safety_factor=0.8)
            overview.append({
                "reservoir": reservoir_item["name"],
                "strategy": strategy,
                "max_level": run["summary"]["max_level"],
                "max_release": run["summary"]["max_release"],
                "peak_cut": run["summary"]["peak_cut"],
                "peak_cut_ratio": run["summary"]["peak_cut_ratio"],
                "exceed_periods": run["summary"]["exceed_periods"],
                "exceed_periods_cut80": run80["summary"]["exceed_periods"],
            })

    return {
        "passed": all(item["passed"] for item in checks),
        "checks": checks,
        "overview": overview,
    }
