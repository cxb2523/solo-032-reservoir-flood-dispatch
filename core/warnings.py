"""下游河道超安全流量判定与按传播时间倒推的预警发布时刻。"""

from __future__ import annotations

from datetime import timedelta

DEEP_NIGHT_START = 23
DEEP_NIGHT_END = 6


def reach_results(reaches, releases, local, safety_factor, start, steps):
    results = []
    for reach in reaches:
        safety = round(reach["safety_flow"] * safety_factor, 1)
        flows = [round(releases[t] + local[reach["id"]][t], 1) for t in range(steps)]
        exceeded = [flows[t] > safety + 1e-9 for t in range(steps)]
        exceed_hours = [t for t, flag in enumerate(exceeded) if flag]
        warning = None
        if exceed_hours:
            first = exceed_hours[0]
            travel = reach["travel_hours"]
            arrival = start + timedelta(hours=first)
            issue = arrival - timedelta(hours=travel)
            peak_flow = max(flows[t] for t in exceed_hours)
            warning = {
                "town": reach["town"],
                "reach_name": reach["name"],
                "safety_flow": safety,
                "base_safety_flow": reach["safety_flow"],
                "first_exceed_hour": first,
                "arrival_time": arrival.isoformat(),
                "travel_hours": travel,
                "issue_hour": first - travel,
                "issue_time": issue.isoformat(),
                "lead_hours": travel,
                "deep_night": _is_deep_night(issue),
                "max_flow": peak_flow,
                "exceed_periods": _merge_periods(exceed_hours),
                "message": (
                    f"{reach['town']}将于{_hm(arrival)}流量{flows[first]}m3/s超过安全流量"
                    f"{safety}m3/s，按传播时间{travel}h倒推，预警必须在"
                    f"{_hm(issue)}前发出（提前{travel}小时）；到了承受上限才发就晚了"
                ),
            }
        results.append({
            "id": reach["id"],
            "name": reach["name"],
            "town": reach["town"],
            "safety_flow": safety,
            "base_safety_flow": reach["safety_flow"],
            "travel_hours": reach["travel_hours"],
            "flows": flows,
            "exceeded": exceeded,
            "exceed_count": len(exceed_hours),
            "exceed_hours": exceed_hours,
            "warning": warning,
        })
    return results


def summarize(raw, reservoir, levels, releases, inflow, reach_data, steps):
    rain = raw["hydrograph"]["hours"]
    peak_inflow = max(inflow[:rain])
    peak_inflow_hour = inflow.index(peak_inflow)
    rain_levels = levels[:rain + 1]
    rain_releases = releases[:rain]
    max_level = max(rain_levels)
    max_level_hour = rain_levels.index(max_level)
    max_release = max(rain_releases)
    max_release_hour = rain_releases.index(max_release)
    exceed_total = sum(r["exceed_count"] for r in reach_data)
    over_limit_hours = sum(
        1 for t in range(rain) if levels[t] > reservoir["flood_limit_level"] + 1e-9
    )
    peak_cut = max(0.0, peak_inflow - max_release)
    peak_cut_ratio = round(peak_cut / peak_inflow * 100.0, 1) if peak_inflow else 0.0
    return {
        "max_level": round(max_level, 3),
        "max_level_hour": max_level_hour,
        "flood_limit_level": reservoir["flood_limit_level"],
        "level_over_limit": round(max_level - reservoir["flood_limit_level"], 3),
        "over_limit_hours": over_limit_hours,
        "peak_inflow": round(peak_inflow, 1),
        "peak_inflow_hour": peak_inflow_hour,
        "max_release": round(max_release, 1),
        "max_release_hour": max_release_hour,
        "peak_cut": round(peak_cut, 1),
        "peak_cut_ratio": peak_cut_ratio,
        "exceed_periods": exceed_total,
        "reach_exceed": {r["town"]: r["exceed_count"] for r in reach_data},
    }


def _merge_periods(hours):
    if not hours:
        return []
    periods = []
    start = prev = hours[0]
    for hour in hours[1:]:
        if hour == prev + 1:
            prev = hour
        else:
            periods.append([start, prev])
            start = prev = hour
    periods.append([start, prev])
    return periods


def _is_deep_night(moment):
    hour = moment.hour
    return hour >= DEEP_NIGHT_START or hour < DEEP_NIGHT_END


def _hm(moment):
    return moment.strftime("%m月%d日%H:%M")
