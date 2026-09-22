"""入库流量—水位—闸门—下游河道逐时调度模拟。

两套口径：
  A 库容曲线查表削峰：过汛限按超出库容回算需泄流量，受下游安全流量约束削峰错峰；
  B 水位涨速提前预泄：按近2h涨速与来水上涨趋势，未到汛限先预泄腾库。

硬规则：闸门逐档调节；下游流量含沿程区间来水；预警按传播时间倒推。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from itertools import product

from .curve import level_at_storage, storage_at_level
from .warnings import reach_results, summarize

TAIL_HOURS = 8


def simulate(raw, reservoir_id, strategy="A", overrides=None, safety_factor=1.0):
    reservoir = next(item for item in raw["reservoirs"] if item["id"] == reservoir_id)
    curve = reservoir["curve"]
    gates = reservoir["gates"]
    reaches = [r for r in raw["reaches"] if r["reservoir_id"] == reservoir_id]
    overrides = sorted(overrides or [], key=lambda item: item["hour"])

    hours_total = raw["hydrograph"]["hours"]
    steps = hours_total + TAIL_HOURS
    inflow = _extended_inflow(raw, reservoir, steps)
    start = datetime.fromisoformat(raw["event"]["start_time"])

    local = {
        r["id"]: [inflow[t] * r["local_factor"] for t in range(steps)]
        for r in reaches
    }
    down_caps = {
        r["id"]: [round(r["safety_flow"] * safety_factor - local[r["id"]][t], 1)
                  for t in range(steps)]
        for r in reaches
    }

    levels = [float(reservoir["current_level"])]
    gears_now = [0] * len(gates)
    gear_track = [[] for _ in gates]
    releases = []
    rows = []
    override_errors = []

    for t in range(steps):
        level = levels[-1]
        inflow_t = inflow[t]
        cap = min(down_caps[r["id"]][t] for r in reaches)
        max_release = sum(g["gear_flows"][-1] for g in gates)
        # 保坝优先：水位超过汛限后错峰库容用尽，下泄允许顶过下游安全流量并留痕
        emergency = level > reservoir["flood_limit_level"] + 1e-9

        forced = _override_at(overrides, t)
        if forced:
            chosen, basis, notes, error = _apply_override(
                gates, gears_now, forced, cap, t
            )
            if error:
                override_errors.append(error)
        else:
            chosen, basis, notes = _auto_decision(
                strategy, gates, gears_now, curve, level, inflow_t, inflow,
                levels, cap, max_release, reservoir["flood_limit_level"], t, emergency
            )

        release = sum(gates[i]["gear_flows"][chosen[i]] for i in range(len(gates)))
        for i, gear in enumerate(chosen):
            gear_track[i].append(gear)
        new_level = _route_level(curve, level, inflow_t, release)

        over_segments = [
            r["town"] for r in reaches
            if release + local[r["id"]][t] > r["safety_flow"] * safety_factor + 1e-9
        ]

        rows.append({
            "hour": t,
            "time": (start + timedelta(hours=t)).isoformat(),
            "level": round(level, 3),
            "inflow": round(inflow_t, 1),
            "release": round(release, 1),
            "gears": {gates[i]["uid"]: chosen[i] for i in range(len(gates))},
            "over_limit": level > reservoir["flood_limit_level"] + 1e-9,
            "over_segments": over_segments,
            "basis": basis,
            "gate_changes": notes,
        })
        releases.append(round(release, 1))
        levels.append(round(new_level, 3))
        gears_now = chosen

    reach_data = reach_results(reaches, releases, local, safety_factor, start, steps)
    summary = summarize(raw, reservoir, levels, releases, inflow, reach_data, steps)

    return {
        "reservoir_id": reservoir_id,
        "reservoir_name": reservoir["name"],
        "strategy": "manual" if overrides else strategy,
        "safety_factor": safety_factor,
        "flood_limit_level": reservoir["flood_limit_level"],
        "current_level": reservoir["current_level"],
        "steps": steps,
        "rain_hours": hours_total,
        "start_time": start.isoformat(),
        "peak_hour": raw["hydrograph"]["peak_hour"],
        "levels": levels,
        "inflow": [round(v, 1) for v in inflow],
        "releases": releases,
        "gates": [
            {
                "uid": g["uid"],
                "code": g["code"],
                "name": g["name"],
                "gear_flows": g["gear_flows"],
                "max_gear": g["max_gear"],
            }
            for g in gates
        ],
        "gears_track": {gates[i]["uid"]: gear_track[i] for i in range(len(gates))},
        "rows": rows,
        "reaches": reach_data,
        "warnings": [r["warning"] for r in reach_data if r["warning"]],
        "summary": summary,
        "override_errors": override_errors,
    }


def _auto_decision(strategy, gates, gears_now, curve, level, inflow_t, inflow,
                   levels, cap, max_release, limit_level, t, emergency):
    if strategy == "B":
        target, basis = _target_b(
            curve, level, limit_level, inflow_t, inflow, levels, cap, max_release, t
        )
    else:
        target, basis = _target_a(
            curve, level, limit_level, inflow_t, cap, max_release
        )
    chosen, flow, notes = _choose_combo(gates, gears_now, target, cap, emergency)
    basis.append(
        f"逐档约束：各闸相对上一小时最多调一档，在全部可行档位组合里选下泄{flow:.1f}"
        f"m3/s（目标{round(target, 1)}m3/s，"
        f"{'保坝优先、穿透下游约束' if emergency else '下游安全流量约束生效'}）"
    )
    return chosen, basis, notes


def _target_a(curve, level, limit_level, inflow_t, cap, max_release):
    """查表削峰：以汛限水位对应库容为目标，逐时回算需泄流量。"""
    cur_storage = storage_at_level(curve, level)
    limit_storage = storage_at_level(curve, limit_level)
    excess = max(0.0, cur_storage - limit_storage)
    need = inflow_t + excess / 0.36
    basis = [
        f"查表口径：当前水位{level:.2f}m，汛限{limit_level:.2f}m，"
        f"查库容曲线得库容{cur_storage:.1f}万m3",
        f"水量平衡回算需泄流量 = 入库{inflow_t:.1f} + 超蓄库容{excess:.1f}万m3"
        f"÷0.36 = {need:.1f}m3/s",
    ]
    if level > limit_level + 1e-9:
        target = min(max(need, inflow_t), max_release)
        if target > cap:
            basis.append(
                f"水位已超汛限、错峰库容用尽，按保坝要求下泄{target:.1f}m3/s，"
                f"超出下游允许{cap:.1f}m3/s，下游将顶过安全流量并留痕"
            )
        else:
            basis.append(f"水位超汛限，按削峰要求下泄{target:.1f}m3/s压回汛限")
    else:
        target = min(need, cap, max_release)
        if cap < inflow_t - 1e-9:
            basis.append(
                f"下游安全约束仅允许下泄{cap:.1f}m3/s，小于入库{inflow_t:.1f}m3/s，"
                "来水蓄入库内削峰，错峰待下游退水"
            )
        else:
            basis.append(f"未超汛限，按来水同量级控泄，目标下泄{target:.1f}m3/s")
    return target, basis


def _target_b(curve, level, limit_level, inflow_t, inflow, levels,
              cap, max_release, t):
    """涨速预泄：近2h涨速加来水上涨趋势，提前预泄到汛限以下1m。"""
    rate = (level - levels[-3]) / 2.0 if t >= 2 else 0.0
    inflow_slope = (inflow_t - inflow[t - 2]) / 2.0 if t >= 2 else 0.0
    inflow_trend = max(inflow_t, inflow_t + max(0.0, inflow_slope) * 2.0)
    pre_level = limit_level - 1.0
    triggered = (
        level >= limit_level - 1.5
        and (rate >= 0.02 or inflow_slope >= 15.0)
        and inflow_t > inflow[0] * 1.3
    )
    basis = [
        f"涨速口径：近2h水位涨速{rate * 100:.1f}cm/h，当前水位{level:.2f}m，"
        f"汛限{limit_level:.2f}m，近2h来水涨幅{inflow_slope:.1f}m3/s/h，"
        f"预判2h后入库约{inflow_trend:.1f}m3/s",
    ]
    if level > limit_level + 1e-9:
        gap = max(0.0, storage_at_level(curve, level)
                  - storage_at_level(curve, limit_level))
        need = inflow_trend + gap / 0.36
        target = min(max(need, inflow_t), max_release)
        if target > cap:
            basis.append(
                f"已超汛限且涨势未止，保坝下泄{target:.1f}m3/s，下游将顶过安全流量"
            )
        else:
            basis.append(f"已超汛限，按涨速预判回算下泄{target:.1f}m3/s压回")
        return target, basis
    if triggered:
        gap = max(0.0, storage_at_level(curve, level)
                  - storage_at_level(curve, pre_level))
        need = inflow_trend + gap / 0.36
        target = min(need, cap, max_release)
        basis.append(
            f"涨速达预泄阈值，提前预泄腾库，目标回落至{pre_level:.1f}m，"
            f"目标下泄{target:.1f}m3/s（受下游安全流量约束）"
        )
        return target, basis
    target = min(inflow_t, cap, max_release)
    basis.append("涨速与水位均未达预泄阈值，按来水同量级控泄")
    return target, basis


def _apply_override(gates, gears_now, forced, cap, t):
    chosen = list(gears_now)
    notes = []
    errors = []
    for item in forced:
        uid, gear = item["uid"], int(item["gear"])
        index = _gate_index(gates, uid)
        if index is None:
            errors.append({"hour": t, "uid": uid, "message": f"找不到闸门 {uid}"})
            continue
        if not 0 <= gear <= gates[index]["max_gear"]:
            errors.append({
                "hour": t, "uid": uid,
                "message": f"{gates[index]['code']} 档位超出范围",
            })
            continue
        if abs(gear - gears_now[index]) > 1:
            errors.append({
                "hour": t, "uid": uid,
                "message": (
                    f"{gates[index]['name']}（{gates[index]['code']}）第{t}小时从"
                    f"{gears_now[index]}档直接拉到{gear}档，违反逐档调节，已拒绝"
                ),
            })
            continue
        if gear != gears_now[index]:
            chosen[index] = gear
            notes.append(
                f"值班员手动：{gates[index]['name']}（{gates[index]['code']}）"
                f"{gears_now[index]}档→{gear}档"
            )
    flow = sum(gates[i]["gear_flows"][chosen[i]] for i in range(len(gates)))
    basis = [f"手动调度：值班员第{t}小时设定闸门档位，组合下泄{flow:.1f}m3/s"]
    if flow > cap:
        basis.append(f"手动下泄超过下游允许{cap:.1f}m3/s，下游河段将顶过安全流量")
    return chosen, basis, notes, (errors[0] if errors else None)


def _choose_combo(gates, gears_now, target, cap, emergency):
    """枚举逐档约束内全部档位组合，选最贴目标且尽量不顶托的一组。"""
    options = []
    for i, gate in enumerate(gates):
        low = max(0, gears_now[i] - 1)
        high = min(gate["max_gear"], gears_now[i] + 1)
        options.append(range(low, high + 1))
    best = None
    for combo in product(*options):
        flow = sum(gates[i]["gear_flows"][combo[i]] for i in range(len(gates)))
        movement = sum(abs(combo[i] - gears_now[i]) for i in range(len(gates)))
        # 超汛限后保坝优先，不再因下游安全流量压减下泄
        over_cap = 0.0 if emergency else max(0.0, flow - cap)
        score = round(over_cap, 4) * 10000.0 + abs(flow - target) + movement * 0.5
        if best is None or score < best[0]:
            best = (score, combo, flow)
    _, combo, flow = best
    notes = []
    for i, gate in enumerate(gates):
        if combo[i] != gears_now[i]:
            notes.append(
                f"{gate['name']}（{gate['code']}）{gears_now[i]}档→{combo[i]}档，"
                f"下泄{gate['gear_flows'][combo[i]]:.1f}m3/s"
            )
    if not notes:
        notes.append("各闸维持上一小时档位")
    return list(combo), flow, notes


def _route_level(curve, level, inflow_t, release):
    """逐时水量平衡：库容(万m3) += (入-出)m3/s × 3600s ÷ 10000。"""
    storage = storage_at_level(curve, level)
    storage += (inflow_t - release) * 0.36
    return level_at_storage(curve, storage)


def _extended_inflow(raw, reservoir, steps):
    series = list(raw["hydrograph"]["series"][reservoir["id"]])
    base = raw["hydrograph"]["base_flow"] * reservoir["inflow_scale"]
    while len(series) < steps:
        series.append(base)
    return [float(v) for v in series]


def _override_at(overrides, t):
    return [item for item in overrides if item["hour"] == t]


def _gate_index(gates, uid):
    for i, gate in enumerate(gates):
        if gate["uid"] == uid:
            return i
    return None
