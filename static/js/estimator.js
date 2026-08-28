// TIM RENO Rough Estimator -- ported from the old TIMR Rough Estimator.html.
// Swaps localStorage rate overrides for fetch() calls against the Flask
// app, and html2pdf()/localStorage "send to builder" for server-rendered
// PDF + a real save-quote call.
(function () {
  "use strict";

  const initial = JSON.parse(document.getElementById("timrInitialData").textContent);
  const CSRF_TOKEN = initial.csrfToken;
  const RATE_DEFAULTS = initial.rateDefaults; // {key: {label, unit, default_rate}}
  const SPACE_TEMPLATES = initial.spaceTemplates; // {type: [[rateKey, qtyRule], ...]}
  const FINISH_MULTIPLIERS = initial.finishMultipliers;
  const UNIT_OPTIONS = initial.unitOptions || [];

  const $ = (id) => document.getElementById(id);
  function round2(n) { return Math.round((n + Number.EPSILON) * 100) / 100; }
  function fmtMoney(n) { return "AED " + (n || 0).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  const state = {
    finishLevel: "Standard",
    overrides: { ...(initial.overrides || {}) },
    spaces: [], // {id, type, label, size, items:[{description,qty,unit,unit_price}], notes}
  };
  let spaceIdCounter = 1;

  function baseRate(key) {
    return (state.overrides[key] != null) ? state.overrides[key] : RATE_DEFAULTS[key].default_rate;
  }
  function effectiveRate(key) {
    return round2(baseRate(key) * FINISH_MULTIPLIERS[state.finishLevel]);
  }

  // ------------------------------------------------------------ rate sheet

  const rateSheetBox = $("rateSheetBox");
  function renderRateSheet() {
    rateSheetBox.innerHTML = Object.keys(RATE_DEFAULTS).map(key => {
      const def = RATE_DEFAULTS[key];
      const val = baseRate(key);
      return `
        <div class="rate-row">
          <div><div class="rlabel">${escapeHtml(def.label)}</div><div class="runit">per ${escapeHtml(def.unit)}</div></div>
          <input type="number" step="0.01" min="0" data-rate-key="${key}" value="${val}">
          <div class="rate-effective" data-rate-effective="${key}"></div>
        </div>
      `;
    }).join("");
    updateEffectiveRateDisplay();
  }
  function updateEffectiveRateDisplay() {
    Object.keys(RATE_DEFAULTS).forEach(key => {
      const el = rateSheetBox.querySelector(`[data-rate-effective="${key}"]`);
      if (el) el.textContent = "now: " + fmtMoney(effectiveRate(key));
    });
  }

  $("saveRatesBtn").addEventListener("click", () => {
    const rates = {};
    rateSheetBox.querySelectorAll("[data-rate-key]").forEach(inp => {
      const v = parseFloat(inp.value);
      if (!isNaN(v)) rates[inp.dataset.rateKey] = v;
    });
    fetch("/estimator/rates/save", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF_TOKEN },
      body: JSON.stringify({ rates }),
    })
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(body.error || "Could not save rates.");
        return body;
      })
      .then((body) => {
        state.overrides = body.overrides || {};
        updateEffectiveRateDisplay();
        setStatus("Rate sheet saved — new spaces will use these rates.");
      })
      .catch((err) => setStatus(err.message));
  });

  $("resetRatesBtn").addEventListener("click", () => {
    if (!confirm("Reset all rates to the built-in defaults? This clears the saved overrides for everyone.")) return;
    fetch("/estimator/rates/reset", {
      method: "POST",
      headers: { "X-CSRF-Token": CSRF_TOKEN },
    })
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(body.error || "Could not reset rates.");
        return body;
      })
      .then((body) => {
        state.overrides = body.overrides || {};
        renderRateSheet();
        setStatus("Rate sheet reset to defaults.");
      })
      .catch((err) => setStatus(err.message));
  });

  // ------------------------------------------------------------- finish lv

  const finishSeg = $("finishSeg");
  finishSeg.querySelectorAll("button").forEach(btn => {
    btn.addEventListener("click", () => {
      finishSeg.querySelectorAll("button").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      state.finishLevel = btn.dataset.level;
      updateEffectiveRateDisplay();
    });
  });

  function resolveQty(rule, size) {
    const s = Number(size) || 0;
    if (rule === "sqft") return s;
    if (rule === "sqft_x2") return round2(s * 2);
    return rule;
  }

  function buildItemsFromTemplate(type, size) {
    const tpl = SPACE_TEMPLATES[type] || [];
    return tpl.map(([rateKey, qtyRule]) => {
      const def = RATE_DEFAULTS[rateKey];
      return {
        description: def.label,
        unit: def.unit,
        qty: resolveQty(qtyRule, size),
        unit_price: effectiveRate(rateKey),
      };
    });
  }

  // ---------------------------------------------------------- add a space

  const newSpaceType = $("newSpaceType");
  Object.keys(SPACE_TEMPLATES).forEach(type => {
    const opt = document.createElement("option");
    opt.value = type; opt.textContent = type;
    newSpaceType.appendChild(opt);
  });

  $("addSpaceBtn").addEventListener("click", () => {
    const type = newSpaceType.value;
    const label = $("newSpaceLabel").value.trim() || type;
    const size = parseFloat($("newSpaceSize").value) || 0;
    const space = { id: spaceIdCounter++, type, label, size, items: buildItemsFromTemplate(type, size), notes: "" };
    state.spaces.push(space);
    $("newSpaceLabel").value = "";
    $("newSpaceSize").value = "";
    renderSpaces();
    setStatus(`Added "${label}".`);
  });

  // ------------------------------------------------------------- spaces UI

  const spacesBox = $("spacesBox");

  function spaceTotals(space) {
    const subtotal = space.items.reduce((s, i) => s + (Number(i.qty) || 0) * (Number(i.unit_price) || 0), 0);
    const perSqft = space.size > 0 ? subtotal / space.size : null;
    return { subtotal, perSqft };
  }

  function findSpace(id) { return state.spaces.find(s => s.id === Number(id)); }

  function renderSpaces() {
    if (state.spaces.length === 0) {
      spacesBox.innerHTML = '<div class="empty-spaces">No spaces yet — add a room, cabin, or partition above to start building the estimate.</div>';
      renderSummary();
      return;
    }
    spacesBox.innerHTML = state.spaces.map(space => {
      const { subtotal, perSqft } = spaceTotals(space);
      const rows = space.items.map((item, idx) => `
        <tr data-space="${space.id}" data-item="${idx}">
          <td class="col-desc"><input type="text" data-field="description" value="${escapeHtml(item.description)}"></td>
          <td class="col-qty"><input type="number" step="1" data-field="qty" value="${item.qty}"></td>
          <td class="col-unit"><input type="text" data-field="unit" value="${escapeHtml(item.unit)}"></td>
          <td class="col-rate"><input type="number" step="0.01" data-field="unit_price" value="${item.unit_price}"></td>
          <td class="col-amt amt">${fmtMoney((Number(item.qty) || 0) * (Number(item.unit_price) || 0))}</td>
          <td class="col-del"><button class="btn btn-danger" type="button" data-remove-item="${idx}" data-space-del="${space.id}">✕</button></td>
        </tr>
      `).join("");
      return `
        <div class="space-card" data-space-card="${space.id}">
          <div class="space-head">
            <span class="stype">${escapeHtml(space.type)}</span>
            <span class="sname">${escapeHtml(space.label)}</span>
            <span class="ssize">${space.size > 0 ? space.size + " sq ft" : "no size set"}</span>
          </div>
          <div class="space-body">
            <table class="items">
              <thead><tr><th class="col-desc">Description</th><th class="col-qty">Qty</th><th class="col-unit">Unit</th><th class="col-rate">Rate (AED)</th><th class="col-amt">Amount</th><th class="col-del"></th></tr></thead>
              <tbody>${rows || '<tr><td colspan="6" class="empty-state">No items yet — add one below.</td></tr>'}</tbody>
            </table>
            <div class="custom-item-row" data-space-custom="${space.id}">
              <div><div class="mini-label">Add item</div><input type="text" placeholder="Description" data-custom-desc></div>
              <div><div class="mini-label">Qty</div><input type="number" step="1" value="1" data-custom-qty></div>
              <div><div class="mini-label">Unit</div><select data-custom-unit>${UNIT_OPTIONS.map(u => `<option value="${escapeHtml(u)}"${u === "Nos" ? " selected" : ""}>${escapeHtml(u)}</option>`).join("")}<option value="Other">Other…</option></select></div>
              <div><div class="mini-label">Rate (AED)</div><input type="number" step="0.01" placeholder="0" data-custom-rate></div>
              <button class="btn btn-secondary" type="button" data-custom-add="${space.id}">+ Add</button>
            </div>
            <div class="space-note">
              <label for="note-${space.id}">Note (optional)</label>
              <input id="note-${space.id}" type="text" data-space-note="${space.id}" value="${escapeHtml(space.notes)}" placeholder="Anything to flag about this space">
            </div>
            <div class="space-footer">
              <div>
                <div class="space-total">Subtotal: <b>${fmtMoney(subtotal)}</b></div>
                <div class="space-persqft">${perSqft != null ? "≈ " + fmtMoney(perSqft) + " / sq ft" : "set a size to see AED/sq ft"}</div>
              </div>
              <div class="space-actions">
                <button class="btn btn-ghost" type="button" data-reset-template="${space.id}">Reset to template</button>
                <button class="btn btn-secondary" type="button" data-duplicate="${space.id}">Duplicate</button>
                <button class="btn btn-danger" type="button" data-remove-space="${space.id}">Remove space</button>
              </div>
            </div>
          </div>
        </div>
      `;
    }).join("");

    wireSpaceEvents();
    renderSummary();
  }

  function wireSpaceEvents() {
    spacesBox.querySelectorAll("table.items input[data-field]").forEach(inp => {
      inp.addEventListener("change", () => {
        const tr = inp.closest("tr");
        const space = findSpace(tr.dataset.space);
        const item = space.items[Number(tr.dataset.item)];
        const field = inp.dataset.field;
        item[field] = (field === "qty" || field === "unit_price") ? (parseFloat(inp.value) || 0) : inp.value;
        renderSpaces();
      });
    });
    spacesBox.querySelectorAll("[data-remove-item]").forEach(btn => {
      btn.addEventListener("click", () => {
        const space = findSpace(btn.dataset.spaceDel);
        space.items.splice(Number(btn.dataset.removeItem), 1);
        renderSpaces();
      });
    });
    spacesBox.querySelectorAll("[data-custom-unit]").forEach(sel => {
      sel.addEventListener("change", () => {
        if (sel.value !== "Other") return;
        const custom = prompt("Enter a custom unit (e.g. sqm, litre):");
        if (custom && custom.trim()) {
          const opt = document.createElement("option");
          opt.value = custom.trim(); opt.textContent = custom.trim();
          sel.insertBefore(opt, sel.querySelector('option[value="Other"]'));
          sel.value = custom.trim();
        } else {
          sel.value = "Nos";
        }
      });
    });
    spacesBox.querySelectorAll("[data-custom-add]").forEach(btn => {
      btn.addEventListener("click", () => {
        const space = findSpace(btn.dataset.customAdd);
        const row = btn.closest(".custom-item-row");
        const desc = row.querySelector("[data-custom-desc]").value.trim();
        if (!desc) { alert("Enter a description first."); return; }
        const qty = parseFloat(row.querySelector("[data-custom-qty]").value) || 0;
        const unit = row.querySelector("[data-custom-unit]").value || "Nos";
        const rate = parseFloat(row.querySelector("[data-custom-rate]").value) || 0;
        space.items.push({ description: desc, qty, unit, unit_price: rate });
        renderSpaces();
      });
    });
    spacesBox.querySelectorAll("[data-space-note]").forEach(inp => {
      inp.addEventListener("change", () => {
        findSpace(inp.dataset.spaceNote).notes = inp.value;
      });
    });
    spacesBox.querySelectorAll("[data-reset-template]").forEach(btn => {
      btn.addEventListener("click", () => {
        const space = findSpace(btn.dataset.resetTemplate);
        if (!confirm(`Reset "${space.label}" back to the ${space.type} template? Any edits to its items will be lost.`)) return;
        space.items = buildItemsFromTemplate(space.type, space.size);
        renderSpaces();
      });
    });
    spacesBox.querySelectorAll("[data-duplicate]").forEach(btn => {
      btn.addEventListener("click", () => {
        const space = findSpace(btn.dataset.duplicate);
        const copy = {
          id: spaceIdCounter++, type: space.type, label: space.label + " (copy)", size: space.size,
          items: space.items.map(i => ({ ...i })), notes: space.notes,
        };
        const idx = state.spaces.findIndex(s => s.id === space.id);
        state.spaces.splice(idx + 1, 0, copy);
        renderSpaces();
      });
    });
    spacesBox.querySelectorAll("[data-remove-space]").forEach(btn => {
      btn.addEventListener("click", () => {
        const space = findSpace(btn.dataset.removeSpace);
        if (!confirm(`Remove "${space.label}" from this estimate?`)) return;
        state.spaces = state.spaces.filter(s => s.id !== space.id);
        renderSpaces();
      });
    });
  }

  // ------------------------------------------------------------- summary

  function overallTotals() {
    const subtotal = state.spaces.reduce((s, sp) => s + spaceTotals(sp).subtotal, 0);
    const vatPct = parseFloat($("vatPercent").value) || 0;
    const vatAmt = subtotal * (vatPct / 100);
    const grand = subtotal + vatAmt;
    const totalSqft = state.spaces.reduce((s, sp) => s + (Number(sp.size) || 0), 0);
    const perSqft = totalSqft > 0 ? subtotal / totalSqft : null;
    return { subtotal, vatPct, vatAmt, grand, totalSqft, perSqft };
  }

  function renderSummary() {
    const t = overallTotals();
    $("sumSubtotal").textContent = fmtMoney(t.subtotal);
    $("sumVat").textContent = fmtMoney(t.vatAmt);
    $("sumGrand").textContent = fmtMoney(t.grand);
    $("sumPerSqft").textContent = t.perSqft != null ? fmtMoney(t.perSqft) + "/sqft" : "—";
  }
  $("vatPercent").addEventListener("input", renderSummary);

  function setStatus(msg) {
    const el = $("estimatorStatus");
    if (el) el.textContent = msg;
  }

  // ---------------------------------------------------------- new estimate

  $("newEstimateBtn").addEventListener("click", () => {
    if (state.spaces.length && !confirm("Start a new estimate? Anything on this page that you have not sent to Quote Builder will be lost.")) return;
    state.spaces = [];
    $("metaClient").value = ""; $("metaProjectName").value = ""; $("metaProjectType").value = ""; $("metaLocation").value = "";
    $("vatPercent").value = 5;
    renderSpaces();
    setStatus("Started a new estimate.");
  });

  // -------------------------------------------------------------- payload

  function buildStatePayload() {
    return {
      client_name: $("metaClient").value.trim(),
      project_name: $("metaProjectName").value.trim(),
      project_type: $("metaProjectType").value.trim(),
      location: $("metaLocation").value.trim(),
      vat_percent: parseFloat($("vatPercent").value) || 5,
      finish_level: state.finishLevel,
      spaces: state.spaces.map(sp => ({
        label: sp.label, size: sp.size, notes: sp.notes,
        items: sp.items.map(i => ({ ...i })),
      })),
    };
  }

  // -------------------------------------------------------------- pdf

  $("pdfBtn").addEventListener("click", () => {
    if (state.spaces.length === 0) { alert("Add at least one space before exporting."); return; }
    setStatus("Generating PDF…");
    fetch("/estimator/pdf", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF_TOKEN },
      body: JSON.stringify(buildStatePayload()),
    })
      .then(async (r) => {
        if (!r.ok) {
          const body = await r.json().catch(() => ({}));
          throw new Error(body.error || "Could not generate the PDF.");
        }
        return r.blob();
      })
      .then((blob) => {
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        const project = $("metaProjectName").value.trim() || "estimate";
        a.href = url;
        a.download = `Rough_Estimate_${project.replace(/[^a-z0-9_-]+/gi, "_")}.pdf`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
        setStatus("PDF downloaded.");
      })
      .catch((err) => setStatus(err.message));
  });

  // ----------------------------------------------------- send to builder

  $("sendToBuilderBtn").addEventListener("click", () => {
    if (state.spaces.length === 0) { alert("Add at least one space before sending this to Quote Builder."); return; }
    setStatus("Sending to Quote Builder…");
    fetch("/estimator/send-to-builder", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF_TOKEN },
      body: JSON.stringify(buildStatePayload()),
    })
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(body.error || "Could not send this to Quote Builder.");
        return body;
      })
      .then((body) => {
        setStatus(`Saved as draft ${body.quote_number}. Opening Quote Builder…`);
        window.location.href = `/quotes/${body.quote_id}/edit`;
      })
      .catch((err) => setStatus(err.message));
  });

  // ---------------------------------------------------------------- init

  renderRateSheet();
  renderSpaces();
})();
