# MES design preview

Open `index.html` in a browser. The file includes all styles, icons, sample records, and interaction code. It needs no server, package installation, or internet connection.

This is a design prototype, not a replacement for the Flask app. It does not connect to Google Sheets, MRP, or the database. Changes stay in browser memory and reset when you reload the page. All customers, orders, dates, and serial numbers in this preview are sample data.

## Browse the screens

The top navigation opens sales orders, saved work orders, and production planning. Select Open order to enter quantities and serial numbers. In Production planning, select Review & schedule orders to inspect source changes and approve a sample order.

The New blank work order action opens manual entry. Saved work orders provide revision and next-release actions. Direct screen links use URL fragments such as `index.html#planning` and `index.html#review`.

## What changed

The preview keeps the existing NEOUSYS brand, clover mark, green palette, top navigation, and table-based workspace. Shared spacing, control sizes, and text styles make the screens consistent. Tables become labeled rows on narrow screens so actions remain visible.

Navigation uses Sales orders and Work orders instead of SO and WO. Preview work order replaces Generate Table. Save work order replaces Push to DB. Approve sales order replaces Push Approved SO. Review & schedule orders describes the approval workflow more accurately than Assign Production Date.

The home page is an order picker with search, order numbers, customer names, and an Open order action. Product details, quantities, dates, and purchase orders remain in the order editor. Search still matches purchase orders and products. Review actions stay in Production planning, and manual entry stays below the list.

Ship date replaces the ambiguous L/T label. Notices explain that preview, approval, and schedule saves are separate actions. The planning legend retains the existing rule: a saved work order marks an order as Picked, but does not confirm physical picking.

## Try the interactions

Search sales orders. Open an order, enter quantities and serial numbers, then preview a work order. Change an input after preview to see the save action disappear until you preview again. Save a sample release and open the next release to see the remaining quantities.

Paste serial numbers into each item's text area, one per line. There is no separate scan field, Add serial button, or live count. Only Preview work order compares the non-empty line count with the quantity. Blank lines are ignored.

A mismatch shows the entered count, expected quantity, and how many lines to add or remove. Correct the list or quantity, then preview again. The preview is available when all item counts match.

Each non-empty line counts once, including repeated serials and NA. The preview preserves entry order, repeated values, and leading zeros. It trims surrounding spaces but does not split lines on commas or semicolons. There are no duplicate warnings in this HTML prototype. The real app's server-side serial rules remain unchanged.

Quantities above the approved allocation produce errors. Manual work orders provide customer validation and Add item and Remove item actions. The Word copy buttons copy the customer values or picking rows separately. Actual Word paste formatting still needs a workstation test.

In planning, change a production date. The schedule shows the unsaved change until you choose Save schedule or Discard changes. In review, weekend production dates produce an error. Approval adds or updates the sample order without changing saved work orders.

## Continue polishing

Edit the CSS variables at the top of `index.html` to change shared colors and typography. Screen functions and sample data are in the script below the page shell. Save the file, then reload the browser to see the next version.

The approved design is also integrated into the Flask templates and shared workspace assets. See the repository README for live-app behavior and isolated browser tests. This file remains a standalone sample-data prototype. The demo validation does not replace the app's database checks, transaction rules, or server-side serial parser.
