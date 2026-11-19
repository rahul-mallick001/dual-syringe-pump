/* Front-end for the Dual Syringe Pump controller. */

const FLOW_UNITS = [
  { value: 0, label: "ul/hr" },
  { value: 1, label: "ul/min" },
  { value: 2, label: "ml/hr" },
  { value: 3, label: "ml/min" },
];

const DURATION_TYPES = [
  { value: 0, label: "Duration" },
  { value: 1, label: "T_Volume" },
];

const DURATION_UNITS = [
  { value: 0, label: "Second" },
  { value: 1, label: "Minute" },
  { value: 2, label: "Hour" },
];

const VOLUME_UNITS = [
  { value: 0, label: "Microliter" },
  { value: 1, label: "Mililiter" },
];

const el = (id) => document.getElementById(id);

let state = null;
let currentStepIndex = 0;
let currentTab = "dualMode";

function fillSelect(select, items) {
  if (!select) return;
  const previous = select.value;
  select.innerHTML = "";
  for (const item of items) {
    const option = document.createElement("option");
    option.value = String(item.value);
    option.textContent = item.label;
    select.appendChild(option);
  }
  if (previous !== "" && items.some((i) => String(i.value) === previous)) {
    select.value = previous;
  }
}

async function api(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    throw new Error(payload.detail || payload.message || `HTTP ${response.status}`);
  }
  return payload;
}

async function refreshPorts() {
  try {
    const data = await (await fetch("/api/ports")).json();
    const select = el("portSelect");
    const previous = select.value;
    select.innerHTML = "";
    for (const port of data.ports) {
      const option = document.createElement("option");
      option.value = port.device;
      option.textContent = port.device + (port.description ? ` — ${port.description}` : "");
      select.appendChild(option);
    }
    if (previous) select.value = previous;
    if (!select.value && select.options.length > 0) select.selectedIndex = 0;
  } catch (error) {
    console.error("Failed to query ports:", error);
  }
}

function updateSelectUnits(typeSelect, unitSelect, currentValue) {
  if (!typeSelect || !unitSelect) return;
  const isDuration = Number(typeSelect.value) === 0;
  const items = isDuration ? DURATION_UNITS : VOLUME_UNITS;
  const kind = isDuration ? "duration" : "volume";
  if (unitSelect.dataset.kind !== kind) {
    fillSelect(unitSelect, items);
    unitSelect.dataset.kind = kind;
  }
  if (currentValue !== undefined && currentValue !== null) {
    unitSelect.value = String(currentValue);
  }
}

function renderPill(pillEl, status) {
  if (!pillEl) return;
  pillEl.textContent = status || "Ready";
  pillEl.className = "status-pill";
  const s = (status || "").toLowerCase();
  if (s.includes("moving") || s.includes("started")) {
    pillEl.classList.add("moving");
  } else if (s.includes("completed") || s.includes("reached")) {
    pillEl.classList.add("completed");
  } else if (s.includes("limit") || s.includes("error") || s.includes("fault") || s.includes("terminated")) {
    pillEl.classList.add("fault");
  }
}

function render(next) {
  state = next;
  const active = document.activeElement;
  const busy = Boolean(next.running || next.homing);

  // Status Text & Dots
  const statusText = el("statusText");
  const statusDot = el("statusDot");
  statusText.textContent = next.error ? `${next.status}: ${next.error}` : next.status;

  if (next.error) {
    statusText.className = "err";
    statusDot.className = "status-indicator-dot err";
  } else if (busy) {
    statusText.className = "busy";
    statusDot.className = "status-indicator-dot busy";
  } else {
    statusText.className = "ok";
    statusDot.className = "status-indicator-dot ok";
  }

  // COM Port info
  el("comText").textContent = next.connected ? `${next.port} (19200)` : "Disconnected";
  el("connectBtn").textContent = next.connected ? "Disconnect" : "Connect";
  el("portSelect").disabled = next.connected;

  // Global button states
  el("runBtn").disabled = busy || Boolean(next.fault) || !next.connected;
  el("stopBtn").disabled = false;
  el("homeAllBtn").disabled = busy || !next.connected;
  el("homeBtn").disabled = busy || !next.connected;
  el("home2Btn").disabled = busy || !next.connected;
  el("withdrawalBtn").disabled = busy;

  // Direction toggle display
  const isWithdrawal = Boolean(next.withdrawal);
  el("withdrawalBtn").classList.toggle("active", isWithdrawal);
  el("dirCaption").textContent = isWithdrawal ? "WITHDRAWAL" : "INFUSION";
  el("dirIcon").textContent = isWithdrawal ? "↩" : "↪";

  // Fault latch
  const faultBar = el("faultBar");
  if (next.fault) {
    faultBar.classList.remove("hidden");
    el("faultText").textContent = next.fault;
  } else {
    faultBar.classList.add("hidden");
  }

  // At home checkboxes
  el("atHomeBox").checked = Boolean(next.atHome);
  el("atHomeBox").disabled = busy;
  el("atHomeBox2").checked = Boolean(next.atHome2);
  el("atHomeBox2").disabled = busy;

  // ----------------------------------------------------
  // Render DUAL PUMP (Racks A & B)
  // ----------------------------------------------------
  const rackA = next.racks?.A || {};
  const rackB = next.racks?.B || {};

  // Pump 1 (Rack A)
  el("pumpCardA").classList.toggle("disabled", !rackA.enabled);
  el("pumpA_enabled").checked = Boolean(rackA.enabled);
  el("pumpA_enabled").disabled = busy;
  renderPill(el("pumpA_status"), rackA.status);

  if (active !== el("pumpA_diameter")) el("pumpA_diameter").value = rackA.diameter ?? 14.5;
  if (active !== el("pumpA_flowRate")) el("pumpA_flowRate").value = rackA.flowRate ?? 1.0;
  if (active !== el("pumpA_flowUnit")) el("pumpA_flowUnit").value = String(rackA.flowRateUnit ?? 3);
  if (active !== el("pumpA_durationType")) el("pumpA_durationType").value = String(rackA.durationType ?? 0);
  if (active !== el("pumpA_duration")) el("pumpA_duration").value = rackA.duration ?? 60.0;

  const unitValA = rackA.durationType === 0 ? rackA.durationUnit : rackA.volumeUnit;
  if (active !== el("pumpA_durationUnit")) {
    updateSelectUnits(el("pumpA_durationType"), el("pumpA_durationUnit"), unitValA);
  }

  el("pumpA_remaining").textContent = rackA.remainingHMS ?? "00 : 00 : 00";
  el("pumpA_calcDuration").textContent = `${rackA.calDuration ?? 0} s`;

  // Pump 2 (Rack B)
  el("pumpCardB").classList.toggle("disabled", !rackB.enabled);
  el("pumpB_enabled").checked = Boolean(rackB.enabled);
  el("pumpB_enabled").disabled = busy;
  renderPill(el("pumpB_status"), rackB.status);

  if (active !== el("pumpB_diameter")) el("pumpB_diameter").value = rackB.diameter ?? 14.5;
  if (active !== el("pumpB_flowRate")) el("pumpB_flowRate").value = rackB.flowRate ?? 1.0;
  if (active !== el("pumpB_flowUnit")) el("pumpB_flowUnit").value = String(rackB.flowRateUnit ?? 3);
  if (active !== el("pumpB_durationType")) el("pumpB_durationType").value = String(rackB.durationType ?? 0);
  if (active !== el("pumpB_duration")) el("pumpB_duration").value = rackB.duration ?? 60.0;

  const unitValB = rackB.durationType === 0 ? rackB.durationUnit : rackB.volumeUnit;
  if (active !== el("pumpB_durationUnit")) {
    updateSelectUnits(el("pumpB_durationType"), el("pumpB_durationUnit"), unitValB);
  }

  el("pumpB_remaining").textContent = rackB.remainingHMS ?? "00 : 00 : 00";
  el("pumpB_calcDuration").textContent = `${rackB.calDuration ?? 0} s`;

  // Hardware Status Bar Strip
  el("rackDotA").classList.toggle("on", Boolean(next.connected));
  el("rackDotB").classList.toggle("on", Boolean(next.connected));
  el("rackAHome").textContent = next.atHome ? "Home (0x32)" : "Away";
  el("rackAHome").className = next.atHome ? "yes" : "";
  el("rackBHome").textContent = next.atHome2 ? "Home (0x32)" : "Away";
  el("rackBHome").className = next.atHome2 ? "yes" : "";
  el("rackAStatusBadge").textContent = rackA.status || "Ready";
  el("rackBStatusBadge").textContent = rackB.status || "Ready";

  // Disable inputs while moving
  const formInputs = [
    "pumpA_diameter", "pumpA_flowRate", "pumpA_flowUnit", "pumpA_durationType", "pumpA_duration", "pumpA_durationUnit",
    "pumpB_diameter", "pumpB_flowRate", "pumpB_flowUnit", "pumpB_durationType", "pumpB_duration", "pumpB_durationUnit",
    "diameter", "flowRate", "flowUnit", "durationType", "duration", "durationUnit", "programStep", "selectAll",
  ];
  for (const id of formInputs) {
    const element = el(id);
    if (element) element.disabled = busy;
  }
  document.querySelectorAll(".stepcheck").forEach((box) => { box.disabled = busy; });

  // ----------------------------------------------------
  // Render MULTI-STEP PROGRAM (Sequential)
  // ----------------------------------------------------
  el("stepRemaining").textContent = next.stepRemaining ?? "00 : 00 : 00";
  el("totalRemaining").textContent = next.totalRemaining ?? "00 : 00 : 00";

  const boxes = document.querySelectorAll(".stepcheck");
  boxes.forEach((box, index) => {
    box.checked = Boolean(next.selected?.[index]);
  });
  const selectedCount = (next.selected || []).filter(Boolean).length;
  el("selectAll").checked = selectedCount === (next.selected || []).length && selectedCount > 0;

  const steps = next.steps || [];
  if (steps.length > 0) {
    if (currentStepIndex >= steps.length) currentStepIndex = 0;
    el("programStep").value = String(currentStepIndex);
    const step = steps[currentStepIndex];
    if (step) {
      if (active !== el("diameter")) el("diameter").value = step.diameter ?? 14.5;
      if (active !== el("flowRate")) el("flowRate").value = step.flowRate ?? 1.0;
      if (active !== el("duration")) el("duration").value = step.duration ?? 60.0;
      if (active !== el("flowUnit")) el("flowUnit").value = String(step.flowRateUnit ?? 3);
      if (active !== el("durationType")) el("durationType").value = String(step.durationType ?? 0);

      const stepUnitVal = step.durationType === 0 ? step.durationUnit : step.volumeUnit;
      if (active !== el("durationUnit")) {
        updateSelectUnits(el("durationType"), el("durationUnit"), stepUnitVal);
      }
      if (el("stepCalcDuration")) {
        el("stepCalcDuration").textContent = `${step.calDuration ?? 0} s`;
      }
    }
  }
}

async function poll() {
  try {
    const response = await fetch("/api/state");
    if (response.ok) render(await response.json());
  } catch (error) {
    el("statusText").textContent = "Controller unreachable";
    el("statusText").className = "err";
    el("statusDot").className = "status-indicator-dot err";
  }
}

async function pushPump(axis, patch) {
  try {
    const next = await api("/api/pump", { axis, ...patch });
    render(next);
  } catch (error) {
    el("statusText").textContent = error.message;
    el("statusText").className = "err";
  }
}

async function pushStep(patch) {
  try {
    const next = await api("/api/step", { index: currentStepIndex, ...patch });
    render(next);
  } catch (error) {
    el("statusText").textContent = error.message;
    el("statusText").className = "err";
  }
}

async function guard(label, fn) {
  try {
    const next = await fn();
    if (next) render(next);
  } catch (error) {
    el("statusText").textContent = `${label}: ${error.message}`;
    el("statusText").className = "err";
  }
}

function wireTabs() {
  const tabs = document.querySelectorAll(".tab-btn");
  tabs.forEach((btn) => {
    btn.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
      btn.classList.add("active");
      const panelId = btn.dataset.tab;
      currentTab = panelId;
      const panel = el(panelId);
      if (panel) panel.classList.add("active");

      // Notify backend of mode switch
      const mode = currentTab === "stepsMode" ? "steps" : "dual";
      api("/api/mode", { mode }).catch(() => {});
    });
  });
}

function wireDualPumpInputs() {
  // Pump 1 (Axis A)
  el("pumpA_enabled").addEventListener("change", (e) => pushPump("A", { enabled: e.target.checked }));
  el("pumpA_diameter").addEventListener("change", (e) => pushPump("A", { diameter: Number(e.target.value) }));
  el("pumpA_flowRate").addEventListener("change", (e) => pushPump("A", { flowRate: Number(e.target.value) }));
  el("pumpA_flowUnit").addEventListener("change", (e) => pushPump("A", { flowRateUnit: Number(e.target.value) }));
  el("pumpA_durationType").addEventListener("change", (e) => {
    const typeVal = Number(e.target.value);
    updateSelectUnits(el("pumpA_durationType"), el("pumpA_durationUnit"));
    pushPump("A", { durationType: typeVal });
  });
  el("pumpA_duration").addEventListener("change", (e) => pushPump("A", { duration: Number(e.target.value) }));
  el("pumpA_durationUnit").addEventListener("change", (e) => {
    const isDuration = Number(el("pumpA_durationType").value) === 0;
    const payload = isDuration ? { durationUnit: Number(e.target.value) } : { volumeUnit: Number(e.target.value) };
    pushPump("A", payload);
  });

  // Pump 2 (Axis B)
  el("pumpB_enabled").addEventListener("change", (e) => pushPump("B", { enabled: e.target.checked }));
  el("pumpB_diameter").addEventListener("change", (e) => pushPump("B", { diameter: Number(e.target.value) }));
  el("pumpB_flowRate").addEventListener("change", (e) => pushPump("B", { flowRate: Number(e.target.value) }));
  el("pumpB_flowUnit").addEventListener("change", (e) => pushPump("B", { flowRateUnit: Number(e.target.value) }));
  el("pumpB_durationType").addEventListener("change", (e) => {
    const typeVal = Number(e.target.value);
    updateSelectUnits(el("pumpB_durationType"), el("pumpB_durationUnit"));
    pushPump("B", { durationType: typeVal });
  });
  el("pumpB_duration").addEventListener("change", (e) => pushPump("B", { duration: Number(e.target.value) }));
  el("pumpB_durationUnit").addEventListener("change", (e) => {
    const isDuration = Number(el("pumpB_durationType").value) === 0;
    const payload = isDuration ? { durationUnit: Number(e.target.value) } : { volumeUnit: Number(e.target.value) };
    pushPump("B", payload);
  });
}

function wireMultiStepInputs() {
  el("selectAll").addEventListener("change", (event) => {
    const on = event.target.checked;
    const selected = (state?.steps || []).map(() => on);
    guard("Select", () => api("/api/selected", { selected }));
  });

  document.querySelectorAll(".stepcheck").forEach((box) => {
    box.addEventListener("change", () => {
      const selected = Array.from(document.querySelectorAll(".stepcheck")).map((b) => b.checked);
      guard("Select", () => api("/api/selected", { selected }));
    });
  });

  el("programStep").addEventListener("change", (event) => {
    currentStepIndex = Number(event.target.value);
    if (state) render(state);
  });

  el("diameter").addEventListener("change", (e) => pushStep({ diameter: Number(e.target.value) }));
  el("flowRate").addEventListener("change", (e) => pushStep({ flowRate: Number(e.target.value) }));
  el("duration").addEventListener("change", (e) => pushStep({ duration: Number(e.target.value) }));
  el("flowUnit").addEventListener("change", (e) => pushStep({ flowRateUnit: Number(e.target.value) }));
  el("durationType").addEventListener("change", (e) => {
    const typeVal = Number(e.target.value);
    updateSelectUnits(el("durationType"), el("durationUnit"));
    pushStep({ durationType: typeVal });
  });
  el("durationUnit").addEventListener("change", (e) => {
    const isDuration = Number(el("durationType").value) === 0;
    const payload = isDuration ? { durationUnit: Number(e.target.value) } : { volumeUnit: Number(e.target.value) };
    pushStep(payload);
  });
}

function wireToolbar() {
  // Connection
  el("connectBtn").addEventListener("click", () => {
    if (state && state.connected) {
      guard("Disconnect", () => api("/api/disconnect"));
    } else {
      const port = el("portSelect").value;
      if (!port) {
        el("statusText").textContent = "Please select a serial port";
        el("statusText").className = "err";
        return;
      }
      guard("Connect", () => api("/api/connect", { port }));
    }
  });

  el("refreshPortsBtn").addEventListener("click", refreshPorts);

  // Motion controls - pass active mode
  el("runBtn").addEventListener("click", () => {
    const mode = currentTab === "stepsMode" ? "steps" : "dual";
    guard("Run", () => api("/api/run", { mode }));
  });
  el("stopBtn").addEventListener("click", () => guard("Stop", () => api("/api/stop")));

  // Direction toggle
  el("withdrawalBtn").addEventListener("click", () => {
    guard("Direction", () => api("/api/withdrawal", { value: !(state?.withdrawal ?? false) }));
  });

  // Dual homing (0x19), Rack 1 homing (0x1A), Rack 2 homing (0x1B)
  el("homeAllBtn").addEventListener("click", () => guard("Home Both", () => api("/api/home", { target: "both" })));
  el("homeBtn").addEventListener("click", () => guard("Home 1", () => api("/api/home", { target: "A" })));
  el("home2Btn").addEventListener("click", () => guard("Home 2", () => api("/api/home", { target: "B" })));

  // Emergency stop
  el("emergencyBtn").addEventListener("click", () => {
    guard("Emergency stop", () => api("/api/emergency-stop"));
  });

  el("clearFaultBtn").addEventListener("click", () => {
    guard("Clear fault", () => api("/api/clear-fault"));
  });

  // At home manual override
  el("atHomeBox").addEventListener("change", (event) => {
    guard("Mark Rack 1 Home", () => api("/api/mark-home", { value: event.target.checked, axis: "A" }));
  });
  el("atHomeBox2").addEventListener("change", (event) => {
    guard("Mark Rack 2 Home", () => api("/api/mark-home", { value: event.target.checked, axis: "B" }));
  });

  // Files
  el("loadBtn").addEventListener("click", () => el("filePicker").click());
  el("filePicker").addEventListener("change", async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    const text = await file.text();
    guard("Load", () => api("/api/load-text", { text, name: file.name }));
    event.target.value = "";
  });

  el("saveBtn").addEventListener("click", () => {
    el("saveDialog").showModal();
  });
  el("saveCancelBtn").addEventListener("click", () => {
    el("saveDialog").close();
  });
  el("saveDialog").addEventListener("close", () => {
    if (el("saveDialog").returnValue !== "ok") return;
    const path = el("savePath").value.trim();
    if (path) guard("Save", () => api("/api/save", { path }));
  });
}

function initSelects() {
  fillSelect(el("pumpA_flowUnit"), FLOW_UNITS);
  fillSelect(el("pumpA_durationType"), DURATION_TYPES);
  updateSelectUnits(el("pumpA_durationType"), el("pumpA_durationUnit"));

  fillSelect(el("pumpB_flowUnit"), FLOW_UNITS);
  fillSelect(el("pumpB_durationType"), DURATION_TYPES);
  updateSelectUnits(el("pumpB_durationType"), el("pumpB_durationUnit"));

  fillSelect(el("flowUnit"), FLOW_UNITS);
  fillSelect(el("durationType"), DURATION_TYPES);
  updateSelectUnits(el("durationType"), el("durationUnit"));
}

async function boot() {
  initSelects();
  wireTabs();
  wireDualPumpInputs();
  wireMultiStepInputs();
  wireToolbar();
  await refreshPorts();
  await poll();
  setInterval(poll, 400);
}

boot();
