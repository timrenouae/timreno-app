// TIM RENO -- Purchase Order product search (Item 4). Ports the Quote
// Builder's debounced search box + category/brand filters + clickable
// result-row pattern (static/js/quote_builder.js: runSearch/
// renderSearchResults) but simpler: a PO has one flat item list, not
// per-room carts, so a click just fills the existing "Add item" form's
// product_id/quantity/unit_price fields -- the form itself (route, field
// names, submit) is unchanged.
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const searchQuery = $("poSearchQuery");
  const categorySelect = $("poCategorySelect");
  const brandSelect = $("poBrandSelect");
  const resultsBox = $("poSearchResults");
  const productIdInput = $("product_id");
  const productLabel = $("poSelectedProduct");
  const quantityInput = $("quantity");
  const unitPriceInput = $("unit_price");

  if (!searchQuery || !resultsBox) return; // panel not on the page (PO not draft/sent)

  const fmtMoney = (n) => (Number(n) || 0).toFixed(2);

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c => (
      { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
    ));
  }

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
    fetch(`/purchases/products/search?${params.toString()}`)
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
        <div class="price">${fmtMoney(it.cost_price)}</div>
      </div>
    `).join("");
    resultsBox.querySelectorAll(".result-row").forEach(row => {
      row.addEventListener("click", () => {
        const item = items[parseInt(row.dataset.idx, 10)];
        selectProduct(item);
      });
    });
  }

  function selectProduct(item) {
    productIdInput.value = item.id;
    if (productLabel) {
      productLabel.textContent =
        `${item.category} — ${item.description}${item.brand ? " (" + item.brand + ")" : ""}`;
    }
    // Convenience defaults the user can still edit before submitting --
    // qty defaults to 1, unit price defaults to the product's current cost
    // price, matching the Quote Builder's search-result click behavior.
    if (!quantityInput.value) quantityInput.value = "1";
    unitPriceInput.value = item.cost_price;
  }

  // Initial state before any search has run.
  resultsBox.innerHTML = '<div class="empty-state">Type to search the Product Master.</div>';
})();
