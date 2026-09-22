"""水位-库容曲线查表：半米一档，档间线性插值。"""

from __future__ import annotations


def storage_at_level(curve: list[dict], level: float) -> float:
    """按水位查库容（万 m3），超出曲线范围按端点线性外推。"""
    levels = [item["level"] for item in curve]
    storages = [item["storage"] for item in curve]
    return _interpolate(levels, storages, level)


def level_at_storage(curve: list[dict], storage: float) -> float:
    """按库容反查水位（m），超出范围按端点线性外推。"""
    levels = [item["level"] for item in curve]
    storages = [item["storage"] for item in curve]
    return _interpolate(storages, levels, storage)


def _interpolate(xs: list[float], ys: list[float], x: float) -> float:
    if x <= xs[0]:
        if xs[1] == xs[0]:
            return ys[0]
        slope = (ys[1] - ys[0]) / (xs[1] - xs[0])
        return ys[0] + slope * (x - xs[0])
    if x >= xs[-1]:
        slope = (ys[-1] - ys[-2]) / (xs[-1] - xs[-2])
        return ys[-1] + slope * (x - xs[-1])
    for index in range(1, len(xs)):
        if x <= xs[index]:
            span = xs[index] - xs[index - 1]
            ratio = (x - xs[index - 1]) / span if span else 0.0
            return ys[index - 1] + ratio * (ys[index] - ys[index - 1])
    return ys[-1]
