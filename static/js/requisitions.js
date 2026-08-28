// TIM RENO -- Material Requisition item builder (Item 5). Ports the Quote
// Builder / PO product-search pattern a third time (static/js/quote_builder.js:
// runSearch/renderSearchResults, static/js/purchases.js), but simpler than
// either: a requisition has one flat item list (no rooms, no per-line
// price -- price is filled in later, per vendor, on the Vendor portal), so
// this is just search-or-custom-add into a single array, kept in sync with
// a hidden JSON field the "Save item list" form posts (same
// hidden-field-synced-by-JS shape as templates/settings/documents.html's
// column editor).
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const itemsBox = $("mrItemsBox");
  if (!itemsBox) return; // items panel not on the page (locked/read-only)

  const initialEl = $("mrInitialData");
  const initial = initialEl ? JSON.parse(initialEl.textContent) : [];
  const state = {
    items: initial.map(i => ({
      product_id: i.product_id, description: i.description, unit: i.unit, quantity: i.quantity,
    })),
  };

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

  // --------------------------------------------------------------- search

  const searchQuery = $("mrSearchQuery");
  const categorySelect = $("mrCategorySelect");
  const brandSelect = $("mrBrandSelect");
  const resultsBox = $("mrSearchResults");

  let searchTimer = null;
  function scheduleSearch() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(runSearch, 150);
  }
  [searchQuery, categorySelect, brandSelect].forEach(el => {
    el.addEventListener("input", scheduleSearch);
    el.addEventListener("change", scheduleSearch);
  });

  function runSearch() {
    const q = searchQuery.value.trim();
    const category = categorySelect.value;
    const brand = brandSelect.value;
    if (!q && !category && !brand) {
      resultsBox.innerHTML = '<div class="empty-state">Type to search the Product Master.</div>';
      return;
    }
    const params = new URLSearchParams();
    if (q) params.set("q", q);
    if (category) params.set("category", category);
    if (brand) params.set("brand", brand);
    fetch(`/requisitions/products/search?${params.toString()}`)
      .then(r => r.json())
      .then(items => renderSearchResults(items))
      .catch(() => { resultsBox.innerHTML = '<div class="empty-state">Search failed.</div>'; });
  }

  function renderSearchResults(items) {
    if (!items.length) {
      resultsBox.innerHTML = '<div class="empty-state">No matching items.</div>';
      return;
    }
    resultsBox.innerHTML = items.slice(0, 100).map((it, i) => `
      <div class="result-row" data-idx="${i}">
        <div style="min-width:0;">
          <span class="cat">${escapeHtml(it.category)}</span>
          ${escapeHtml(it.description)}
          ${it.brand ? `<span class="brand-tag">${escapeHtml(it.brand)}</span>` : ""}
        </div>
        <div class="price">${escapeHtml(it.unit)}</div>
      </div>
    `).join("");
    resultsBox.querySelectorAll(".result-row").forEach(row => {
      row.addEventListener("click", () => {
        const item = items[parseInt(row.dataset.idx, 10)];
        addItem({ product_id: item.id, description: item.description, unit: item.unit, quantity: 1 });
      });
    });
  }
  resultsBox.innerHTML = '<div class="empty-state">Type to search the Product Master.</div>';

  // --------------------------------------------------------- custom items

  $("mrCustomAddBtn").addEventListener("click", () => {
    const description = $("mrCustomDesc").value.trim();
    const unit = $("mrCustomUnit").value || "Nos";
    const quantity = parseFloat($("mrCustomQty").value) || 0;
    if (!description) { alert("Enter a description."); return; }
    if (quantity <= 0) { alert("Enter a positive quantity."); return; }
    addItem({ product_id: null, description, unit, quantity });
    $("mrCustomDesc").value = "";
    $("mrCustomQty").value = "1";
  });

  // ------------------------------------------------------------ item list

  function addItem(item) {
    state.items.push(item);
    render();
  }

  function removeItem(idx) {
    state.items.splice(idx, 1);
    render();
  }

  function render() {
    if (!state.items.length) {
      itemsBox.innerHTML = '<div class="empty-state">No items yet -- search or add a custom item above.</div>';
      sync();
      return;
    }
    itemsBox.innerHTML = `
      <table>
        <thead><tr><th>Description</th><th>Unit</th><th>Qty</th><th></th></tr></thead>
        <tbody>
        ${state.items.map((item, i) => `
          <tr class="item-row">
            <td>${escapeHtml(item.description)}</td>
            <td>${escapeHtml(item.unit)}</td>
            <td class="qty-cell"><input type="number" step="0.01" min="0.01" value="${item.quantity}" data-qty="${i}"></td>
            <td><button type="button" class="remove-link" data-remove="${i}">✕</button></td>
          </tr>
        `).join("")}
        </tbody>
      </table>
    `;
    itemsBox.querySelectorAll("[data-remove]").forEach(btn =>
      btn.addEventListener("click", () => removeItem(parseInt(btn.dataset.remove, 10))));
    itemsBox.querySelectorAll("[data-qty]").forEach(inp =>
      inp.addEventListener("input", () => {
        state.items[parseInt(inp.dataset.qty, 10)].quantity = parseFloat(inp.value) || 0;
        sync();
      }));
    sync();
  }

  function sync() {
    $("mrItemsJson").value = JSON.stringify(state.items);
  }

  render();
})();
