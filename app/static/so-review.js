(() => {
  const body = document.getElementById('approved-items');
  if (!body) return;
  const order = document.getElementById('approved-item-order');
  const rowPayload = document.getElementById('approved-item-rows');
  let dragged = null;
  let dirty = false;
  const productionDate = document.getElementById('review-production-date');
  if (productionDate) {
    productionDate.addEventListener('input', () => { productionDate.setCustomValidity(''); dirty = true; });
    productionDate.addEventListener('change', () => {
      productionDate.setCustomValidity('');
      if (productionDate.value) {
        const weekday = new Date(productionDate.value + 'T00:00:00').getDay();
        if (weekday === 0 || weekday === 6) productionDate.setCustomValidity('Choose a weekday for production.');
      }
      dirty = true;
    });
  }
  function fieldValue(row, selector, label) {
    const field = row.querySelector(selector);
    if (field) return field.value || '';
    const cell = row.querySelector(`[data-label="${label}"]`);
    return cell ? cell.textContent.trim() : '';
  }
  function rowData(row) {
    return {
      id: row.dataset.itemId,
      item: fieldValue(row, '.review-item', 'Product'),
      quantity: fieldValue(row, '.review-quantity', 'Qty'),
      configuration: fieldValue(row, '.review-configuration', 'Configuration'),
      inventory_site: fieldValue(row, '.review-site', 'Site')
    };
  }
  function update(changed = true) {
    [...body.children].forEach((row, index) => row.querySelector('.approval-number').textContent = index + 1);
    order.value = [...body.children].map(row => row.dataset.itemId).join(',');
    if (rowPayload) rowPayload.value = JSON.stringify([...body.children].map(rowData));
    if (changed) dirty = true;
  }
  function addManualRow() {
    const id = `manual-${crypto.randomUUID ? crypto.randomUUID() : Date.now().toString(36) + Math.random().toString(36).slice(2)}`;
    const row = document.createElement('tr');
    row.dataset.itemId = id;
    row.innerHTML = '<td><span class="approval-drag" draggable="true" title="Drag to reorder">☰</span> <span class="approval-number"></span></td>' +
      '<td><input class="form-control form-control-sm review-item" required></td>' +
      '<td><input class="form-control form-control-sm review-quantity" type="number" min="0" step="1" value="1" required></td>' +
      '<td><input class="form-control form-control-sm review-site"></td>' +
      '<td><button type="button" class="btn btn-outline-secondary approval-up" aria-label="Move row up">↑</button> <button type="button" class="btn btn-outline-secondary approval-down" aria-label="Move row down">↓</button> <button type="button" class="btn btn-outline-danger approval-delete" aria-label="Delete manual row">×</button></td>';
    body.appendChild(row);
    row.querySelector('.review-item').focus();
    update();
  }
  body.addEventListener('click', event => {
    const row = event.target.closest('tr');
    if (!row) return;
    if (event.target.closest('.approval-delete') && row.dataset.itemId.startsWith('manual-')) row.remove();
    else if (event.target.closest('.approval-up') && row.previousElementSibling) body.insertBefore(row, row.previousElementSibling);
    else if (event.target.closest('.approval-down') && row.nextElementSibling) body.insertBefore(row.nextElementSibling, row);
    else return;
    update();
  });
  body.addEventListener('input', event => {
    if (event.target.closest('.review-item, .review-quantity, .review-configuration, .review-site')) update();
  });
  document.getElementById('add-review-row')?.addEventListener('click', addManualRow);
  body.addEventListener('dragstart', event => {
    if (!event.target.closest('.approval-drag')) { event.preventDefault(); return; }
    dragged = event.target.closest('tr');
    event.dataTransfer.effectAllowed = 'move';
    event.dataTransfer.setData('text/plain', dragged.dataset.itemId);
    dragged.classList.add('table-active');
  });
  body.addEventListener('dragover', event => {
    if (!dragged) return;
    event.preventDefault();
    const target = event.target.closest('tr');
    if (!target || target === dragged) return;
    const rect = target.getBoundingClientRect();
    body.insertBefore(dragged, event.clientY < rect.top + rect.height / 2 ? target : target.nextSibling);
    update();
  });
  body.addEventListener('drop', event => { event.preventDefault(); });
  body.addEventListener('dragend', () => {
    if (dragged) dragged.classList.remove('table-active');
    dragged = null;
  });
  document.getElementById('restore-sheet-order').addEventListener('click', () => {
    const rows = new Map([...body.children].map(row => [row.dataset.itemId, row]));
    JSON.parse(document.getElementById('sheet-item-order').textContent).forEach(id => { if (rows.get(id)) body.appendChild(rows.get(id)); });
    update();
  });
  document.getElementById('so-approval-form').addEventListener('submit', () => {
    [...body.children].forEach(row => {
      const isManual = row.dataset.itemId.startsWith('manual-');
      const data = rowData(row);
      if (isManual && !data.item.trim() && !data.configuration.trim() && !data.inventory_site.trim()) row.remove();
    });
    update(false);
    dirty = false;
  });
  window.addEventListener('beforeunload', event => {
    if (dirty) { event.preventDefault(); event.returnValue = ''; }
  });
  update(false);
})();
