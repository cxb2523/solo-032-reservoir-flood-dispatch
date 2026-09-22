"""生成 data/reservoir.json：三座水库台账、库容曲线、下游河道与洪水过程线。

只依赖标准库，重复执行结果必须完全一致（不用随机数）。
脏数据（按题目要求保留在文件里，由调度引擎加载时识别并提示）：
  1. 碧龙水库 G-03 闸门编号出现两次；
  2. 松鸣水库库容曲线 85.0m 那档库容写反，破坏单调性。
"""

import json
import os


HOURS = 36
PEAK_HOUR = 14
BASE_FLOW = 180.0
PEAK_FLOW = 1800.0


def inflow_series(scale: float) -> list[float]:
    """一条单峰入流过程线（单位 m3/s），峰形平滑，时长 36h。"""
    values = []
    for hour in range(HOURS):
        if hour <= PEAK_HOUR:
            ratio = hour / PEAK_HOUR
            flow = BASE_FLOW + (PEAK_FLOW - BASE_FLOW) * (ratio ** 1.7)
        else:
            ratio = (hour - PEAK_HOUR) / (HOURS - PEAK_HOUR)
            flow = BASE_FLOW + (PEAK_FLOW - BASE_FLOW) * max(0.0, 1.0 - ratio) ** 2.2
        values.append(round(flow * scale, 1))
    return values


def storage_curve(z0: float, z1: float, a: float, b: float, c: float,
                  bad_level: float | None = None, bad_value: float | None = None):
    """半米一档的水位-库容曲线，库容单位 万 m3，二次式拟合。"""
    curve = []
    level = z0
    while round(level, 2) <= z1 + 1e-9:
        storage = round(a * level * level + b * level + c, 1)
        if bad_level is not None and abs(level - bad_level) < 1e-9:
            storage = bad_value
        curve.append({"level": round(level, 2), "storage": storage})
        level += 0.5
    return curve


def gate(code: str, name: str, max_flow: float):
    """五档开度：0、25%、50%、75%、100%，逐档对应下泄流量。"""
    return {
        "code": code,
        "name": name,
        "openings": [
            {"gear": 0, "label": "全关", "flow": 0.0},
            {"gear": 1, "label": "25%", "flow": round(max_flow * 0.25, 1)},
            {"gear": 2, "label": "50%", "flow": round(max_flow * 0.5, 1)},
            {"gear": 3, "label": "75%", "flow": round(max_flow * 0.75, 1)},
            {"gear": 4, "label": "100%", "flow": round(max_flow, 1)},
        ],
    }


def build() -> dict:
    reservoirs = [
        {
            "id": "QFS",
            "name": "青峰水库",
            "flood_limit_level": 110.0,
            "current_level": 109.5,
            "inflow_scale": 1.0,
            "curve": storage_curve(100.0, 112.0, 280.0, -50400.0, 2343600.0),
            "gates": [
                gate("G-01", "左岸溢洪道闸", 200.0),
                gate("G-02", "主河床闸", 220.0),
                gate("G-03", "右岸泄洪洞闸", 180.0),
                gate("G-04", "非常溢洪道闸", 160.0),
                gate("G-05", "发电引水闸", 140.0),
            ],
        },
        {
            "id": "BLW",
            "name": "碧龙水库",
            "flood_limit_level": 96.0,
            "current_level": 95.5,
            "inflow_scale": 0.62,
            "curve": storage_curve(86.0, 98.0, 147.0, -23520.0, 971792.5),
            "gates": [
                gate("G-01", "主溢洪道1号闸", 170.0),
                gate("G-02", "主溢洪道2号闸", 170.0),
                gate("G-03", "左岸泄洪闸", 130.0),
                gate("G-03", "右岸泄洪闸", 130.0),
                gate("G-05", "底孔冲沙闸", 100.0),
            ],
        },
        {
            "id": "SMY",
            "name": "松鸣水库",
            "flood_limit_level": 84.0,
            "current_level": 83.5,
            "inflow_scale": 0.4,
            # 85.0m 这一档库容被写反（比 84.5m 还小），引擎需识别并提示
            "curve": storage_curve(74.0, 86.0, 94.5, -13230.0, 480012.75,
                                   bad_level=85.0, bad_value=52000.0),
            "gates": [
                gate("G-01", "溢洪道1号闸", 120.0),
                gate("G-02", "溢洪道2号闸", 120.0),
                gate("G-03", "左岸泄洪闸", 100.0),
                gate("G-04", "右岸泄洪闸", 80.0),
                gate("G-05", "放空底孔闸", 80.0),
            ],
        },
    ]

    # 每座水库各自一条独立来水过程线（按入库尺度缩放），峰时相同
    hydrograph = {
        "hours": HOURS,
        "peak_hour": PEAK_HOUR,
        "base_flow": BASE_FLOW,
        "peak_flow": PEAK_FLOW,
        "series": {res["id"]: inflow_series(res["inflow_scale"]) for res in reservoirs},
    }

    reaches = [
        {
            "id": "R1",
            "name": "青峰大坝—柳湾镇",
            "reservoir_id": "QFS",
            "town": "柳湾镇",
            "safety_flow": 700.0,
            "travel_hours": 4,
            "local_factor": 0.18,
        },
        {
            "id": "R2",
            "name": "柳湾镇—东埠村",
            "reservoir_id": "QFS",
            "town": "东埠村",
            "safety_flow": 650.0,
            "travel_hours": 6,
            "local_factor": 0.22,
        },
        {
            "id": "R3",
            "name": "碧龙大坝—白沙镇",
            "reservoir_id": "BLW",
            "town": "白沙镇",
            "safety_flow": 520.0,
            "travel_hours": 3,
            "local_factor": 0.16,
        },
        {
            "id": "R4",
            "name": "松鸣大坝—清溪村",
            "reservoir_id": "SMY",
            "town": "清溪村",
            "safety_flow": 360.0,
            "travel_hours": 5,
            "local_factor": 0.14,
        },
    ]

    return {
        "event": {
            "name": "2026年7月中旬流域性大洪水",
            "start_time": "2026-07-16T06:00:00+08:00",
            "duration_hours": HOURS,
            "peak_hour": PEAK_HOUR,
            "description": "全流域36小时强降雨，第14小时入库洪峰，洪峰流量见各库过程线。",
        },
        "hydrograph": hydrograph,
        "reservoirs": reservoirs,
        "reaches": reaches,
    }


def main() -> None:
    path = os.path.join(os.path.dirname(__file__), "..", "data", "reservoir.json")
    path = os.path.normpath(path)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(build(), fh, ensure_ascii=False, indent=2)
    print(f"written: {path}")


if __name__ == "__main__":
    main()
