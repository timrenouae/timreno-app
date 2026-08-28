// TIM RENO Quote Builder -- ported from the old TIMR Quote Builder.html.
// Swaps the embedded 20k-row array + localStorage for fetch() calls against
// the Flask app, and html2pdf() for a link to the server-rendered PDF route.
(function () {
  "use strict";

  const initial = JSON.parse(document.getElementById("timrInitialData").textContent);
  const CSRF_TOKEN = initial.csrfToken;

  const state = {
    quoteId: initial.quoteId,
    rooms: [],
    terms: [...initial.defaultTerms],
  };
  if (initial.existing) {
    state.rooms = initial.existing.rooms.map(r => ({
      name: r.name, notes: r.notes || "",
      items: r.items.map(i => ({
        product_id: i.product_id, description: i.description, unit: i.unit,
        brand: i.brand, qty: i.qty, unit_price: i.unit_price,
      })),
    }));
    state.terms = initial.existing.terms.length ? [...initial.existing.terms] : [...initial.defaultTerms];
  }
  let selectedRoomIndex = state.rooms.length ? 0 : -1;

  const $ = (id) => document.getElementById(id);
  const fmtMoney = (n) => (Number(n) || 0).toFixed(2);

  // ------------------------------------------------------------- rooms UI

  function refreshRoomSelect() {
    const sel = $("roomSelect");
    if (state.rooms.length === 0) {
      sel.innerHTML = '<option value="">— add a room/cabin first —</option>';
      selectedRoomIndex = -1;
      return;
    }
    if (selectedRoomIndex < 0 || selectedRoomIndex >= state.rooms.length) selectedRoomIndex = 0;
    sel.innerHTML = state.rooms.map((r, i) => `<option value="${i}">${escapeHtml(r.name)}</option>`).join("");
    sel.value = String(selectedRoomIndex);
  }
  $("roomSelect").addEventListener("change", (e) => {
    selectedRoomIndex = parseInt(e.target.value, 10);
  });

  function addRoom(name, notes) {
    state.rooms.push({ name: name.trim(), notes: notes || "", items: [] });
    selectedRoomIndex = state.rooms.length - 1;
  }

  $("addRoomBtn").addEventListener("click", () => {
    const name = prompt("Name this room / cabin (e.g. Kitchen, Manager's Office):");
    if (!name || !name.trim()) return;
    addRoom(name, "");
    refreshRoomSelect();
    renderCart();
  });

  function removeRoom(index) {
    const room = state.rooms[index];
    if (room.items.length > 0 && !confirm(`Remove "${room.name}" and its ${room.items.length} item(s)?`)) return;
    state.rooms.splice(index, 1);
    if (selectedRoomIndex >= state.rooms.length) selectedRoomIndex = state.rooms.length - 1;
    refreshRoomSelect();
    renderCart();
  }

  function removeItem(roomIndex, itemIndex) {
    state.rooms[roomIndex].items.splice(itemIndex, 1);
    renderCart();
  }

  // --------------------------------------------------------------- search

  let searchTimer = null;
  function scheduleSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 150);
  }
  ["searchQuery", "categorySelect", "brandSelect"].forEach(id => {
    $(id).addEventListener("input", scheduleSearch);
    $(id).addEventListener("change", scheduleSearch);
  });

  function runSearch() {
    const q = $("searchQuery").value.trim();
    const category = $("categorySelect").value;
    const brand = $("brandSelect").value;
    if (!q && !category && !brand) {
      $("searchResults").innerHTML = '<div class="empty-state">Type to search the Product Master.</div>';
      return;
    }
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (category) params.set("category", category);
    if (brand) params.set("brand", brand);
    fetch(`/quotes/products/search?${params.toString()}`)
      .then(r => r.json())
      .then(items => renderSearchResults(items))
      .catch(() => { $("searchResults").innerHTML = '<div class="empty-state">Search failed.</div>'; });
  }

  function renderSearchResults(items) {
    const box = $("searchResults");
    if (!items.length) {
      box.innerHTML = '<div class="empty-state">No matching items.</div>';
      return;
    }
    box.innerHTML = items.slice(0, 100).map((it, i) => `
      <div class="result-row" data-idx="${i}">
        <div style="min-width:0;">
          <span class="cat">${escapeHtml(it.category)}</span>
          ${escapeHtml(it.description)}
          ${it.brand ? `<span class="brand-tag">${escapeHtml(it.brand)}</span>` : ""}
        </div>
        <div class="price">${fmtMoney(it.cost_price)}</div>
      </div>
    `).join("");
    box.querySelectorAll(".result-row").forEach(row => {
      row.addEventListener("click", () => {
        const item = items[parseInt(row.dataset.idx, 10)];
        addItemToSelectedRoom({
          product_id: item.id, description: item.description, unit: item.unit,
          brand: item.brand, qty: 1, unit_price: item.cost_price,
        });
      });
    });
  }

  function addItemToSelectedRoom(item) {
    if (selectedRoomIndex < 0) { alert("Add a room first."); return; }
    state.rooms[selectedRoomIndex].items.push(item);
    renderCart();
  }

  // "Other" in the unit dropdown prompts for a custom unit and adds it as
  // a real option, rather than leaving a stray text field on the page.
  $("customUnit").addEventListener("change", () => {
    if ($("customUnit").value !== "Other") return;
    const custom = prompt("Enter a custom unit (e.g. sqm, litre):");
    if (custom && custom.trim()) {
      const opt = document.createElement("option");
      opt.value = custom.trim(); opt.textContent = custom.trim();
      $("customUnit").insertBefore(opt, $("customUnit").querySelector('option[value="Other"]'));
      $("customUnit").value = custom.trim();
    } else {
      $("customUnit").value = "Nos";
    }
  });

  $("customAddBtn").addEventListener("click", () => {
    const description = $("customDesc").value.trim();
    const unit = $("customUnit").value || "Nos";
    const qty = parseFloat($("customQty").value) || 0;
    const price = parseFloat($("customPrice").value) || 0;
    if (!description) { alert("Enter a description."); return; }
    addItemToSelectedRoom({ product_id: null, description, unit, brand: null, qty, unit_price: price });
    $("customDesc").value = ""; $("customQty").value = "1"; $("customPrice").value = "";
  });

  // ----------------------------------------------------------------- cart

  function roomSubtotal(room) {
    return room.items.reduce((s, i) => s + (i.qty || 0) * (i.unit_price || 0), 0);
  }

  function renderCart() {
    const box = $("cartBox");
    if (state.rooms.length === 0) {
      box.innerHTML = '<div class="empty-state">No rooms yet — add one above to start pricing.</div>';
      updateTotals();
      return;
    }
    box.innerHTML = state.rooms.map((room, ri) => `
      <div class="room-card">
        <div class="room-card-head">
          <b>${escapeHtml(room.name)}</b>
          <div style="display:flex; align-items:center; gap:10px;">
            <span class="room-subtotal">${fmtMoney(roomSubtotal(room))}</span>
            <button type="button" class="remove-link" data-remove-room="${ri}">Remove room</button>
          </div>
        </div>
        ${room.items.length ? `
        <table>
          <thead><tr><th>Description</th><th>Qty</th><th>Unit</th><th>Price</th><th>Amount</th><th></th></tr></thead>
          <tbody>
          ${room.items.map((item, ii) => `
            <tr class="item-row">
              <td>${escapeHtml(item.description)}${item.brand ? ` <span class="brand-tag">${escapeHtml(item.brand)}</span>` : ""}</td>
              <td class="qty-cell"><input type="number" step="1" value="${item.qty}" data-qty="${ri}:${ii}"></td>
              <td>${escapeHtml(item.unit)}</td>
              <td class="price-cell"><input type="number" step="0.01" value="${item.unit_price}" data-price="${ri}:${ii}"></td>
              <td>${fmtMoney((item.qty || 0) * (item.unit_price || 0))}</td>
              <td><button type="button" class="remove-link" data-remove-item="${ri}:${ii}">✕</button></td>
            </tr>
          `).join("")}
          </tbody>
        </table>
        ` : '<div class="empty-state">No items in this room yet.</div>'}
        <div style="padding:10px 12px;">
          <textarea placeholder="Room notes (optional)" data-room-notes="${ri}" style="width:100%; min-height:36px;">${escapeHtml(room.notes || "")}</textarea>
        </div>
      </div>
    `).join("");

    box.querySelectorAll("[data-remove-room]").forEach(btn =>
      btn.addEventListener("click", () => removeRoom(parseInt(btn.dataset.removeRoom, 10))));
    box.querySelectorAll("[data-remove-item]").forEach(btn =>
      btn.addEventListener("click", () => {
        const [ri, ii] = btn.dataset.removeItem.split(":").map(Number);
        removeItem(ri, ii);
      }));
    box.querySelectorAll("[data-qty]").forEach(inp =>
      inp.addEventListener("input", () => {
        const [ri, ii] = inp.dataset.qty.split(":").map(Number);
        state.rooms[ri].items[ii].qty = parseFloat(inp.value) || 0;
        renderCart();
      }));
    box.querySelectorAll("[data-price]").forEach(inp =>
      inp.addEventListener("input", () => {
        const [ri, ii] = inp.dataset.price.split(":").map(Number);
        state.rooms[ri].items[ii].unit_price = parseFloat(inp.value) || 0;
        renderCart();
      }));
    box.querySelectorAll("[data-room-notes]").forEach(ta =>
      ta.addEventListener("input", () => {
        state.rooms[parseInt(ta.dataset.roomNotes, 10)].notes = ta.value;
      }));

    updateTotals();
  }

  function updateTotals() {
    const subtotal = state.rooms.reduce((s, r) => s + roomSubtotal(r), 0);
    const vatPct = parseFloat($("metaVat").value) || 0;
    const vatAmt = subtotal * vatPct / 100;
    $("totalSubtotal").textContent = fmtMoney(subtotal);
    $("totalVat").textContent = fmtMoney(vatAmt);
    $("totalGrand").textContent = fmtMoney(subtotal + vatAmt);
  }
  $("metaVat").addEventListener("input", updateTotals);

  // ----------------------------------------------------------- import drawing

  let drawingReview = []; // [{name, sizeText, checked}]

  function setDrawingStatus(msg, kind) {
    const el = $("drawingStatus");
    el.textContent = msg;
    el.className = "save-status" + (kind ? ` is-${kind}` : "");
  }

  function renderDrawingReview() {
    const box = $("drawingReviewBox");
    if (!drawingReview.length) { box.innerHTML = ""; return; }
    box.innerHTML = `
      <table>
        <thead><tr><th></th><th>Room name</th><th>Approx. size (sq ft)</th></tr></thead>
        <tbody>
        ${drawingReview.map((r, i) => `
          <tr class="item-row">
            <td><input type="checkbox" data-drawing-check="${i}" ${r.checked ? "checked" : ""}></td>
            <td><input type="text" data-drawing-name="${i}" value="${escapeHtml(r.name)}"></td>
            <td><input type="text" data-drawing-size="${i}" value="${escapeHtml(r.sizeText)}" placeholder="e.g. 180"></td>
          </tr>
        `).join("")}
        </tbody>
      </table>
      <button type="button" id="addCheckedRoomsBtn" class="btn btn-secondary">Add checked rooms</button>
    `;
    box.querySelectorAll("[data-drawing-check]").forEach(cb =>
      cb.addEventListener("change", () => { drawingReview[parseInt(cb.dataset.drawingCheck, 10)].checked = cb.checked; }));
    box.querySelectorAll("[data-drawing-name]").forEach(inp =>
      inp.addEventListener("input", () => { drawingReview[parseInt(inp.dataset.drawingName, 10)].name = inp.value; }));
    box.querySelectorAll("[data-drawing-size]").forEach(inp =>
      inp.addEventListener("input", () => { drawingReview[parseInt(inp.dataset.drawingSize, 10)].sizeText = inp.value; }));
    $("addCheckedRoomsBtn").addEventListener("click", () => {
      const checked = drawingReview.filter(r => r.checked && r.name.trim());
      if (!checked.length) { setDrawingStatus("Check at least one room to add.", "error"); return; }
      checked.forEach(r => {
        const size = r.sizeText.trim();
        const notes = size ? `Approx. ${size} sq ft (from drawing)` : "";
        addRoom(r.name, notes);
      });
      refreshRoomSelect();
      renderCart();
      drawingReview = drawingReview.filter(r => !(r.checked && r.name.trim()));
      renderDrawingReview();
      setDrawingStatus(`Added ${checked.length} room(s).`, "success");
    });
  }

  $("analyzeDrawingBtn").addEventListener("click", () => {
    const fileInput = $("drawingFile");
    const file = fileInput.files && fileInput.files[0];
    if (!file) { setDrawingStatus("Choose a drawing file first.", "error"); return; }
    const fd = new FormData();
    fd.append("drawing_file", file);
    fd.append("csrf_token", CSRF_TOKEN);
    setDrawingStatus("Analyzing drawing… this can take up to a minute.");
    $("analyzeDrawingBtn").disabled = true;
    fetch("/quotes/import-drawing", {
      method: "POST",
      headers: { "X-CSRF-Token": CSRF_TOKEN },
      body: fd,
    })
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(body.error || "Drawing analysis failed.");
        return body;
      })
      .then((body) => {
        drawingReview = (body.rooms || []).map(r => ({
          name: r.name,
          sizeText: r.approx_sqft != null ? String(Math.round(r.approx_sqft)) : "",
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
      .finally(() => { $("analyzeDrawingBtn").disabled = false; });
  });

  // ---------------------------------------------------------------- terms

  function renderTerms() {
    $("termsBox").innerHTML = state.terms.map((t, i) => `
      <div class="term-row">
        <input type="text" value="${escapeHtml(t)}" data-term="${i}">
        <button type="button" class="remove-link" data-remove-term="${i}">✕</button>
      </div>
    `).join("");
    $("termsBox").querySelectorAll("[data-term]").forEach(inp =>
      inp.addEventListener("input", () => { state.terms[parseInt(inp.dataset.term, 10)] = inp.value; }));
    $("termsBox").querySelectorAll("[data-remove-term]").forEach(btn =>
      btn.addEventListener("click", () => {
        state.terms.splice(parseInt(btn.dataset.removeTerm, 10), 1);
        renderTerms();
      }));
  }
  $("addTermBtn").addEventListener("click", () => { state.terms.push(""); renderTerms(); });

  // ----------------------------------------------------------------- save

  function setStatus(msg, kind) {
    const el = $("saveStatus");
    el.textContent = msg;
    el.className = "save-status" + (kind ? ` is-${kind}` : "");
  }

  function buildPayload() {
    return {
      quote_id: state.quoteId,
      quote_number: $("metaQuoteNo").value.trim(),
      client_name: $("metaClient").value.trim(),
      project_name: $("metaProjectName").value.trim(),
      project_type: $("metaProjectType").value.trim(),
      location: $("metaLocation").value.trim(),
      quote_date: $("metaDate").value.trim(),
      vat_percent: parseFloat($("metaVat").value) || 5,
      job_notes: $("jobNotes").value,
      rooms: state.rooms,
      terms: state.terms.map(t => t.trim()).filter(Boolean),
    };
  }

  function save(finalize) {
    if (!$("metaQuoteNo").value.trim()) { alert("Enter a quote number first."); return; }
    const url = finalize ? "/quotes/save" : "/quotes/save-draft";
    setStatus("Saving…");
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": CSRF_TOKEN },
      body: JSON.stringify(buildPayload()),
    })
      .then(async (r) => {
        const body = await r.json();
        if (!r.ok) throw new Error(body.error || "Save failed.");
        return body;
      })
      .then((body) => {
        const wasNew = state.quoteId === null;
        state.quoteId = body.quote_id;
        if (wasNew) {
          // Redirect to the edit URL so the page reflects the persisted
          // state and the Download PDF link becomes available.
          window.location.href = `/quotes/${body.quote_id}/edit`;
          return;
        }
        setStatus(`Saved as ${finalize ? "finalized" : "draft"} — ${body.quote_number}`, "success");
      })
      .catch((err) => setStatus(err.message, "error"));
  }

  $("saveDraftBtn").addEventListener("click", () => save(false));
  $("saveBtn").addEventListener("click", () => save(true));

  // ---------------------------------------------------------------- utils

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  // ---------------------------------------------------------------- init

  refreshRoomSelect();
  renderCart();
  renderTerms();
  if (!$("metaDate").value) {
    const d = new Date();
    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    $("metaDate").value = `${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`;
  }
})();
