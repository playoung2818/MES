/* Display-only search: never modifies schedule data or capacity totals. */
(() => {
  const input = document.getElementById('planning-search');
  if (!input) return;
  const workspace = input.closest('.planning-workspace');
  const status = document.getElementById('planning-search-status');
  const empty = document.getElementById('planning-search-empty');
  const hiddenClass = 'planning-search-hidden';

  function filterOrders() {
    const terms = input.value.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
    const rows = [...workspace.querySelectorAll('.order-line[data-wo]')];
    let matches = 0;
    rows.forEach(row => {
      const text = `${row.dataset.wo} ${row.textContent}`.toLocaleLowerCase();
      const visible = terms.every(term => text.includes(term));
      row.classList.toggle(hiddenClass, !visible);
      if (visible) matches += 1;
    });
    // Hide empty date cards while searching, keeping their data intact.
    workspace.querySelectorAll('.day-card').forEach(card => {
      const hasMatch = [...card.querySelectorAll('.order-line[data-wo]')]
        .some(row => !row.classList.contains(hiddenClass));
      card.parentElement.classList.toggle(hiddenClass, terms.length > 0 && !hasMatch);
    });
    empty.hidden = !terms.length || matches > 0;
    status.textContent = terms.length
      ? `${matches} of ${rows.length} orders match. Capacity totals remain unchanged.`
      : 'Search filters orders only; capacity totals remain unchanged.';
  }

  input.addEventListener('input', filterOrders);
  document.getElementById('planning-search-clear').addEventListener('click', () => {
    input.value = '';
    filterOrders();
    input.focus();
  });
  filterOrders();
})();
