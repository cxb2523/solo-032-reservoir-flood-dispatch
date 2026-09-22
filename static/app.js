"use strict";

const state = {
  overview: null,
  reservoirId: "QFS",
  strategy: "A",
  safetyCut: false,
  result: null,
  hour: 0,
  manual: {},
};

const $ = (id) => document.getElementById(id);

async function getJson(url) {
  const resp = await fetch(url);
  return resp.json();
}

async function postJson(url, payload) {
  const resp = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return { ok: resp.ok, data: await resp.json() };
}

function fmtTime(iso) {
  const d = new Date(iso);
  const p = (v) => String(v).padStart(2, "0");
  return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

function strategyName(s) {
  return { A: "库容查表削峰", B: "涨速提前预泄", manual: "手动调度" }[s] || s;
}

async function init() {
  state.overview = await getJson("/api/overview");
  renderHeader();
  renderIssues();
  renderReservoirTabs();
  bindControls();
  await refreshSimulation();
  if (window.LedgerBoard) window.LedgerBoard.init();
}

function renderHeader() {
  const ev = state.overview.event;
  $("event-title").textContent =
    `${ev.name}｜${fmtTime(ev.start_time)} 起，历时${ev.duration_hours}h，第${ev.peak_hour}h洪峰`;
}

function renderIssues() {
  const issues = state.overview.data_issues || [];
  if (!issues.length) return;
  const bar = $("issue-bar");
  bar.hidden = false;
  bar.innerHTML = "<h3>台账脏数据提示（原始记录保留，计算前已处置）</h3><ul>" +
    issues.map((i) => `<li>${i.message}</li>`).join("") + "</ul>";
}

function renderReservoirTabs() {
  const box = $("reservoir-tabs");
  box.innerHTML = "";
  for (const res of state.overview.reservoirs) {
    const btn = document.createElement("button");
    btn.textContent = res.name;
    btn.dataset.id = res.id;
    if (res.id === state.reservoirId) btn.classList.add("active");
    btn.onclick = () => {
      state.reservoirId = res.id;
      state.hour = 0;
      document.querySelectorAll("#reservoir-tabs button").forEach((b) =>
        b.classList.toggle("active", b.dataset.id === res.id));
      refreshSimulation();
      if (window.LedgerBoard) window.LedgerBoard.selectReservoir(res.id);
    };
    box.appendChild(btn);
  }
}

function bindControls() {
  document.querySelectorAll("#strategy-switch button").forEach((btn) => {
    btn.onclick = () => {
      state.strategy = btn.dataset.strategy;
      document.querySelectorAll("#strategy-switch button").forEach((b) =>
        b.classList.toggle("active", b === btn));
      $("btn-clear-manual").hidden = state.strategy !== "manual";
      refreshSimulation();
    };
  });
  $("safety-cut").onchange = (e) => {
    state.safetyCut = e.target.checked;
    refreshSimulation();
  };
  $("btn-clear-manual").onclick = () => {
    state.manual[state.reservoirId] = [];
    refreshSimulation();
  };
  $("btn-selfcheck").onclick = openSelfcheck;
  $("modal-close").onclick = () => { $("modal").hidden = true; };
  $("modal").onclick = (e) => { if (e.target.id === "modal") $("modal").hidden = true; };

  const canvas = $("chart");
  canvas.onclick = (e) => {
    const rect = canvas.getBoundingClientRect();
    const x = (e.clientX - rect.left) * (canvas.width / rect.width);
    const padL = 56, padR = 56;
    const steps = state.result.rain_hours;
    const innerW = canvas.width - padL - padR;
    state.hour = Math.max(0, Math.min(steps - 1,
      Math.round((x - padL) / innerW * steps)));
    renderAll();
  };
}

async function refreshSimulation() {
  const payload = {
    reservoir_id: state.reservoirId,
    strategy: state.strategy === "manual" ? "A" : state.strategy,
    safety_factor: state.safetyCut ? 0.8 : 1.0,
  };
  if (state.strategy === "manual") {
    payload.overrides = state.manual[state.reservoirId] || [];
  }
  const { data } = await postJson("/api/simulate", payload);
  state.result = data;
  if (state.hour >= data.rain_hours) state.hour = 0;
  renderAll();
}

function reservoirMeta() {
  return state.overview.reservoirs.find((r) => r.id === state.reservoirId);
}

function renderAll() {
  renderKpis();
  drawChart();
  renderGateTable();
  renderReachTable();
  renderBasis();
}

function renderKpis() {
  const r = state.result;
  const s = r.summary;
  const cutLabel = state.safetyCut ? "（安全流量8折）" : "";
  const overLimit = s.max_level > s.flood_limit_level + 1e-9;
  const cards = [
    { label: `最高水位 m ${cutLabel}`, value: s.max_level.toFixed(2),
      sub: `第${s.max_level_hour}h｜汛限${s.flood_limit_level}`,
      cls: overLimit ? "warn" : "good" },
    { label: "最大下泄流量 m³/s", value: s.max_release.toFixed(0),
      sub: `第${s.max_release_hour}h`, cls: "" },
    { label: "洪峰入库 m³/s", value: s.peak_inflow.toFixed(0),
      sub: `第${s.peak_inflow_hour}h`, cls: "" },
    { label: "削掉的峰值", value: `${s.peak_cut.toFixed(0)} m³/s`,
      sub: `削峰率 ${s.peak_cut_ratio}%`, cls: "good" },
    { label: "下游超安全流量时段数", value: String(s.exceed_periods),
      sub: Object.entries(s.reach_exceed).map(([k, v]) => `${k}:${v}`).join(" "),
      cls: s.exceed_periods ? "warn" : "good" },
    { label: "超汛限时长", value: `${s.over_limit_hours} h`,
      sub: strategyName(r.strategy), cls: s.over_limit_hours ? "warn" : "good" },
  ];
  $("kpi-cards").innerHTML = cards.map((c) =>
    `<div class="kpi ${c.cls}"><div class="label">${c.label}</div>` +
    `<div class="value">${c.value}</div><div class="sub">${c.sub}</div></div>`
  ).join("");
}

function drawChart() {
  const canvas = $("chart");
  const ctx = canvas.getContext("2d");
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  const r = state.result;
  const n = r.rain_hours;
  const padL = 56, padR = 56, padT = 18, padB = 40;
  const plotW = W - padL - padR, plotH = H - padT - padB;

  const fMax = Math.max(...r.inflow.slice(0, n), ...r.releases.slice(0, n)) * 1.08;
  const lvls = r.levels.slice(0, n + 1);
  const lMin = Math.min(...lvls, r.flood_limit_level) - 0.4;
  const lMax = Math.max(...lvls, r.flood_limit_level) + 0.4;

  const xAt = (i) => padL + (i / n) * plotW;
  const yFlow = (v) => padT + plotH - (v / fMax) * plotH;
  const yLevel = (v) => padT + plotH - ((v - lMin) / (lMax - lMin)) * plotH;

  ctx.strokeStyle = "#223247";
  ctx.fillStyle = "#8da2bd";
  ctx.font = "11px sans-serif";
  ctx.lineWidth = 1;
  for (let i = 0; i <= 6; i++) {
    const y = padT + (i / 6) * plotH;
    ctx.beginPath(); ctx.moveTo(padL, y); ctx.lineTo(W - padR, y); ctx.stroke();
    ctx.fillText(`${(fMax * (1 - i / 6)).toFixed(0)}`, 8, y + 3);
    ctx.fillText(`${(lMax - (i / 6) * (lMax - lMin)).toFixed(1)}`, W - padR + 6, y + 3);
  }
  for (let h = 0; h <= n; h += 4) {
    ctx.fillText(`${h}h`, xAt(h) - 8, H - 16);
  }

  const yLim = yLevel(r.flood_limit_level);
  ctx.fillStyle = "rgba(255,107,107,0.07)";
  ctx.fillRect(padL, padT, plotW, yLim - padT);

  ctx.setLineDash([6, 5]);
  ctx.strokeStyle = "#ff6b6b";
  ctx.beginPath(); ctx.moveTo(padL, yLim); ctx.lineTo(W - padR, yLim); ctx.stroke();
  ctx.setLineDash([]);
  ctx.fillStyle = "#ff6b6b";
  ctx.fillText(`汛限 ${r.flood_limit_level}m`, W - padR - 92, yLim - 5);

  const line = (values, color, width) => {
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = xAt(i), y = yFlow(v);
      if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
    });
    ctx.stroke();
  };
  line(r.inflow.slice(0, n), "#4aa3ff", 2);
  line(r.releases.slice(0, n), "#f5b945", 2);

  ctx.strokeStyle = "#35d0c0";
  ctx.lineWidth = 2.2;
  ctx.beginPath();
  r.levels.slice(0, n + 1).forEach((v, i) => {
    const x = xAt(i), y = yLevel(v);
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();

  const peakH = r.peak_hour;
  const px = xAt(peakH), py = yFlow(r.inflow[peakH]);
  ctx.fillStyle = "#b48cf0";
  ctx.beginPath(); ctx.arc(px, py, 4, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#d9c8f7";
  ctx.fillText(`洪峰 ${r.inflow[peakH]}m³/s`, px - 34, py - 10);

  r.rows.slice(0, n).forEach((row) => {
    if (row.over_segments.length) {
      ctx.fillStyle = "rgba(255,107,107,0.55)";
      ctx.fillRect(xAt(row.hour), H - 26, plotW / n - 1, 6);
    }
  });

  const sx = xAt(state.hour);
  ctx.strokeStyle = "rgba(220,231,245,0.55)";
  ctx.setLineDash([3, 3]);
  ctx.beginPath(); ctx.moveTo(sx, padT); ctx.lineTo(sx, padT + plotH); ctx.stroke();
  ctx.setLineDash([]);
  const sel = r.rows[state.hour];
  ctx.fillStyle = "#dce7f5";
  ctx.fillText(`第${state.hour}h`, sx - 10, padT + 12);
  ctx.fillStyle = "#4aa3ff";
  ctx.beginPath(); ctx.arc(sx, yFlow(sel.inflow), 3.5, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#f5b945";
  ctx.beginPath(); ctx.arc(sx, yFlow(sel.release), 3.5, 0, Math.PI * 2); ctx.fill();
}

function renderGateTable() {
  const r = state.result;
  $("selected-hour").textContent = String(state.hour);
  const row = r.rows[state.hour];
  const prevRow = state.hour > 0 ? r.rows[state.hour - 1] : null;
  const meta = reservoirMeta();
  const tbody = $("gate-tbody");
  tbody.innerHTML = "";

  for (const gate of r.gates) {
    const gear = row.gears[gate.uid];
    const prevGear = prevRow ? prevRow.gears[gate.uid] : 0;
    const dupIssue = meta.issues.find((i) =>
      i.type === "duplicate_gate_code" &&
      (i.duplicate_uid === gate.uid || i.first_uid === gate.uid));
    const tr = document.createElement("tr");
    let options = "";
    for (let g = 0; g <= gate.max_gear; g++) {
      const disabled = state.strategy === "manual" && Math.abs(g - prevGear) > 1;
      options += `<option value="${g}" ${g === gear ? "selected" : ""} ${
        disabled ? "disabled" : ""}>${g}档 · ${gate.gear_flows[g]}m³/s</option>`;
    }
    tr.innerHTML =
      `<td>${gate.name}</td>` +
      `<td>${gate.code}${dupIssue ? '<span class="dup-tag" title="台账重号">重号</span>' : ""}` +
      `<div style="color:var(--muted)">${gate.uid}</div></td>` +
      `<td><span class="gear-badge ${gear ? "on" : ""}">${gear}</span></td>` +
      `<td>${gate.gear_flows[gear]}</td>` +
      `<td><select class="gear-select" data-uid="${gate.uid}" ${
        state.strategy !== "manual" ? "disabled" : ""}>${options}</select></td>`;
    tbody.appendChild(tr);
  }

  if (state.strategy !== "manual") {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="5" style="color:var(--muted)">当前为自动调度口径，切到「手动调度」后可逐档拖闸重算</td>`;
    tbody.appendChild(tr);
  } else if (state.hour === 0) {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td colspan="5" style="color:var(--muted)">第0小时初始全关，从第1小时起可逐档调节</td>`;
    tbody.appendChild(tr);
  }

  tbody.querySelectorAll("select.gear-select").forEach((sel) => {
    sel.onchange = async (e) => {
      const uid = e.target.dataset.uid;
      const gear = Number(e.target.value);
      const list = state.manual[state.reservoirId] || [];
      const filtered = list.filter((o) => !(o.hour === state.hour && o.uid === uid));
      filtered.push({ hour: state.hour, uid, gear });
      state.manual[state.reservoirId] = filtered;
      await refreshSimulation();
      const errs = state.result.override_errors || [];
      if (errs.length) toast(errs[0].message, true);
    };
  });
}

function renderReachTable() {
  const r = state.result;
  const tbody = $("reach-tbody");
  tbody.innerHTML = "";
  for (const reach of r.reaches) {
    const maxFlow = Math.max(...reach.flows.slice(0, r.rain_hours));
    const danger = reach.exceed_hours.some((h) => h < r.rain_hours);
    let warnCell;
    if (reach.warning) {
      const w = reach.warning;
      const deep = w.deep_night ? '<span class="warn-pill deep">深夜照发</span>' : "";
      warnCell =
        `<span class="warn-pill">${fmtTime(w.issue_time)} 发（提前${w.lead_hours}h）</span> ` +
        `${deep}<div style="color:var(--muted);margin-top:2px">${w.message}</div>`;
    } else {
      warnCell = '<span class="warn-none">全程未超，无需预警</span>';
    }
    const tr = document.createElement("tr");
    if (danger) tr.classList.add("danger-row");
    tr.innerHTML =
      `<td>${reach.town}<div style="color:var(--muted)">${reach.name}</div></td>` +
      `<td>${reach.safety_flow}${state.safetyCut ? ' <span style="color:var(--amber)">8折</span>' : ""}</td>` +
      `<td class="${danger ? "danger" : ""}">${maxFlow.toFixed(1)}</td>` +
      `<td class="${danger ? "danger" : ""}">${
        danger ? `${reach.exceed_hours.filter((h) => h < r.rain_hours).join(", ")} 时` : "0"
      }</td>` +
      `<td>${warnCell}</td>`;
    tbody.appendChild(tr);
  }
}

function renderBasis() {
  const row = state.result.rows[state.hour];
  $("basis-list").innerHTML =
    row.basis.map((b) => `<li>${b}</li>`).join("") +
    row.gate_changes.map((g) => `<li style="color:var(--muted)">${g}</li>`).join("") +
    (row.over_segments.length
      ? `<li style="color:var(--red)">本时段下游顶过安全流量：${row.over_segments.join("、")}</li>`
      : "");
}

function toast(message, isError) {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast" + (isError ? " error" : "");
  el.hidden = false;
  clearTimeout(toast._timer);
  toast._timer = setTimeout(() => { el.hidden = true; }, 4200);
}

async function openSelfcheck() {
  $("modal").hidden = false;
  $("modal-title").textContent = "交付前自检";
  $("modal-body").innerHTML = "<p>正在跑三条验收口径…</p>";
  const report = await getJson("/api/selfcheck");
  const html = report.checks.map((c) => `
    <div class="check-card ${c.passed ? "pass" : "fail"}">
      <strong>${c.passed ? "✅" : "❌"} ${c.name}</strong>
      <pre>${JSON.stringify(c.detail, null, 2)}</pre>
    </div>`).join("") +
    `<div class="check-card"><strong>三库两套口径汇总对比</strong><pre>${
      JSON.stringify(report.overview, null, 2)}</pre></div>` +
    `<p>总结论：<strong style="color:${report.passed ? "var(--green)" : "var(--red)"}">${
      report.passed ? "全部通过" : "存在未通过项"}</strong></p>`;
  $("modal-body").innerHTML = html;
}

init();
