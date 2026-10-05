/* One serial per line. Only Preview runs the browser's line-count check.
   Allocation, serial normalization, uniqueness and signed previews remain server-owned. */
(() => {
  const form = document.querySelector("[data-work-order-entry]");
  if (!form) return;
  const list = form.querySelector("[data-entry-items]");
  const status = form.querySelector("[data-entry-status]");
  const preview = form.querySelector("#generated-table");
  let dragged = null;
  const rows = () => Array.from(list.querySelectorAll("tr"));
  const clearErrors = () => {
    rows().forEach((row) => {
      row.querySelector(".serial-input").removeAttribute("aria-invalid");
      const feedback = row.querySelector(".serial-feedback");
      feedback.hidden = true;
      feedback.textContent = "";
    });
  };
  const invalidate = () => {
    clearErrors();
    form.querySelectorAll('[name="action"][value="push"]').forEach((button) => {
      button.disabled = true;
    });
    if (preview) {
      preview.hidden = true;
    }
    if (status) {
      status.textContent =
        "Inputs changed. Preview the work order again before saving.";
    }
  };
  const updateOrder = () => {
    rows().forEach((row, index) => {
      const number = row.querySelector(".item-number");
      if (number) number.textContent = index + 1;
      const up = row.querySelector(".entry-up"),
        down = row.querySelector(".entry-down");
      if (up) up.disabled = index === 0;
      if (down) down.disabled = index === rows().length - 1;
    });
    const order = form.querySelector("#item-order");
    if (order)
      order.value = rows()
        .map((row) => row.dataset.itemId)
        .join(",");
  };
  let nextId = 0;
  const identifyRows = () => {
    rows().forEach((row) => {
      if (row.dataset.entryId) return;
      row.dataset.entryId = ++nextId;
      const feedback = row.querySelector(".serial-feedback");
      feedback.id = `serial-feedback-${nextId}`;
      row
        .querySelector(".serial-input")
        .setAttribute("aria-describedby", feedback.id);
    });
  };
  form.addEventListener("input", invalidate);
  form.addEventListener("change", invalidate);
  const isNA = (value) => value.replace(/[^A-Za-z0-9]/g, "").toUpperCase().match(/^(NA|NAN|NONE|NULL)$/);
  form.addEventListener("submit", (event) => {
    // Legacy direct POSTs and database validation keep their existing contracts.
    if (event.submitter && event.submitter.value !== "preview") return;
    clearErrors();
    let first = null;
    rows().forEach((row) => {
      const qty = row.querySelector("[data-entry-quantity]");
      if (!qty.validity.valid) return; // Native required/min/step checks handle this.
      const expected = Number(qty.value);
      const serials = row.querySelector(".serial-input");
      const lines = serials.value
        .split(/\r\n?|\n/)
        .map((line) => line.trim())
        .filter(Boolean);
      if (lines.some(isNA)) return;
      const actual = lines.length;
      if (actual === expected) return;
      const difference = Math.abs(expected - actual);
      const feedback = row.querySelector(".serial-feedback");
      feedback.textContent = `${actual} serial ${actual === 1 ? "line" : "lines"} for Qty ${expected}. ${actual < expected ? "Add" : "Remove"} ${difference} ${difference === 1 ? "line" : "lines"}, or change Qty.`;
      feedback.hidden = false;
      serials.setAttribute("aria-invalid", "true");
      first ||= serials;
    });
    if (first) {
      event.preventDefault();
      form
        .querySelectorAll('[name="action"][value="push"]')
        .forEach((button) => {
          button.disabled = true;
        });
      if (preview) preview.hidden = true;
      if (status)
        status.textContent =
          "Check the serial lines below, then preview again.";
      first.focus();
    }
  });
  list.addEventListener("click", (event) => {
    const up = event.target.closest(".entry-up"),
      down = event.target.closest(".entry-down");
    if (up || down) {
      const row = event.target.closest("tr");
      const neighbor = up ? row.previousElementSibling : row.nextElementSibling;
      if (!neighbor) return;
      if (up) list.insertBefore(row, neighbor);
      else list.insertBefore(neighbor, row);
      updateOrder();
      invalidate();
      (up || down).focus();
    }
    if (event.target.closest(".remove-row") && rows().length > 1) {
      const row = event.target.closest("tr");
      const next = row.nextElementSibling || row.previousElementSibling;
      row.remove();
      updateOrder();
      invalidate();
      next.querySelector("input").focus();
    }
  });
  const add = form.querySelector("#add-item");
  if (add)
    add.addEventListener("click", () => {
      list.appendChild(
        document.querySelector("#blank-row").content.cloneNode(true),
      );
      identifyRows();
      updateOrder();
      invalidate();
      rows().at(-1).querySelector("input").focus();
    });
  list.addEventListener("dragstart", (event) => {
    if (!event.target.closest(".reorder-handle")) return;
    dragged = event.target.closest("tr");
    event.dataTransfer.effectAllowed = "move";
    dragged.classList.add("table-active");
  });
  list.addEventListener("dragover", (event) => {
    if (!dragged) return;
    event.preventDefault();
    const target = event.target.closest("tr");
    if (!target || target === dragged) return;
    const box = target.getBoundingClientRect();
    list.insertBefore(
      dragged,
      event.clientY < box.top + box.height / 2 ? target : target.nextSibling,
    );
    updateOrder();
    invalidate();
  });
  const finishDrag = () => {
    if (dragged) dragged.classList.remove("table-active");
    dragged = null;
    updateOrder();
  };
  list.addEventListener("drop", (event) => {
    if (dragged) event.preventDefault();
    finishDrag();
  });
  list.addEventListener("dragend", finishDrag);
  identifyRows();
  updateOrder();
})();
