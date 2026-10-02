(() => {
  const body = document.getElementById('approved-items');
  if (!body) return;
  const order = document.getElementById('approved-item-order');
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
  function update(changed = true) {
    [...body.children].forEach((row, index) => row.querySelector('.approval-number').textContent = index + 1);
    order.value = [...body.children].map(row => row.dataset.itemId).join(',');
    if (changed) dirty = true;
  }
  body.addEventListener('click', event => {
    const row = event.target.closest('tr');
    if (!row) return;
    if (event.target.closest('.approval-up') && row.previousElementSibling) body.insertBefore(row, row.previousElementSibling);
    else if (event.target.closest('.approval-down') && row.nextElementSibling) body.insertBefore(row.nextElementSibling, row);
    else return;
    update();
  });
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
    JSON.parse(document.getElementById('sheet-item-order').textContent).forEach(id => body.appendChild(rows.get(id)));
    update();
  });
  document.getElementById('so-approval-form').addEventListener('submit', () => { update(false); dirty = false; });
  window.addEventListener('beforeunload', event => {
    if (dirty) { event.preventDefault(); event.returnValue = ''; }
  });
  update(false);
})();
