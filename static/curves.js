/* Interactive Dual-Syringe Curve & Waveform Preset Editor. */

(function () {
  const VELOCITY_UNITS = ["uL/min", "mL/min", "uL/hr", "mL/hr", "mm/s"];
  const DISPLACEMENT_UNITS = ["uL", "mL", "mm"];

  const PAD = { left: 50, right: 16, top: 18, bottom: 28 };

  function defaultKnots(T, y0 = 0, y1 = 2) {
    return [
      { t: 0, y: y0, inX: 0, inY: y0, outX: T * 0.33, outY: y0 },
      { t: T, y: y1, inX: T * 0.67, inY: y1, outX: T, outY: y1 },
    ];
  }

  const curveState = {
    yMode: "velocity",
    unit: "mL/min",
    timeUnit: "s",
    duration: 60,
    sliceDurationS: 0.25,
    link: { mode: "independent", factor: -1.0 },
    channelA: {
      enabled: true,
      diameter: 14.5,
      syringeCapacityMl: 20,
      invertDirection: false,
      source: "preset",
      preset: "shifted_sine",
      amplitude: 1.0,
      cycles: 1.0,
      phaseDeg: 0,
      offset: 1.0,
      duty: 0.5,
      equation: "1.0 + 1.0 * sin(2 * pi * t / T)",
      knots: defaultKnots(60, 0, 2),
    },
    channelB: {
      enabled: true,
      diameter: 14.5,
      syringeCapacityMl: 20,
      invertDirection: false,
      source: "preset",
      preset: "shifted_cosine",
      amplitude: 1.0,
      cycles: 1.0,
      phaseDeg: 0,
      offset: 1.0,
      duty: 0.5,
      equation: "1.0 + 1.0 * cos(2 * pi * t / T)",
      knots: defaultKnots(60, 2, 0),
    },
  };

  let lastPreview = null;
  let playhead = { running: false, elapsedS: 0, durationS: 60 };
  let syncTimer = null;

  const hoverState = { A: null, B: null };
  const dragState = { channel: null, kind: null, knotIdx: -1 };

  const $ = (id) => document.getElementById(id);

  // -------------------------------------------------------------------------
  // Coordinate mapping & Bezier math
  // -------------------------------------------------------------------------
  function getYRange(chKey) {
    const vals = lastPreview
      ? chKey === "A"
        ? lastPreview.valuesA
        : lastPreview.valuesB
      : [];
    const ch = chKey === "A" ? curveState.channelA : curveState.channelB;
    let ymin = 0;
    let ymax = 2;
    const all = [...vals];
    if (ch.source === "bezier") {
      for (const k of ch.knots) {
        all.push(k.y, k.inY, k.outY);
      }
    }
    if (all.length) {
      ymin = Math.min(0, ...all);
      ymax = Math.max(0, ...all);
    }
    if (Math.abs(ymax - ymin) < 1e-4) {
      ymin -= 1;
      ymax += 1;
    }
    const pad = (ymax - ymin) * 0.15;
    return { ymin: ymin - pad, ymax: ymax + pad };
  }

  function toCanvas(canvas, t, y, yRange) {
    const w = canvas.width;
    const h = canvas.height;
    const plotW = w - PAD.left - PAD.right;
    const plotH = h - PAD.top - PAD.bottom;
    const x = PAD.left + (t / Math.max(1e-6, curveState.duration)) * plotW;
    const cy = PAD.top + ((yRange.ymax - y) / Math.max(1e-6, yRange.ymax - yRange.ymin)) * plotH;
    return { x, y: cy };
  }

  function fromCanvas(canvas, px, py, yRange) {
    const w = canvas.width;
    const h = canvas.height;
    const plotW = w - PAD.left - PAD.right;
    const plotH = h - PAD.top - PAD.bottom;
    const t = ((px - PAD.left) / plotW) * curveState.duration;
    const y = yRange.ymax - ((py - PAD.top) / plotH) * (yRange.ymax - yRange.ymin);
    return { t, y };
  }

  function samplesToBezierKnots(times, values, count = 7) {
    if (!times || times.length < 2) return defaultKnots(curveState.duration);
    const knots = [];
    const n = times.length;
    for (let i = 0; i < count; i++) {
      const idx = Math.round((i / (count - 1)) * (n - 1));
      knots.push({ t: times[idx], y: values[idx] });
    }
    // Compute smooth Catmull-Rom-like Bezier handles
    for (let i = 0; i < knots.length; i++) {
      const prev = knots[Math.max(0, i - 1)];
      const curr = knots[i];
      const next = knots[Math.min(knots.length - 1, i + 1)];
      const slope =
        next.t - prev.t > 1e-6 ? (next.y - prev.y) / (next.t - prev.t) : 0;
      const dtPrev = i > 0 ? (curr.t - prev.t) / 3 : 0;
      const dtNext = i < knots.length - 1 ? (next.t - curr.t) / 3 : 0;
      curr.inX = curr.t - dtPrev;
      curr.inY = curr.y - slope * dtPrev;
      curr.outX = curr.t + dtNext;
      curr.outY = curr.y + slope * dtNext;
    }
    return knots;
  }

  // -------------------------------------------------------------------------
  // Rendering
  // -------------------------------------------------------------------------
  function drawChannel(chKey) {
    const canvas = $(chKey === "A" ? "curveCanvasA" : "curveCanvasB");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const w = canvas.width;
    const h = canvas.height;
    const yRange = getYRange(chKey);
    const ch = chKey === "A" ? curveState.channelA : curveState.channelB;
    const color = chKey === "A" ? "#38bdf8" : "#c084fc";

    ctx.clearRect(0, 0, w, h);
    ctx.fillStyle = "#0b1120";
    ctx.fillRect(0, 0, w, h);

    // Gridlines
    ctx.strokeStyle = "rgba(148, 163, 184, 0.14)";
    ctx.lineWidth = 1;
    ctx.font = "10px system-ui, sans-serif";
    ctx.fillStyle = "rgba(203, 213, 225, 0.7)";

    for (let i = 0; i <= 4; i++) {
      const yVal = yRange.ymin + ((yRange.ymax - yRange.ymin) * i) / 4;
      const p = toCanvas(canvas, 0, yVal, yRange);
      ctx.beginPath();
      ctx.moveTo(PAD.left, p.y);
      ctx.lineTo(w - PAD.right, p.y);
      ctx.stroke();
      ctx.textAlign = "right";
      ctx.fillText(yVal.toFixed(2), PAD.left - 6, p.y + 3);
    }

    for (let i = 0; i <= 4; i++) {
      const tVal = (curveState.duration * i) / 4;
      const p = toCanvas(canvas, tVal, yRange.ymin, yRange);
      ctx.beginPath();
      ctx.moveTo(p.x, PAD.top);
      ctx.lineTo(p.x, h - PAD.bottom);
      ctx.stroke();
      ctx.textAlign = "center";
      ctx.fillText(`${tVal.toFixed(1)}${curveState.timeUnit}`, p.x, h - 8);
    }

    // Zero baseline
    if (yRange.ymin < 0 && yRange.ymax > 0) {
      const z = toCanvas(canvas, 0, 0, yRange);
      ctx.strokeStyle = "rgba(248, 250, 252, 0.32)";
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(PAD.left, z.y);
      ctx.lineTo(w - PAD.right, z.y);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Evaluated curve from backend preview
    if (lastPreview && lastPreview.times) {
      const vals = chKey === "A" ? lastPreview.valuesA : lastPreview.valuesB;
      ctx.strokeStyle = color;
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      for (let i = 0; i < lastPreview.times.length; i++) {
        const pt = toCanvas(canvas, lastPreview.times[i], vals[i], yRange);
        if (i === 0) ctx.moveTo(pt.x, pt.y);
        else ctx.lineTo(pt.x, pt.y);
      }
      ctx.stroke();
    }

    // Bezier knots & control handles (when in bezier mode and not a linked follower)
    const isFollower =
      (chKey === "B" && curveState.link.mode === "B_from_A") ||
      (chKey === "A" && curveState.link.mode === "A_from_B");

    if (ch.source === "bezier" && !isFollower) {
      for (let i = 0; i < ch.knots.length; i++) {
        const k = ch.knots[i];
        const pk = toCanvas(canvas, k.t, k.y, yRange);

        // Handles
        ctx.strokeStyle = "rgba(251, 191, 36, 0.7)";
        ctx.lineWidth = 1.2;
        if (i > 0) {
          const pin = toCanvas(canvas, k.inX, k.inY, yRange);
          ctx.beginPath();
          ctx.moveTo(pk.x, pk.y);
          ctx.lineTo(pin.x, pin.y);
          ctx.stroke();
          ctx.fillStyle = "#fbbf24";
          ctx.beginPath();
          ctx.arc(pin.x, pin.y, 4, 0, Math.PI * 2);
          ctx.fill();
        }
        if (i < ch.knots.length - 1) {
          const pout = toCanvas(canvas, k.outX, k.outY, yRange);
          ctx.beginPath();
          ctx.moveTo(pk.x, pk.y);
          ctx.lineTo(pout.x, pout.y);
          ctx.stroke();
          ctx.fillStyle = "#fbbf24";
          ctx.beginPath();
          ctx.arc(pout.x, pout.y, 4, 0, Math.PI * 2);
          ctx.fill();
        }

        // Anchor knot
        ctx.fillStyle = "#f8fafc";
        ctx.strokeStyle = color;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.arc(pk.x, pk.y, 5.5, 0, Math.PI * 2);
        ctx.fill();
        ctx.stroke();
      }
    }

    // Playhead cursor during execution
    if (playhead.running && playhead.durationS > 0) {
      const frac = Math.min(1, Math.max(0, playhead.elapsedS / playhead.durationS));
      const pt = toCanvas(canvas, frac * curveState.duration, yRange.ymax, yRange);
      ctx.strokeStyle = "#22c55e";
      ctx.lineWidth = 2;
      ctx.beginPath();
      ctx.moveTo(pt.x, PAD.top);
      ctx.lineTo(pt.x, h - PAD.bottom);
      ctx.stroke();
    }

    // Hover crosshair readout
    const hov = hoverState[chKey];
    if (hov) {
      ctx.fillStyle = "rgba(15, 23, 42, 0.85)";
      ctx.fillRect(w - 150, PAD.top + 2, 132, 20);
      ctx.fillStyle = "#e2e8f0";
      ctx.textAlign = "right";
      ctx.fillText(
        `t=${hov.t.toFixed(1)}${curveState.timeUnit}, y=${hov.y.toFixed(2)} ${curveState.unit}`,
        w - 22,
        PAD.top + 15
      );
    }
  }

  function renderMetrics(chKey, m) {
    const box = $(chKey === "A" ? "curveMetricsA" : "curveMetricsB");
    if (!box || !m) return;
    const warns = (m.warnings || [])
      .map((w) => `<div class="curve-warn-box">⚠ ${w}</div>`)
      .join("");
    box.innerHTML = `
      <div class="curve-metric-item"><span class="m-label">Required Initial Fill</span><span class="m-val">${m.requiredInitialFillMl.toFixed(3)} mL</span></div>
      <div class="curve-metric-item"><span class="m-label">Min Syringe Capacity</span><span class="m-val">${m.minSyringeCapacityMl.toFixed(3)} mL</span></div>
      <div class="curve-metric-item"><span class="m-label">Peak Stroke</span><span class="m-val">${m.peakStrokeMm.toFixed(2)} mm</span></div>
      <div class="curve-metric-item"><span class="m-label">Total Infused</span><span class="m-val">${m.totalInfusedMl.toFixed(3)} mL</span></div>
      <div class="curve-metric-item"><span class="m-label">Total Withdrawn</span><span class="m-val">${m.totalWithdrawnMl.toFixed(3)} mL</span></div>
      <div class="curve-metric-item"><span class="m-label">Peak Flow Rate</span><span class="m-val">${m.peakFlowMlMin.toFixed(3)} mL/min</span></div>
      ${warns}
    `;
  }

  function renderAll() {
    drawChannel("A");
    drawChannel("B");
    const cardA = $("curveCardA");
    const cardB = $("curveCardB");
    if (cardA) cardA.classList.toggle("linked-follower", curveState.link.mode === "A_from_B");
    if (cardB) cardB.classList.toggle("linked-follower", curveState.link.mode === "B_from_A");
  }

  // -------------------------------------------------------------------------
  // Backend sync
  // -------------------------------------------------------------------------
  async function syncWithBackend() {
    try {
      const resp = await fetch("/api/curve/config", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(curveState),
      });
      const data = await resp.json().catch(() => ({}));
      if (!resp.ok) {
        const msg = data.detail || `HTTP ${resp.status}`;
        const isB = msg.toLowerCase().includes("pump 2") || msg.toLowerCase().includes("channel b") || curveState.channelB.source === "equation";
        const errEl = isB ? $("curveB_eqErr") : $("curveA_eqErr");
        errEl.textContent = msg;
        errEl.classList.remove("hidden");
        return;
      }
      $("curveA_eqErr").classList.add("hidden");
      $("curveB_eqErr").classList.add("hidden");
      lastPreview = data.preview;
      renderMetrics("A", lastPreview.metricsA);
      renderMetrics("B", lastPreview.metricsB);
      updateResolutionBadge(lastPreview);
      renderAll();
    } catch (err) {
      console.error("Curve sync error:", err);
    }
  }

  function updateResolutionBadge(prev) {
    const badge = $("curveResolutionBadge");
    if (!badge) return;
    const totalS =
      curveState.duration *
      (curveState.timeUnit === "min" ? 60 : curveState.timeUnit === "hr" ? 3600 : 1);
    const stepS = prev ? prev.sliceDurationS : curveState.sliceDurationS;
    const count = prev ? prev.sliceCount : Math.ceil(totalS / Math.max(0.01, stepS));
    const hz = (1.0 / Math.max(1e-4, stepS)).toFixed(1);
    badge.textContent = `${count} slices (${hz} Hz • ${stepS}s)`;
    if (stepS <= 0.25) {
      badge.className = "curve-res-badge ultra-smooth";
    } else if (stepS <= 0.5) {
      badge.className = "curve-res-badge smooth";
    } else {
      badge.className = "curve-res-badge coarse";
    }
  }

  function updateStepPills() {
    const cur = curveState.sliceDurationS;
    document.querySelectorAll(".curve-pill-btn").forEach((btn) => {
      const s = parseFloat(btn.dataset.step);
      if (Math.abs(s - cur) < 1e-3) {
        btn.classList.add("active");
      } else {
        btn.classList.remove("active");
      }
    });
    updateResolutionBadge(lastPreview);
  }

  function scheduleSync() {
    clearTimeout(syncTimer);
    syncTimer = setTimeout(syncWithBackend, 90);
  }

  // -------------------------------------------------------------------------
  // Canvas pointer interaction (Bezier knots & handles)
  // -------------------------------------------------------------------------
  function getCanvasCoords(canvas, evt) {
    const rect = canvas.getBoundingClientRect();
    return {
      px: ((evt.clientX - rect.left) / rect.width) * canvas.width,
      py: ((evt.clientY - rect.top) / rect.height) * canvas.height,
    };
  }

  function wireCanvas(chKey) {
    const canvas = $(chKey === "A" ? "curveCanvasA" : "curveCanvasB");
    if (!canvas) return;

    canvas.addEventListener("mousedown", (evt) => {
      if (evt.button !== 0) return;
      const ch = chKey === "A" ? curveState.channelA : curveState.channelB;
      if (ch.source !== "bezier") return;
      const { px, py } = getCanvasCoords(canvas, evt);
      const yRange = getYRange(chKey);

      // Hit-test knots and handles
      for (let i = 0; i < ch.knots.length; i++) {
        const k = ch.knots[i];
        const pk = toCanvas(canvas, k.t, k.y, yRange);
        if (Math.hypot(px - pk.x, py - pk.y) <= 9) {
          dragState.channel = chKey;
          dragState.kind = "knot";
          dragState.knotIdx = i;
          return;
        }
        if (i > 0) {
          const pin = toCanvas(canvas, k.inX, k.inY, yRange);
          if (Math.hypot(px - pin.x, py - pin.y) <= 8) {
            dragState.channel = chKey;
            dragState.kind = "in";
            dragState.knotIdx = i;
            return;
          }
        }
        if (i < ch.knots.length - 1) {
          const pout = toCanvas(canvas, k.outX, k.outY, yRange);
          if (Math.hypot(px - pout.x, py - pout.y) <= 8) {
            dragState.channel = chKey;
            dragState.kind = "out";
            dragState.knotIdx = i;
            return;
          }
        }
      }
    });

    canvas.addEventListener("mousemove", (evt) => {
      const { px, py } = getCanvasCoords(canvas, evt);
      const yRange = getYRange(chKey);
      const pt = fromCanvas(canvas, px, py, yRange);
      hoverState[chKey] = {
        t: Math.max(0, Math.min(curveState.duration, pt.t)),
        y: pt.y,
      };

      if (dragState.channel === chKey && dragState.knotIdx >= 0) {
        const ch = chKey === "A" ? curveState.channelA : curveState.channelB;
        const idx = dragState.knotIdx;
        const k = ch.knots[idx];
        const prevT = idx > 0 ? ch.knots[idx - 1].t + 0.01 : 0;
        const nextT = idx < ch.knots.length - 1 ? ch.knots[idx + 1].t - 0.01 : curveState.duration;

        if (dragState.kind === "knot") {
          const newT =
            idx === 0
              ? 0
              : idx === ch.knots.length - 1
              ? curveState.duration
              : Math.max(prevT, Math.min(nextT, pt.t));
          const dt = newT - k.t;
          const dy = pt.y - k.y;
          k.t = newT;
          k.y = pt.y;
          k.inX += dt;
          k.inY += dy;
          k.outX += dt;
          k.outY += dy;
        } else if (dragState.kind === "in") {
          k.inX = Math.max(ch.knots[idx - 1].t, Math.min(k.t, pt.t));
          k.inY = pt.y;
        } else if (dragState.kind === "out") {
          k.outX = Math.max(k.t, Math.min(ch.knots[idx + 1].t, pt.t));
          k.outY = pt.y;
        }
        scheduleSync();
      }
      drawChannel(chKey);
    });

    canvas.addEventListener("mouseleave", () => {
      hoverState[chKey] = null;
      drawChannel(chKey);
    });

    canvas.addEventListener("dblclick", (evt) => {
      const ch = chKey === "A" ? curveState.channelA : curveState.channelB;
      if (ch.source !== "bezier" && lastPreview) {
        ch.knots = samplesToBezierKnots(
          lastPreview.times,
          chKey === "A" ? lastPreview.valuesA : lastPreview.valuesB
        );
        ch.source = "bezier";
        $(`curve${chKey}_preset`).value = "bezier";
      }
      const { px, py } = getCanvasCoords(canvas, evt);
      const yRange = getYRange(chKey);
      const pt = fromCanvas(canvas, px, py, yRange);
      const tClamped = Math.max(0.01, Math.min(curveState.duration - 0.01, pt.t));
      const dt = curveState.duration * 0.06;
      ch.knots.push({
        t: tClamped,
        y: pt.y,
        inX: tClamped - dt,
        inY: pt.y,
        outX: tClamped + dt,
        outY: pt.y,
      });
      ch.knots.sort((a, b) => a.t - b.t);
      scheduleSync();
    });

    canvas.addEventListener("contextmenu", (evt) => {
      evt.preventDefault();
      const ch = chKey === "A" ? curveState.channelA : curveState.channelB;
      if (ch.source !== "bezier" || ch.knots.length <= 2) return;
      const { px, py } = getCanvasCoords(canvas, evt);
      const yRange = getYRange(chKey);
      for (let i = 1; i < ch.knots.length - 1; i++) {
        const pk = toCanvas(canvas, ch.knots[i].t, ch.knots[i].y, yRange);
        if (Math.hypot(px - pk.x, py - pk.y) <= 10) {
          ch.knots.splice(i, 1);
          scheduleSync();
          return;
        }
      }
    });
  }

  window.addEventListener("mouseup", () => {
    dragState.channel = null;
    dragState.kind = null;
    dragState.knotIdx = -1;
  });

  // -------------------------------------------------------------------------
  // Controls wiring
  // -------------------------------------------------------------------------
  function renderUnitRadios() {
    const group = $("curveUnitGroup");
    if (!group) return;
    const units = curveState.yMode === "velocity" ? VELOCITY_UNITS : DISPLACEMENT_UNITS;
    if (!units.includes(curveState.unit)) {
      curveState.unit = units[1] || units[0];
    }
    group.innerHTML = units
      .map(
        (u) =>
          `<label><input type="radio" name="curveUnit" value="${u}" ${
            u === curveState.unit ? "checked" : ""
          } /> ${u}</label>`
      )
      .join("");
    group.querySelectorAll('input[name="curveUnit"]').forEach((inp) => {
      inp.addEventListener("change", () => {
        curveState.unit = inp.value;
        scheduleSync();
      });
    });
  }

  function updatePresetVisibility(chKey) {
    const ch = chKey === "A" ? curveState.channelA : curveState.channelB;
    const isFollower =
      (chKey === "B" && curveState.link.mode === "B_from_A") ||
      (chKey === "A" && curveState.link.mode === "A_from_B");

    const paramsEl = $(`curve${chKey}_presetParams`);
    const eqRowEl = $(`curve${chKey}_eqRow`);
    const hintEl = $(`curve${chKey}_hint`);
    const presetSelect = $(`curve${chKey}_preset`);

    if (presetSelect) presetSelect.disabled = isFollower;
    if (paramsEl) paramsEl.style.display = (ch.source === "preset" && !isFollower) ? "flex" : "none";
    if (eqRowEl) eqRowEl.style.display = (ch.source === "equation" && !isFollower) ? "flex" : "none";
    if (hintEl) hintEl.style.display = (ch.source === "bezier" && !isFollower) ? "block" : "none";

    if (ch.source === "preset" && !isFollower) {
      const p = ch.preset || "shifted_sine";
      const isPeriodic = ["shifted_sine", "shifted_cosine", "sawtooth", "triangle", "pulse"].includes(p);
      const isPulse = p === "pulse";
      const isConstant = p === "constant";
      const isLinear = p === "linear";

      const cycLabel = $(`curve${chKey}_cyclesLabel`);
      const phaseLabel = $(`curve${chKey}_phaseLabel`);
      const dutyLabel = $(`curve${chKey}_dutyLabel`);
      const ampLabel = $(`curve${chKey}_ampLabel`);
      const offsetLabel = $(`curve${chKey}_offsetLabel`);

      if (cycLabel) cycLabel.style.display = isPeriodic ? "inline-flex" : "none";
      if (phaseLabel) phaseLabel.style.display = isPeriodic ? "inline-flex" : "none";
      if (dutyLabel) dutyLabel.style.display = isPulse ? "inline-flex" : "none";

      if (ampLabel) {
        ampLabel.childNodes[0].textContent = isLinear ? "Slope " : (isConstant ? "Value " : "Amp ");
      }
      if (offsetLabel) {
        offsetLabel.style.display = isConstant ? "none" : "inline-flex";
      }
    }
  }

  function wireChannelControls(chKey) {
    const ch = chKey === "A" ? curveState.channelA : curveState.channelB;
    $(`curve${chKey}_enabled`).addEventListener("change", (e) => {
      ch.enabled = e.target.checked;
      scheduleSync();
    });
    $(`curve${chKey}_diameter`).addEventListener("change", (e) => {
      ch.diameter = Number(e.target.value) || 14.5;
      scheduleSync();
    });
    $(`curve${chKey}_capacity`).addEventListener("change", (e) => {
      ch.syringeCapacityMl = Number(e.target.value) || 0;
      scheduleSync();
    });
    $(`curve${chKey}_invert`).addEventListener("change", (e) => {
      ch.invertDirection = e.target.checked;
      scheduleSync();
    });

    $(`curve${chKey}_preset`).addEventListener("change", (e) => {
      const v = e.target.value;
      if (v === "bezier") {
        ch.source = "bezier";
      } else if (v === "equation") {
        ch.source = "equation";
      } else {
        ch.source = "preset";
        ch.preset = v;
      }
      updatePresetVisibility(chKey);
      scheduleSync();
    });

    $(`curve${chKey}_amp`).addEventListener("input", (e) => {
      ch.amplitude = Number(e.target.value);
      scheduleSync();
    });
    $(`curve${chKey}_cycles`).addEventListener("input", (e) => {
      ch.cycles = Number(e.target.value);
      scheduleSync();
    });
    $(`curve${chKey}_phase`).addEventListener("input", (e) => {
      ch.phaseDeg = Number(e.target.value);
      scheduleSync();
    });
    $(`curve${chKey}_offset`).addEventListener("input", (e) => {
      ch.offset = Number(e.target.value);
      scheduleSync();
    });
    $(`curve${chKey}_duty`)?.addEventListener("input", (e) => {
      ch.duty = Number(e.target.value);
      scheduleSync();
    });

    $(`curve${chKey}_toBezier`).addEventListener("click", () => {
      if (!lastPreview) return;
      const vals = chKey === "A" ? lastPreview.valuesA : lastPreview.valuesB;
      ch.knots = samplesToBezierKnots(lastPreview.times, vals, 7);
      ch.source = "bezier";
      $(`curve${chKey}_preset`).value = "bezier";
      updatePresetVisibility(chKey);
      scheduleSync();
    });

    $(`curve${chKey}_applyEq`).addEventListener("click", () => {
      ch.equation = $(`curve${chKey}_eq`).value;
      ch.source = "equation";
      $(`curve${chKey}_preset`).value = "equation";
      updatePresetVisibility(chKey);
      scheduleSync();
    });
  }

  function init() {
    if (!$("curveMode")) return;
    renderUnitRadios();

    document.querySelectorAll('input[name="curveYMode"]').forEach((inp) => {
      inp.addEventListener("change", () => {
        curveState.yMode = inp.value;
        renderUnitRadios();
        scheduleSync();
      });
    });

    $("curveDuration").addEventListener("change", (e) => {
      const oldT = curveState.duration;
      const newT = Math.max(1, Number(e.target.value) || 60);
      const scale = newT / Math.max(1e-6, oldT);
      for (const ch of [curveState.channelA, curveState.channelB]) {
        for (const k of ch.knots) {
          k.t *= scale;
          k.inX *= scale;
          k.outX *= scale;
        }
      }
      curveState.duration = newT;
      scheduleSync();
    });

    $("curveTimeUnit").addEventListener("change", (e) => {
      curveState.timeUnit = e.target.value;
      updateResolutionBadge(lastPreview);
      scheduleSync();
    });

    $("curveSliceS").value = curveState.sliceDurationS;
    $("curveSliceS").addEventListener("change", (e) => {
      const val = Math.max(0.05, Number(e.target.value) || 0.25);
      curveState.sliceDurationS = Math.round(val * 100) / 100;
      updateStepPills();
      scheduleSync();
    });

    document.querySelectorAll(".curve-pill-btn").forEach((btn) => {
      btn.addEventListener("click", () => {
        const step = parseFloat(btn.dataset.step) || 0.25;
        curveState.sliceDurationS = step;
        $("curveSliceS").value = step;
        updateStepPills();
        scheduleSync();
      });
    });

    $("curveLinkMode").addEventListener("change", (e) => {
      curveState.link.mode = e.target.value;
      updatePresetVisibility("A");
      updatePresetVisibility("B");
      renderAll();
      scheduleSync();
    });

    $("curveLinkFactor").addEventListener("input", (e) => {
      curveState.link.factor = Number(e.target.value);
      scheduleSync();
    });

    $("curveBakeLinkBtn").addEventListener("click", () => {
      if (!lastPreview || curveState.link.mode === "independent") return;
      const followerKey = curveState.link.mode === "B_from_A" ? "B" : "A";
      const ch = followerKey === "A" ? curveState.channelA : curveState.channelB;
      const vals = followerKey === "A" ? lastPreview.valuesA : lastPreview.valuesB;
      ch.knots = samplesToBezierKnots(lastPreview.times, vals, 7);
      ch.source = "bezier";
      $(`curve${followerKey}_preset`).value = "bezier";
      curveState.link.mode = "independent";
      $("curveLinkMode").value = "independent";
      updatePresetVisibility("A");
      updatePresetVisibility("B");
      renderAll();
      scheduleSync();
    });

    $("curvePushPullBtn").addEventListener("click", () => {
      curveState.link.mode = "independent";
      $("curveLinkMode").value = "independent";
      curveState.channelA.source = "preset";
      curveState.channelA.preset = "shifted_sine";
      curveState.channelA.amplitude = 1.0;
      curveState.channelA.offset = 1.0;
      curveState.channelA.phaseDeg = 0;
      $("curveA_preset").value = "shifted_sine";
      $("curveA_amp").value = "1.0";
      $("curveA_offset").value = "1.0";
      $("curveA_phase").value = "0";

      curveState.channelB.source = "preset";
      curveState.channelB.preset = "shifted_cosine";
      curveState.channelB.amplitude = 1.0;
      curveState.channelB.offset = 1.0;
      curveState.channelB.phaseDeg = 0;
      $("curveB_preset").value = "shifted_cosine";
      $("curveB_amp").value = "1.0";
      $("curveB_offset").value = "1.0";
      $("curveB_phase").value = "0";

      updatePresetVisibility("A");
      updatePresetVisibility("B");
      renderAll();
      scheduleSync();
    });

    wireChannelControls("A");
    wireChannelControls("B");
    wireCanvas("A");
    wireCanvas("B");
    updatePresetVisibility("A");
    updatePresetVisibility("B");
    syncWithBackend();
  }

  window.CurveEditor = {
    init,
    syncNow: syncWithBackend,
    setPlayhead(elapsedS, durationS, running) {
      const wasRunning = playhead.running;
      playhead = { elapsedS, durationS, running };
      if (running || wasRunning) renderAll();
    },
  };

  document.addEventListener("DOMContentLoaded", init);
})();
