/* Copy HTML plus plain text so Word receives real tables, not tab-separated text. */
(() => {
  function selectionCopy(element) {
    const selection = window.getSelection();
    const oldRanges = Array.from({length: selection.rangeCount}, (_, i) => selection.getRangeAt(i).cloneRange());
    const range = document.createRange();
    range.selectNodeContents(element);
    selection.removeAllRanges();
    selection.addRange(range);
    const ok = document.execCommand('copy');
    if (ok) {
      selection.removeAllRanges();
      oldRanges.forEach(r => selection.addRange(r));
    }
    return ok;
  }
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-copy-word]');
    if (!button) return;
    let element = document.getElementById(button.dataset.copyWord);
    const status = document.getElementById('word-copy-status');
    if (!element) return;
    document.querySelectorAll('[data-word-copy-temp]').forEach(node => node.remove());
    let temporary = null;
    let copied = false;
    if (button.hasAttribute('data-copy-values-only') || button.hasAttribute('data-copy-table-data-only')) {
      temporary = element.cloneNode(true);
      temporary.removeAttribute('id');
      temporary.setAttribute('data-word-copy-temp', '');
      if (button.hasAttribute('data-copy-values-only')) {
        temporary.querySelectorAll('tr').forEach(row => {
          if (row.cells.length > 1) row.deleteCell(0);
        });
      } else {
        const table = temporary.querySelector('table');
        table.querySelectorAll('thead, tfoot, [data-copy-exclude]').forEach(node => node.remove());
        temporary.replaceChildren(table);
      }
      // Render offscreen so both innerText and selection-copy work.
      temporary.style.position = 'fixed';
      temporary.style.left = '-10000px';
      temporary.style.width = '600px';
      document.body.appendChild(temporary);
      element = temporary;
    }
    button.disabled = true;
    const html = '<html><head><meta charset="utf-8"></head><body>' + element.innerHTML + '</body></html>';
    try {
      if (!navigator.clipboard?.write || !window.ClipboardItem) throw new Error('Use selection copy');
      await navigator.clipboard.write([new ClipboardItem({
        'text/html': new Blob([html], {type: 'text/html'}),
        'text/plain': new Blob([element.innerText], {type: 'text/plain'})
      })]);
      copied = true;
      status.textContent = 'Copied. Paste in Word using Keep Source Formatting.';
    } catch (error) {
      try {
        if (!selectionCopy(element)) throw new Error('Clipboard blocked');
        copied = true;
        status.textContent = 'Copied. Paste in Word using Keep Source Formatting.';
      } catch (fallbackError) {
        status.textContent = 'Clipboard blocked. The area is selected — press Ctrl+C, then paste in Word.';
      }
    } finally {
      if (temporary && copied) temporary.remove();
      button.disabled = false;
    }
  });
})();
