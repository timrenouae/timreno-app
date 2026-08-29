// TIM RENO Rough Estimator -- Quick Estimate mode: total area x a Low/
// Medium/High per-sq-ft rate (by project type) = a ballpark total in
// minutes, as opposed to estimator.js's detailed per-space/per-item mode.
// Reuses the same "Import from Drawing" endpoint pattern as the Quote
// Builder (static/js/quote_builder.js) to pull room sizes straight off an
// uploaded floor plan.
(function () {
  "use strict";

  const initial = JSON.parse(document.getElementById("timrInitialData").textContent);
  const CSRF_TOKEN = initial.csrfToken;
  const SQFT_TIERS = initial.sqftTiers || ["Low", "Medium", "High"];
  const SQFT_PROJECT_TYPES = initial.sqftProjectTypes || []; // [[key, label], ...]

  const $ = (id) => document.getElementById(id);
  function fmtMoney(n) { return "AED " + (n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  const state = {
    sqftRates: JSON.parse(JSON.stringify(initial.sqftRates || {})), // {project_type: {tier: {key,label,rate}}}
    projectType: (SQFT_PROJECT_TYPES[0] || ["house"])[0],
    tier: "Medium",
    rooms: [], // {id, name, sqft}
  };
  let roomIdCounter = 1;

  function setStatus(msg) { $("qeStatus").textContent = msg || ""; }
  function setDrawingStatus(msg, kind) {
    const el = $("qeDrawingStatus");
    el.textContent = msg || "";
    el.className = "save-status" + (kind ? ` is-${kind}` : "");
  }

  function currentRateEntry() {
    return (state.sqftRates[state.projectType] || {})[state.tier] || null;
  }

  // --------------------------------------------------------- mode toggle

  const modeSeg = $("estimatorModeSeg");
  modeSeg.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => {
      modeSeg.querySelectorAll("button").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      const quick = btn.dataset.mode === "quick";
      $("quickModeBox").hidden = !quick;
      $("detailedModeBox").hidden = quick;
    });
  });

  // ------------------------------------------------ project type / tier

  function renderProjectTypeSeg() {
    const box = $("qeProjectTypeSeg");
    box.innerHTML = SQFT_PROJECT_TYPES.map(([key, label]) => `
      <button type="button" data-key="${key}" class="${key === state.projectType ? 'active' : ''}">${escapeHtml(label)}</button>
    `).join("");
    box.querySelectorAll("button").forEach(btn => {
      btn.addEventListener("click", () => {
        state.projectType = btn.dataset.key;
        renderProjectTypeSeg();
        renderRateSheet();
        renderSummary();
      });
    });
  }

  function renderTierSeg() {
    const box = $("qeTierSeg");
    box.innerHTML = SQFT_TIERS.map(tier => `
      <button type="button" data-tier="${tier}" class="${tier === state.tier ? 'active' : ''}">${tier}</button>
    `).join("");
    box.querySelectorAll("button").forEach(btn => {
      btn.addEventListener("click", () => {
        state.tier = btn.dataset.tier;
        renderTierSeg();
        renderSummary();
      });
    });
  }

  // ------------------------------------------------------- rate sheet

  function renderRateSheet() {
    const box = $("qeRateSheetBox");
    const rows = SQFT_PROJECT_TYPES.map(([ptKey, ptLabel]) => {
      const tiers = state.sqftRates[ptKey] || {};
      const cells = SQFT_TIERS.map(tier => {
        const entry = tiers[tier];
        if (!entry) return "<td>—</td>";
        return `<td><input type="number" min="0" step="1" value="${entry.rate}" data-rate-key="${entry.key}" style="width:90px;"></td>`;
      }).join("");
      return `<tr><td><b>${escapeHtml(ptLabel)}</b></td>${cells}</tr>`;
    }).join("");
    box.innerHTML = `
      <table>
        <thead><tr><th>Project type</th>${SQFT_TIERS.map(t => `<th>${t} (AED/sq ft)</th>`).join("")}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
    `;
  }

  $("qeSaveRatesBtn").addEventListener("click", () => {
    const rates = {};
    document.querySelectorAll("#qeRateSheetBox [data-rate-key]").forEach(inp => {
      rates[inp.dataset.rateKey] = parseFloat(inp.value) || 0;
    });
    fetch("/estimator/sqft-rates/save", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF_TOKEN },
      body: JSON.stringify({ rates }),
    })
      .then(r => r.json())
      .then(body => {
        if (body.sqftRates) {
          state.sqftRates = body.sqftRates;
          renderRateSheet();
          renderSummary();
          setStatus("Rates saved.");
        }
      })
      .catch(() => setStatus("Could not save rates."));
  });

  $("qeResetRatesBtn").addEventListener("click", () => {
    fetch("/estimator/sqft-rates/reset", {
      method: "POST",
      headers: { "X-CSRF-Token": CSRF_TOKEN },
    })
      .then(r => r.json())
      .then(body => {
        if (body.sqftRates) {
          state.sqftRates = body.sqftRates;
          renderRateSheet();
          renderSummary();
          setStatus("Rates reset to defaults.");
        }
      })
      .catch(() => setStatus("Could not reset rates."));
  });

  // -------------------------------------------------------------- rooms

  function addRoom(name, sqft) {
    state.rooms.push({ id: roomIdCounter++, name, sqft });
    renderRooms();
    renderSummary();
  }

  function removeRoom(id) {
    state.rooms = state.rooms.filter(r => r.id !== id);
    renderRooms();
    renderSummary();
  }

  function renderRooms() {
    const box = $("qeRoomsBox");
    if (!state.rooms.length) {
      box.innerHTML = '<div class="empty-state">No rooms yet — import a drawing or add one manually above.</div>';
      return;
    }
    box.innerHTML = `
      <table>
        <thead><tr><th>Room / space</th><th>Sq ft</th><th></th></tr></thead>
        <tbody>
        ${state.rooms.map(r => `
          <tr>
            <td>${escapeHtml(r.name)}</td>
            <td class="qty-cell"><input type="number" step="0.01" min="0" value="${r.sqft}" data-room-sqft="${r.id}"></td>
            <td><button type="button" class="remove-link" data-room-remove="${r.id}">✕</button></td>
          </tr>
        `).join("")}
        </tbody>
      </table>
    `;
    box.querySelectorAll("[data-room-remove]").forEach(btn =>
      btn.addEventListener("click", () => removeRoom(parseInt(btn.dataset.roomRemove, 10))));
    box.querySelectorAll("[data-room-sqft]").forEach(inp =>
      inp.addEventListener("input", () => {
        const room = state.rooms.find(r => r.id === parseInt(inp.dataset.roomSqft, 10));
        if (room) { room.sqft = parseFloat(inp.value) || 0; renderSummary(); }
      }));
  }

  $("qeAddRoomBtn").addEventListener("click", () => {
    const name = $("qeNewRoomName").value.trim();
    const sqft = parseFloat($("qeNewRoomSqft").value) || 0;
    if (!name) { alert("Enter a room/space name."); return; }
    if (sqft <= 0) { alert("Enter a positive sq ft value."); return; }
    addRoom(name, sqft);
    $("qeNewRoomName").value = "";
    $("qeNewRoomSqft").value = "";
  });

  // ---------------------------------------------------- import drawing

  let drawingReview = []; // [{name, sqft, checked}]

  function renderDrawingReview() {
    const box = $("qeDrawingReviewBox");
    if (!drawingReview.length) { box.innerHTML = ""; return; }
    box.innerHTML = `
      <table>
        <thead><tr><th></th><th>Room</th><th>Approx. sq ft</th></tr></thead>
        <tbody>
        ${drawingReview.map((r, i) => `
          <tr>
            <td><input type="checkbox" data-dr-check="${i}" ${r.checked ? "checked" : ""}></td>
            <td><input type="text" data-dr-name="${i}" value="${escapeHtml(r.name)}"></td>
            <td><input type="text" data-dr-sqft="${i}" value="${escapeHtml(r.sqft)}" placeholder="e.g. 180"></td>
          </tr>
        `).join("")}
        </tbody>
      </table>
      <button type="button" class="btn btn-primary" id="qeAddCheckedBtn" style="margin-top:8px;">Add checked rooms</button>
    `;
    box.querySelectorAll("[data-dr-check]").forEach(cb =>
      cb.addEventListener("change", () => { drawingReview[parseInt(cb.dataset.drCheck, 10)].checked = cb.checked; }));
    box.querySelectorAll("[data-dr-name]").forEach(inp =>
      inp.addEventListener("input", () => { drawingReview[parseInt(inp.dataset.drName, 10)].name = inp.value; }));
    box.querySelectorAll("[data-dr-sqft]").forEach(inp =>
      inp.addEventListener("input", () => { drawingReview[parseInt(inp.dataset.drSqft, 10)].sqft = inp.value; }));
    $("qeAddCheckedBtn").addEventListener("click", () => {
      const checked = drawingReview.filter(r => r.checked && r.name.trim() && parseFloat(r.sqft) > 0);
      checked.forEach(r => addRoom(r.name.trim(), parseFloat(r.sqft)));
      drawingReview = drawingReview.filter(r => !(r.checked && r.name.trim() && parseFloat(r.sqft) > 0));
      renderDrawingReview();
      setDrawingStatus(checked.length ? `Added ${checked.length} room(s).` : "", checked.length ? "success" : undefined);
    });
  }

  $("qeAnalyzeDrawingBtn").addEventListener("click", () => {
    const fileInput = $("qeDrawingFile");
    const file = fileInput.files && fileInput.files[0];
    if (!file) { setDrawingStatus("Choose a drawing file first.", "error"); return; }
    const fd = new FormData();
    fd.append("drawing_file", file);
    fd.append("csrf_token", CSRF_TOKEN);
    setDrawingStatus("Analyzing drawing… this can take up to a minute.");
    $("qeAnalyzeDrawingBtn").disabled = true;
    fetch("/estimator/import-drawing", {
      method: "POST",
      headers: { "X-CSRF-Token": CSRF_TOKEN },
      body: fd,
    })
      .then(async (r) => {
        const contentType = r.headers.get("content-type") || "";
        if (!contentType.includes("application/json")) {
          throw new Error(
            r.status === 504 || r.status === 502
              ? "The drawing took too long to analyze and the request timed out. Try a smaller file, a clearer single-page image, or try again."
              : `Drawing analysis failed (server error ${r.status}). Try again in a moment.`
          );
        }
        const body = await r.json();
        if (!r.ok) throw new Error(body.error || "Drawing analysis failed.");
        return body;
      })
      .then((body) => {
        drawingReview = (body.rooms || []).map(r => ({
          name: r.name,
          sqft: r.approx_sqft != null ? String(Math.round(r.approx_sqft)) : "",
          checked: true,
        }));
        renderDrawingReview();
        if (body.message) {
          setDrawingStatus(body.message, drawingReview.length ? "success" : "error");
        } else {
          setDrawingStatus(`Found ${drawingReview.length} room(s) — review below, then add.`, "success");
        }
      })
      .catch((err) => setDrawingStatus(err.message, "error"))
      .finally(() => { $("qeAnalyzeDrawingBtn").disabled = false; });
  });

  // ------------------------------------------------------------ summary

  function renderSummary() {
    const totalArea = state.rooms.reduce((sum, r) => sum + (r.sqft || 0), 0);
    const entry = currentRateEntry();
    const rate = entry ? entry.rate : 0;
    const subtotal = totalArea * rate;
    const vatPct = parseFloat($("qeVat").value) || 0;
    const vatAmt = subtotal * vatPct / 100;
    const grand = subtotal + vatAmt;

    $("qeSumArea").textContent = `${totalArea.toLocaleString(undefined, { maximumFractionDigits: 2 })} sq ft`;
    $("qeSumRate").textContent = entry ? `${fmtMoney(rate)} / sq ft (${state.tier})` : "—";
    $("qeSumSubtotal").textContent = fmtMoney(subtotal);
    $("qeSumGrand").textContent = fmtMoney(grand);
    $("qeRateHint").textContent = entry
      ? `${entry.label} — ${SQFT_PROJECT_TYPES.find(([k]) => k === state.projectType)?.[1] || state.projectType}, ${state.tier} quality: ${fmtMoney(rate)} per sq ft.`
      : "";
  }

  $("qeVat").addEventListener("input", renderSummary);

  // ---------------------------------------------------------------- pdf

  $("qePdfBtn").addEventListener("click", () => {
    if (!state.rooms.length) { alert("Add at least one room/space before exporting."); return; }
    setStatus("Generating PDF…");
    fetch("/estimator/quick-pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF_TOKEN },
      body: JSON.stringify({
        project_type: state.projectType,
        tier: state.tier,
        project_name: $("qeProjectName").value.trim(),
        location: $("qeLocation").value.trim(),
        vat_percent: parseFloat($("qeVat").value) || 0,
        rooms: state.rooms.map(r => ({ name: r.name, sqft: r.sqft })),
      }),
    })
      .then(async (r) => {
        const contentType = r.headers.get("content-type") || "";
        if (!contentType.includes("application/pdf")) {
          const body = contentType.includes("application/json") ? await r.json().catch(() => ({})) : {};
          throw new Error(body.error || "Could not generate the PDF.");
        }
        return r.blob();
      })
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        const project = $("qeProjectName").value.trim() || "estimate";
        a.href = url;
        a.download = `Quick_Estimate_${project.replace(/[^a-z0-9_-]+/gi, "_")}.pdf`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        setStatus("PDF downloaded.");
      })
      .catch((err) => setStatus(err.message));
  });

  $("qeNewEstimateBtn").addEventListener("click", () => {
    if (!confirm("Clear this Quick Estimate and start a new one?")) return;
    state.rooms = [];
    drawingReview = [];
    $("qeClient").value = ""; $("qeProjectName").value = ""; $("qeLocation").value = "";
    renderRooms();
    renderDrawingReview();
    setDrawingStatus("");
    renderSummary();
    setStatus("Started a new Quick Estimate.");
  });

  // --------------------------------------------------------------- init

  renderProjectTypeSeg();
  renderTierSeg();
  renderRateSheet();
  renderRooms();
  renderSummary();
})();
