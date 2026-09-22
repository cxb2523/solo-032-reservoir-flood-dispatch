"use strict";

window.LedgerBoard = (() => {
  const board = {
    1: { version: null, data: null, conflict: null },
    2: { version: null, data: null, conflict: null },
  };
  let reservoirId = "QFS";

  const $ = (id) => document.getElementById(id);

  async function api(url, options) {
    const resp = await fetch(url, options || {});
    return resp.json();
  }

  async function init() {
    selectReservoir(reservoirId);
  }

  async function selectReservoir(rid) {
    reservoirId = rid;
    board[1] = { version: null, data: null, conflict: null };
    board[2] = { version: null, data: null, conflict: null };
    await Promise.all([refresh(1), refresh(2)]);
  }

  async function refresh(op) {
    const data = await api(`/api/ledger/${reservoirId}`);
    board[op].data = data;
    board[op].version = data.version;
    board[op].conflict = null;
    render(op);
  }

  async function submit(op) {
    const changes = [];
    document.querySelectorAll(`#ledger-state-${op} select`).forEach((sel) => {
      const gear = Number(sel.value);
      const current = board[op].data.gears[sel.dataset.uid];
      if (gear !== current) changes.push({ uid: sel.dataset.uid, gear });
    });
    if (!changes.length) {
      renderMessage(op, "没有改动，无需提交", false);
      return;
    }
    const payload = {
      operator: op === 1 ? "值班员甲" : "值班员乙",
      base_version: board[op].version,
      changes,
    };
    const resp = await fetch(`/api/ledger/${reservoirId}/change`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await resp.json();
    if (resp.ok) {
      board[op].data = data;
      board[op].version = data.version;
      board[op].conflict = null;
      render(op);
      const other = op === 1 ? 2 : 1;
      if (board[other].version !== null && board[other].version !== data.version) {
        board[other].conflict =
          "另一名值班员已更新台账，你的页面是旧版本；请点「刷新最新台账」后再操作";
        render(other);
      }
    } else if (data.error === "version_conflict") {
      board[op].conflict = data.message;
      board[op].serverData = data.current;
      render(op);
    } else {
      board[op].conflict = data.message || "提交失败";
      render(op);
    }
  }

  function renderMessage(op, message, isError) {
    const el = document.createElement("div");
    el.className = isError ? "ledger-conflict" : "ledger-ok";
    el.textContent = message;
    const host = $(`ledger-state-${op}`);
    const old = host.querySelector(".ledger-ok, .ledger-conflict");
    if (old) old.replaceWith(el); else host.appendChild(el);
  }

  function render(op) {
    const state = board[op];
    const host = document.getElementById(`ledger-state-${op}`);
    if (!state.data) {
      host.innerHTML = "加载中…";
      return;
    }
    const data = state.data;
    let rows = "";
    for (const [uid, gear] of Object.entries(data.gears)) {
      const info = data.gate_info[uid];
      let opts = "";
      for (let g = 0; g <= info.max_gear; g++) {
        opts += `<option value="${g}" ${g === gear ? "selected" : ""}>${g}档</option>`;
      }
      rows += `<div class="ledger-row">
        <span class="gname">${info.name} <span style="color:var(--muted)">${info.code}</span></span>
        <span class="gear-badge ${gear ? "on" : ""}">${gear}</span>
        <select data-uid="${uid}">${opts}</select>
      </div>`;
    }
    host.innerHTML =
      `<div style="color:var(--muted);margin-bottom:6px">` +
      `水库：${data.reservoir_name}｜本页面基于台账版本 <strong>${state.version}</strong></div>` +
      rows +
      `<div style="margin-top:8px;display:flex;gap:8px">` +
      `<button class="ghost small" data-act="submit">提交调整</button>` +
      `<button class="ghost small" data-act="refresh">刷新最新台账</button></div>` +
      (state.conflict ? `<div class="ledger-conflict">⚠ ${state.conflict}</div>`
                      : `<div class="ledger-ok">版本一致，可以提交</div>`);

    host.querySelector('[data-act="submit"]').onclick = () => submit(op);
    host.querySelector('[data-act="refresh"]').onclick = () => refresh(op);

    const meta = $("ledger-meta");
    meta.textContent =
      `当前台账版本 ${data.version}｜规则：一次只能逐档调（±1档），后提交者版本落后会被拒绝`;
    renderLog(data);
  }

  function renderLog(data) {
    const host = $("ledger-log");
    host.innerHTML = data.history.slice().reverse().map((h) => {
      const detail = h.changes.map((c) =>
        `${c.name || c.code} ${c.from !== undefined ? c.from + "→" + c.to : "→" + c.to}档`
      ).join("，");
      const time = new Date(h.time);
      const p = (v) => String(v).padStart(2, "0");
      return `<div class="entry">${p(time.getMonth() + 1)}-${p(time.getDate())} ` +
        `${p(time.getHours())}:${p(time.getMinutes())}:${p(time.getSeconds())} ` +
        `[v${h.version}] ${h.operator}：${detail}${h.note ? "（" + h.note + "）" : ""}</div>`;
    }).join("");
  }

  return { init, selectReservoir };
})();
