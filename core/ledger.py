"""实时闸门台账：两个值班员并发改闸时用版本号做乐观锁。

后提交的人若基于旧版本，直接 409 冲突，必须刷新看到对方改动后才能再改，
不允许各改各的互相覆盖。调档同样强制逐档（相对现状最多调一档）。
"""

from __future__ import annotations

from datetime import datetime, timezone
from threading import Lock


class LedgerError(Exception):
    def __init__(self, code: str, message: str, current=None):
        super().__init__(message)
        self.code = code
        self.current = current


class Ledger:
    def __init__(self, raw: dict):
        self._lock = Lock()
        self._states = {}
        for reservoir in raw["reservoirs"]:
            gears = {gate["uid"]: 0 for gate in reservoir["gates"]}
            gate_info = {
                gate["uid"]: {"code": gate["code"], "name": gate["name"],
                              "max_gear": gate["max_gear"]}
                for gate in reservoir["gates"]
            }
            self._states[reservoir["id"]] = {
                "reservoir_id": reservoir["id"],
                "reservoir_name": reservoir["name"],
                "version": 1,
                "gears": gears,
                "gate_info": gate_info,
                "history": [],
            }

    def snapshot(self) -> dict:
        with self._lock:
            return {"reservoirs": [self._public(state) for state in self._states.values()]}

    def reservoir(self, reservoir_id: str) -> dict:
        with self._lock:
            state = self._get(reservoir_id)
            return self._public(state)

    def change(self, reservoir_id: str, operator: str, base_version: int,
               changes: list[dict]) -> dict:
        operator = (operator or "匿名值班员").strip()
        with self._lock:
            state = self._get(reservoir_id)
            if int(base_version) != state["version"]:
                current = self._public(state)
                raise LedgerError(
                    "version_conflict",
                    (
                        f"台账已被其他值班员更新（你的版本{base_version}，"
                        f"当前版本{state['version']}）。请刷新查看最新闸门状态后再提交，"
                        "本次操作未生效"
                    ),
                    current,
                )
            if not changes:
                raise LedgerError("empty_change", "没有任何闸门调整")
            applied = []
            for item in changes:
                uid = item["uid"]
                if uid not in state["gears"]:
                    raise LedgerError("bad_gate", f"闸门 {uid} 不在 {state['reservoir_name']} 台账内")
                target = int(item["gear"])
                max_gear = state["gate_info"][uid]["max_gear"]
                if not 0 <= target <= max_gear:
                    raise LedgerError("bad_gear", f"{state['gate_info'][uid]['code']} 档位须在0-{max_gear}之间")
                current_gear = state["gears"][uid]
                if abs(target - current_gear) > 1:
                    raise LedgerError(
                        "gear_jump",
                        (
                            f"{state['gate_info'][uid]['name']}（{state['gate_info'][uid]['code']}）"
                            f"不能从{current_gear}档直接拉到{target}档，闸门只能逐档调节"
                        ),
                    )
                if target != current_gear:
                    applied.append({
                        "uid": uid,
                        "code": state["gate_info"][uid]["code"],
                        "name": state["gate_info"][uid]["name"],
                        "from": current_gear,
                        "to": target,
                    })
            if not applied:
                raise LedgerError("no_change", "提交的档位与现状一致，无实际调整")
            for item in applied:
                state["gears"][item["uid"]] = item["to"]
            state["version"] += 1
            record = {
                "version": state["version"],
                "time": datetime.now(timezone.utc).astimezone().isoformat(),
                "operator": operator,
                "changes": applied,
            }
            state["history"].append(record)
            return self._public(state)

    def reset(self, reservoir_id: str) -> dict:
        with self._lock:
            state = self._get(reservoir_id)
            for uid in state["gears"]:
                state["gears"][uid] = 0
            state["version"] += 1
            state["history"].append({
                "version": state["version"],
                "time": datetime.now(timezone.utc).astimezone().isoformat(),
                "operator": "系统",
                "changes": [{"uid": uid, "to": 0} for uid in state["gears"]],
                "note": "台账复位",
            })
            return self._public(state)

    def _get(self, reservoir_id: str) -> dict:
        if reservoir_id not in self._states:
            raise LedgerError("bad_reservoir", f"未知水库：{reservoir_id}")
        return self._states[reservoir_id]

    @staticmethod
    def _public(state: dict) -> dict:
        return {
            "reservoir_id": state["reservoir_id"],
            "reservoir_name": state["reservoir_name"],
            "version": state["version"],
            "gears": dict(state["gears"]),
            "gate_info": state["gate_info"],
            "history": list(state["history"][-20:]),
        }
