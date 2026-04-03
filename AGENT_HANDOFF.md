# Agent handoff — NCE_Sync (`nce_sync`)

**For the next agent:** Read `nce_sync/CODE_INDEX.json` first (machine index). For Frappe API behaviour, use `Frappe Context/INDEX.md` at repo root and load only what you need.

## What this repo is

Frappe app **Tables** (`nce_sync`): mirror WordPress MySQL tables into Frappe DocTypes, sync data (WP → Frappe), optional live **SQL write-back** (Frappe → WP) on `default` queue, `frappe.flags.in_sync` during inbound sync to avoid feedback loops.

## Git / deploy note

`main` on GitHub may be ahead of what runs on a given bench; the maintainer sometimes pins the bench to an older SHA. Confirm target revision on the server before debugging “it works in repo but not live.”

## Write-back (recent history)

- **Do not** pass a kwarg named `method` into `frappe.enqueue(...)` — it collides with Frappe’s `enqueue` signature (`TypeError: multiple values for argument 'method'`). `run_write_back_for_doc` takes `(wp_table_name, doctype, docname)` only.
- Product direction included **simplifying** to SQL-only write-back (no child “steps” table in current `main`); if you see older docs mentioning handlers/steps, trust **code + CODE_INDEX** over stale notes.
- `write-back-deferred-dispatch-plan.md` is **gitignored** (local scratch only).

## Mirror schema — incomplete follow-ups

Mirror preview UI sends **`read_only_columns`** and **`pick_list_columns`** to `mirror_schema` / `remap_schema`, but **`mirror_table_schema()` is not yet given those args** — they have no effect on generated DocTypes.

Also, preview payload does not yet expose **`previous_read_only_columns` / `previous_pick_list_columns`**, so the JS cannot restore those checkboxes on reopen/remap.

**Implemented:** optional **`title_field_column`** → Frappe DocType `title_field`, `show_title_field_in_link`, `search_fields`; stored on WP Tables.

## Repo rules

See `.cursor/rules/` (e.g. confirm understanding, approval before code changes unless user clearly directs). Prefer root-cause fixes; match existing code style.

## Quick entrypoints

| Concern | Start here |
|--------|------------|
| Index | `nce_sync/CODE_INDEX.json` |
| Live write-back hook | `nce_sync/utils/live_sync.py` |
| Write-back job | `nce_sync/utils/write_back_dispatch.py` |
| WP→Frappe sync | `nce_sync/utils/data_sync.py` |
| Schema mirror / DocType build | `nce_sync/utils/schema_mirror.py` |
| WP Tables form + mirror dialog | `nce_sync/nce_sync/doctype/wp_tables/wp_tables.js` |

*Last updated: 2026-04-02 (session handoff).*
