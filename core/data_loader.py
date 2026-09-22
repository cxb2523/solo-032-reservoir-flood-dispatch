"""台账加载与脏数据识别。

两处脏数据都不改原文件，只在加载结果里打 issues：
  * 同一水库内闸门编号重复：保留全部闸门，补内部 uid 供程序区分；
  * 库容曲线非单调（某档写反）：用相邻有效档位线性插值修复后参与计算。
"""

from __future__ import annotations

import json
import os


DEFAULT_DATA_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "data", "reservoir.json")
)


def load(path: str | None = None) -> dict:
    with open(path or DEFAULT_DATA_PATH, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return normalize(raw)


def normalize(raw: dict) -> dict:
    issues: list[dict] = []
    for reservoir in raw.get("reservoirs", []):
        reservoir["issues"] = []
        _normalize_gates(reservoir, issues)
        _normalize_curve(reservoir, issues)
    raw["data_issues"] = issues
    return raw


def _normalize_gates(reservoir: dict, issues: list[dict]) -> None:
    seen: dict[str, int] = {}
    for index, gate in enumerate(reservoir["gates"]):
        code = gate["code"]
        gate["uid"] = f"{reservoir['id']}-{index + 1:02d}"
        gate["max_gear"] = max(item["gear"] for item in gate["openings"])
        flows = [round(item["flow"], 3) for item in gate["openings"]]
        gate["gear_flows"] = flows
        if code in seen:
            issue = {
                "type": "duplicate_gate_code",
                "reservoir_id": reservoir["id"],
                "reservoir_name": reservoir["name"],
                "gate_code": code,
                "first_uid": reservoir["gates"][seen[code]]["uid"],
                "duplicate_uid": gate["uid"],
                "message": (
                    f"{reservoir['name']}闸门编号 {code} 重复（台账第{seen[code] + 1}、"
                    f"{index + 1}扇），已用内部编号 {gate['uid']} 区分，请尽快核实台账"
                ),
            }
            reservoir["issues"].append(issue)
            issues.append(issue)
        else:
            seen[code] = index


def _normalize_curve(reservoir: dict, issues: list[dict]) -> None:
    curve = reservoir["curve"]
    curve.sort(key=lambda item: item["level"])
    # 单档写反会连累相邻档看起来也不单调，所以修一个、重扫一遍，直到曲线单调
    repaired_indexes: set[int] = set()
    while True:
        bad_index = None
        for index in range(1, len(curve) - 1):
            prev_storage = curve[index - 1]["storage"]
            storage = curve[index]["storage"]
            next_storage = curve[index + 1]["storage"]
            if not (prev_storage < storage < next_storage):
                bad_index = index
                break
        if bad_index is None:
            break
        level = curve[bad_index]["level"]
        original = curve[bad_index].get("storage_original",
                                        curve[bad_index]["storage"])
        repaired = round(
            (curve[bad_index - 1]["storage"] + curve[bad_index + 1]["storage"])
            / 2.0, 1
        )
        curve[bad_index]["storage"] = repaired
        if bad_index not in repaired_indexes:
            curve[bad_index]["storage_original"] = original
            curve[bad_index]["repaired"] = True
            repaired_indexes.add(bad_index)
            issue = {
                "type": "curve_out_of_order",
                "reservoir_id": reservoir["id"],
                "reservoir_name": reservoir["name"],
                "level": level,
                "original_storage": original,
                "repaired_storage": repaired,
                "message": (
                    f"{reservoir['name']}库容曲线 {level}m 一档库容写反"
                    f"（原值{original}万m3），已按相邻档位插值为{repaired}万m3参与计算，"
                    "请核对台账"
                ),
            }
            reservoir["issues"].append(issue)
            issues.append(issue)


def hydrograph_for(raw: dict, reservoir_id: str) -> list[float]:
    return list(raw["hydrograph"]["series"][reservoir_id])


def reservoir(raw: dict, reservoir_id: str) -> dict:
    for item in raw["reservoirs"]:
        if item["id"] == reservoir_id:
            return item
    raise KeyError(f"unknown reservoir: {reservoir_id}")


def reaches_for(raw: dict, reservoir_id: str) -> list[dict]:
    return [item for item in raw["reaches"] if item["reservoir_id"] == reservoir_id]
