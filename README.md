# MES / WO Serial Entry

Internal Flask web app for syncing open sales orders from Google Sheets, entering serial numbers by SO/item, and generating a clean table for work-order documentation.

## Current workflow

```text
Google Sheet: PDF_WO
Worksheet: Open Sales Order
        ↓
Production Planning → Sync Google Sheet (stage snapshots)
        ↓
Review differences + reorder SO items
        ↓
Push Approved SO → approved_work_orders
        ↓
Planning and WO generation read the same approved SO data
        ↓
Select Sales Order → view its approved item order
        ↓
Paste / scan serial numbers into item text boxes
        ↓
Generate Table preview
        ↓
Check table
        ↓
Push to DB
```

## Main behavior

- Google Sheets are read only by the explicit sync action; there is no Excel upload.
- Open SOs, new WO generation, and planning read approved, active `approved_work_orders` rows.
- **New Blank WO** supports manual entry without any Google Sheet record or connection.
- **Sync Google Sheet** updates review snapshots only, never approved fields or WOs.
- **Push Approved SO** publishes reviewed SO data and item order to `approved_work_orders`.
- Sales orders are grouped by SO number.
- After selecting an SO, all items under that SO are displayed.
- Each item has:
  - Product number / item
  - Quantity
  - Serial number text box
  - Notes / Deviation text box
- Serial numbers are pruned and sorted using logic based on:
  - https://github.com/playoung2818/Serial-Number-Prune
- One SO can have multiple independent WOs (P1, P2, ...), each with its own Document #.
- Duplicate actual serial numbers across items or saved WOs are rejected. `NA` remains visible and is exempt.
- Item rows can be reordered by drag/drop before preview/push.
- The generated output is an HTML preview table shown on the SO page.
- WO table preview does not write DB. **Push to DB** saves only that WO.
- SO approval and schedule saves are separate explicit actions; they do not create WOs.

## WO History module

Use the navbar link:

```text
WO History
```

This module pulls saved/generated records directly from the DB table:

```text
WO Details
```

It shows all pushed WO records, release numbers, document numbers, dates, item counts, serial counts, and a detail table with:

```text
Item | Qty | Serial Numbers | Notes/Deviation
```

Only records that have been pushed with **Push to DB** appear here. Each push updates the `pushed_at` timestamp.

## Production Planning

The **Production Planning** button sits at the top right of the global navigation.
Open `/production_planning` for the module adapted from
[playoung2818/MRP_System](https://github.com/playoung2818/MRP_System), upstream
commit `f37fe90861187b8806cd6ac6fb3ea3fbb2694578` (`Webpage/server.py` and
`Webpage/ui.py`, `PRODUCTION_TPL`). Its UI uses the MES green styling.

- Weekly labor capacity (90 hours/week), family counts, large-order and review
  indicators; Nuvo / NRU / SEMIL / F = 1 hour/unit, POC = 0.5, regardless of
  customer. Other models need review. Your approved
  item order is never re-sorted by product name or PDF ordering.
- Day-by-day production schedule; date edits and finished-goods moves are staged
  until **Save Schedule**. Whole batches commit together, or roll back on error.
- Placeholder 7/4 and 12/31 dates, missing dates, and future weekend dates appear
  under Unassigned L/T. Production assignments must be today/future weekdays.
- The Finished Goods / Past Dates panel includes explicit finished goods and past
  schedule dates, matching the upstream grouping. A past date does not prove completion.

### Picked status and partial-WO rule

- **Any saved WO in `WO Details` automatically marks its SO as Picked.**
  Generating a preview does not count; only **Push to DB** creates a saved WO.
- **Picked Qty is read-only**, calculated from the saved item quantities across
  all partial WOs for the same SO. It does not come from an external picked-status
  API, `wo_structured.Picked`, or manual picked-quantity overrides.
- Match product names (trimmed, case-insensitive), and cap each product's picked
  quantity at its planned quantity. **Remaining = Planned − Picked.**
  Unpicked quantities and their labor remain in production planning.
- A revision replaces that WO's quantities; it does not count as another release.
  Reload planning after saving/revising a WO to update the totals.
- Accessories do not inflate system-unit counts. Remaining labor is calculated
  per product/family, including mixed-family partials. Fully picked unknown
  models have zero remaining labor.
- **Row colors:** Picked = green, no saved WO = NA/white. **Red takes priority**
  when production is on or after the ship date. No alternating row colors.

**Example:** an SO has 10 units. Saving P1 for 4 units means Picked Qty = 4 and
Remaining = 6. Saving P2 for another 3 means Picked Qty = 7 and Remaining = 3.
The SO is Picked after P1 even though some quantity is still unpicked.

### Google sync, diff review, and approved SOs

Use **Assign Production Date** (`/production_planning/orders`) from Production Planning.
The summary is sorted by **L/T ascending**, with missing/invalid dates last and
SO number as the tie-breaker. The **Status** dropdown supports Pending review
(default), All statuses, Legacy, Unapproved, Changed, Removed, Approved, and
Retired. Status filtering can be combined with SO/customer/PO search.

1. **Sync Google Sheet** reads the complete `PDF_WO` / `Open Sales Order` sheet.
   It saves previous/latest snapshots in the `approved_work_orders` table, and
   shows changes since the previous sync and since the last approval. Empty,
   malformed, or partially skipped reads are rejected without retiring any SOs.
2. Open **Review / reorder**. Review customer/PO/date/terms/site changes,
   added/removed products, quantities, and Google order changes separately.
   Drag handles or use up/down buttons; **Use Google item order** is optional.
   Existing approved order is retained when merging updated content; new items
   are appended for review. Configuration is retained and initializes WO notes.
   **L/T** (the Google Lead Time / Ship Date) appears in the list and detail page.
   The review page also displays **Earliest Material Ready Date** directly from
   the existing MRP-managed `so_material_readiness.earliest_material_ready_date`,
   matched by `"QB Num"` to the SO number. This is read-only: it does not modify
   readiness data or automatically set/restrict the Production Date. Missing
   records, undetermined dates, or unavailable readiness data are labeled clearly.
   During review, optionally choose a **Production Date** (today/future weekdays).
   Existing future dates are prefilled; blank preserves the existing schedule,
   including finished-goods status. Assigning a date returns finished goods to
   production, like the calendar's production-date assignment.
3. **Push Approved SO** publishes the reviewed items and your chosen order,
   and saves an assigned production date in `production_overrides` in the same
   transaction. Invalid dates or save failures leave both SO and schedule unchanged.
   Only approved, active SOs appear under Open SOs and in planning. Reordering an
   already approved SO is possible by selecting **All statuses** or **Approved**. Approval itself does
   not create or revise a WO.
4. An SO missing from a successful sync is **automatically excluded** from Open
   SOs, production planning, and new WO generation. No removal review/retirement
   approval is required, and it leaves the Pending review list. Stored approved
   data and saved WOs are preserved; existing saved WOs can still be revised.
   Already-flagged removals are excluded without another sync. If an SO reappears,
   it stays inactive until explicitly approved again (shown as Unapproved).

The three source snapshots are previous, latest, and last-approved; they are
bounded state, **not an unlimited historical sync log**. Item IDs are preserved
by product/configuration/site and occurrence. Unambiguous product matches also
retain their IDs across configuration/site edits. Repeated identical lines lack
a Google row ID and are flagged for careful review. Source and approved revision
checks reject stale approval submissions; WO pushes also reject previews made
against an older approved revision. Approval cannot reduce product quantities
below saved WO allocations.

**Source and scope:** planning and new WO generation both read approved, active,
Google-present `public.approved_work_orders`. Planning excludes Drop Ship items. Picked quantities come
from `WO Details`. Neither page reads Google or `open_sales_orders` directly.
The MRP-managed `open_sales_orders` table is left untouched. Scheduling remains
per SO/QB number, not per partial-release UUID. Manual WOs without an approved
SO are not added to planning. Upstream PDF-viewer links are not imported.

**Shared writes:** explicit schedule saves update MRP's `production_overrides`;
changes also affect the MRP schedule. MES picked status and quantities now ignore
`wo_structured.Picked` and `wo_picked_qty_overrides` entirely. Legacy overrides are
preserved but cannot be edited from MES (`POST /api/wo_picked_qty` returns 409).
Planning never inserts or modifies `WO Details`; use the WO editor / Push to DB
for quantity changes, then reload planning. GET/reload only reads data. New or
upgraded installs require syncing and approving SOs before planning/generation.
Schedule requests validate source keys/dates and require a session CSRF header.
Existing shared tables are not replaced. This MES rule treats a saved WO as
picked by definition; it does not separately confirm physical warehouse picking.

The full-database backup below includes `approved_work_orders`, `WO Details`,
production schedules, and all other objects/data in the configured database.

## Copy into the Word template

Preview and saved WO detail pages provide two separate rich-text copy areas,
matching the layout inspected in `WO09-2026.docx`:

1. **Copy header for Word**: copies only the Customer, Customer PO #,
   and NTA Order ID values as a three-row, one-column table. The on-screen
   labels and Customer details heading are not copied. Document Number and Date
   are not included.
2. **Copy picking table for Word**: item data rows only, retaining the Product
   Number / QTY / S/N / Notes-Deviation / Check column positions. The heading,
   column labels, End of Part row, and signature fields are not copied.

Paste each area in the corresponding place in Word using **Keep Source Formatting**.
The copy uses HTML tables plus plain text, with a selection-copy fallback for
browsers without the rich clipboard API. It does not modify the Word file directly.
Document Number and Generated Date remain stored in DB and displayed on saved
WO details, but neither is included in the copied content.
Browser clipboard formatting is implemented but actual Word paste rendering should
be checked on the operator workstation.

## Database

The app reuses these existing tables; the approval workflow adds **no tables**:

| Table | Purpose |
|---|---|
| `approved_work_orders` | Approved SO data/order plus bounded Google snapshots (not saved WO releases) |
| `WO Details` | Saved WO releases and serials |
| `production_overrides` | Shared MRP production dates / finished goods |
| `wo_picked_qty_overrides` | Legacy MRP table, ignored for MES picked quantities |

The former `sales_order` or `approved_sales_orders` table is renamed to
`approved_work_orders` on startup, before table creation, with a full JSON backup
at `data/backups/*_before_rename_*.json`. This is a rename, not a second
SO table: records, approvals, snapshots, and revisions are preserved. If both
names exist, a recognized empty leftover is backed up and removed under a table
lock. If both contain data (or the schema is unexpected), startup stops rather
than guessing or merging records. Restart older app processes after upgrading
so they do not recreate the legacy name.
The SO-number column remains named `sales_order`; `open_sales_orders` is unchanged.

On initial approval-workflow upgrades, legacy rows are backed up before adding
review columns (`data/backups/*_before_approval_*.json`). Existing
customer/PO/items are preserved, but legacy rows are **unapproved and inactive**
until explicit review. Added columns: `metadata_fields` (ship date/terms/site),
`previous_snapshot`, `latest_snapshot`, `approved_snapshot`, `google_present`,
`is_active`, `synced_at`, `approved_at`, `source_revision`, and `revision`.

Saved WO model/table:

```python
__tablename__ = "WO Details"
```

Model:

```text
SalesOrder
```

Columns:

| Column | Description |
|---|---|
| `id` | UUID primary key identifying one WO |
| `sales_order` | SO reference; multiple WOs can share an SO |
| `release_number` | P1/P2/... number; unique within an SO |
| `is_manual` | Marks a manually entered blank WO (no Google Sheet quantity limits) |
| `customer` | Customer name |
| `customer_po` | Customer PO |
| `items` | JSON array containing item lines, serials, notes, and display order |
| `pushed_at` | Timestamp when this WO was last pushed to DB |
| `Generated Date` | First Push to DB timestamp (UTC); immutable on revision |
| `Document #` | Unique `WO-YYMM-NNNN` identity; immutable on revision |

New previews display an estimated Document Number using the same monthly
allocation function as the actual push. Preview does not reserve or save the number;
other pushes or a month change can change the final number. Revisions preview the
existing document number. The preview number is not included in Word copy areas.

Document numbers are allocated only on the first push, using the UTC month.
`SO-20261207` is seeded as `WO-2609-0127`; the next September 2026
record is `WO-2609-0128`. A new month starts at `0001`.
PostgreSQL allocations use a transaction lock and a unique constraint.
For existing records, the original creation date was not recorded; the migration
uses the existing `pushed_at` as the best available date (not a proven creation date).

Each item inside `items` contains data like:

```json
{
  "id": 1,
  "item": "Product Number",
  "quantity": 1,
  "serials": ["SN001", "SN002"],
  "notes": "Notes or deviation"
}
```

## Partial WOs

Select an SO to **create a new WO**. The editable quantities default to remaining
quantities after subtracting previously allocated WOs. Saved serials are never
copied into a new release. Example: SO quantity 10 → P1 quantity 5 → P2 quantity 5.

From a generated detail page:
- **Revise this WO** edits only that record; its Document # and Generated Date stay unchanged.
- **Create next WO** creates a separate release with a new Document # on push.

Preview is read-only. Push accepts the signed preview, rechecks allocations and
serial uniqueness, and saves one WO. If inputs change after preview, regenerate
before pushing. Retrying the same new-WO push does not create another release.
PostgreSQL pushes use a transaction lock to protect quantity and number allocation.

Quantity limits are aggregated by product across the SO; duplicate product lines
share that limit. Approved SO Qty is assumed to be the original ordered quantity,
not a shrinking unshipped balance. Allocation is not shipment confirmation.

New SO-based releases require approved, active `approved_work_orders` data, not Google
access. Existing WOs remain independently revisable; without active approved
limits, revisions cannot increase total allocated quantities. WO preview is
read-only, and only **Push to DB** saves the WO.

## Blank / manual WO

Click **New Blank WO** on the Open SO page (`/wo/new`); it is not in the top bar. Enter
Customer, Customer PO#, NTA Order ID, and any number of item rows using **Add item**
and **Remove**. Item, Qty, Serial Numbers, and Notes/Deviation are editable.

**Generate Table** previews these fields without writing the database; **Push to DB**
creates a WO with its own document number and generated date. Multiple manual WOs
can share an NTA Order ID. Saved manual WOs can be revised without Google Sheet access.

Manual quantities are operator-defined: there is no authoritative sheet quantity
against which to validate remaining allocation. Actual serial uniqueness and
nonnegative integer quantity validation still apply. `NA` is preserved and exempt
from duplicate checks. Changing a preview requires generating a new table before push.

```text
instance/wo_generator.db
```

## Run

Install dependencies:

```bash
pip install -r requirements.txt
```

Start the app:

```bash
python run.py
```

Open:

```text
http://localhost:5001
```

## Google Sheet read

The importer reads:

```text
Google Sheet file: PDF_WO
Worksheet/tab: Open Sales Order
```

The Google Sheet must be shared with the service-account email from the JSON credential.

The importer matches common column names including:

- `QB Num` / Sales Order / SO
- Customer
- Customer PO
- Item
- Qty / Quantity (finite, nonnegative whole numbers)
- Lead Time / Lead_Time / Ship Date
- Terms
- Inventory Site
- Configuration / Description

If the sheet uses different headers, update aliases in:

```text
app/importer.py
```

## Serial pruning

Serial parsing code is in:

```text
app/serial_pruner.py
```

Examples:

```text
Pcie-poe, SN0023783  →  23783
S/N: ZX-12           →  ZX-12
ABC-456              →  ABC-456
S75CNS0L518419        →  S75CNS0L518419
04842010792403       →  4842010792403
00123                →  123
000                  →  0
```

A bare leading `S` is part of the serial and is not stripped. Numeric-only
serials of **any length** lose all leading zeros (Excel-style); an all-zero
serial becomes `0`. Alphanumeric serials retain their leading zeros. Values
remain strings, with no floating-point conversion or loss of precision.
Normalization happens before deduplication and duplicate-SN validation.
Previously saved serials are not automatically changed; revise and push those
WOs again if correction is needed.

Input can be separated by Enter, comma, or semicolon.
Duplicates are removed and results are sorted.

## Daily full-database backup

The table-only Python backup and its wrapper were removed. The replacement is
native PowerShell + PostgreSQL client tools, with editable settings in
`scripts/database_backup.json` and complete setup instructions in
[`scripts/DATABASE_BACKUP.md`](scripts/DATABASE_BACKUP.md).

Default destination:

```text
D:\OneDrive - neousys-tech\Share NTA Warehouse\Database Backup\postgres_latest.dump
```

This includes **every schema/table and all database objects** in the configured
`postgres` database, not just WO Details. It does not include other databases or
server-wide roles/tablespaces. Default is latest-file **overwrite**; optionally
set `Mode` to `dated` for timestamped archives and `RetentionDays` retention.
Dated copies are recommended for recovery from accidentally deleted/bad data.

The script stages a custom-format `pg_dump`, reads/decompresses the full archive
with `pg_restore` without executing SQL, verifies SHA256 after copying, then
atomically replaces the finalized file. Failures before publication retain the
previous archive. A file lock and Task Scheduler settings prevent overlaps.
Logs are under `D:\DatabaseBackupStaging\logs`, with success/hash or failure details.

**Client tools verified:** PostgreSQL **18.6** at `D:\PostgreSQL\18\bin` can
back up server 17.2. `PgBinDirectory` now explicitly selects this installation,
not the older version 16 on C:. Configure the database password locally, then
register the task:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\setup_database_backup_credentials.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\install_database_backup_task.ps1
```

Credential setup stores a dedicated ACL-protected pgpass file **outside OneDrive**.
Task setup first requires a successful full backup, then prompts for the same
account's Windows sign-in password to schedule unattended daily runs. Default:
**23:00**, missed-run recovery, wake request, battery operation, and failure retries.
Rerun task setup after changing `DailyTime`, task identity/path, or Windows password.
The task is **not registered automatically** by creating these files.

OneDrive upload must be checked separately; local backup success is not cloud
sync confirmation. Keep the backup folder on this device, restrict access, and
regularly test restoration into a separate test database.

## Tests

Run:

```bash
python -m unittest discover -s tests -v
```

## Legacy migration

On startup, `app/schema.py` migrates the former SO-primary-key table to a WO UUID
primary key. Existing records become P1 and retain their items, document numbers,
and timestamps. A backup is written under `data/backups` before migration.

`migrate_single_table.py` is an older, pre-multi-WO migration tool; do not run it
against a database already upgraded to multiple WOs.

It backs up legacy data under:

```text
data/backups
```

and consolidates legacy records into the current table structure.
