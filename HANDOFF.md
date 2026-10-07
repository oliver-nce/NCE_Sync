<!--
Comprehensive NCE Sync handoff covering architecture, API connectors, sync methods, and roadmap.

code-index:begin
{
 "purpose": "Comprehensive NCE Sync handoff covering architecture, API connectors, sync methods, and roadmap.",
 "read_when": "When onboarding to the full sync system, connectors, scheduler, or development workflow.",
 "gotchas": [
  "Test Connection tests base_url with configured auth.",
  "Mirror Schema introspects WP table and creates DocType via preview dialog."
 ],
 "keywords": ["handoff", "nce sync", "api connector", "sync", "mirror schema", "scheduler", "roadmap", "frappe"],
 "format": 1
}
code-index:end
-->

# NCE Sync - Handoff Document

## Overview

NCE Sync is a Frappe app that mirrors WordPress database tables/views into Frappe DocTypes, syncs data between them, and provides an AI-powered API connector management system.

**Current Status:** Phase 1 complete, Phase 2 roadmapped

---

## Architecture

```
Analytics (Metabase)
│   Enrollments as root — denormalized with all related data
│
├── Frappe AI Layer (Anthropic Connector)
│   • AI Discover: chat-driven connector + endpoint creation
│   • Auto-generate Setup & Implementation Guides
│   • Data Analysis: profanity, retention, trends on any list/report (roadmap)
│   • Natural Language Queries across all DocTypes (roadmap)
│   • AI-driven Metabase chart/dashboard creation (roadmap)
│
├── Frappe Data Layer
│   ┌──────────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌──────────┐
│   │ Enrollments  │  │ Families │  │  People  │  │  Events  │  │ Venues │  │ Sessions │
│   └──────────────┘  └──────────┘  └──────────┘  └──────────┘  └────────┘  └──────────┘
│        ▲                              ▲              ▲              ▲           ▲
│        │                              │              │              │           │
├── Base Layer (WordPress)
│   ┌──────────────┐  ┌──────────┐  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌──────────┐
│   │ Order Lines  │  │  Users   │  │  Users   │  │ Products │  │ Venues │  │ Sessions │
│   │  Flattened   │  │ Flattened│  │ Flattened│  │ Flattened│  │        │  │          │
│   └──────────────┘  └──────────┘  └──────────┘  └──────────┘  └────────┘  └──────────┘
```

### ERD (verified from DB)

```
Families 1───∞ People 1───∞ Enrollments ∞───1 Events 1───∞ Event Sessions
                                              Events ∞───1 Venues
```

| Source (many) | Field | Target (one) |
|---|---|---|
| Enrollments | `player_id` | People |
| Enrollments | `product_id` | Events |
| Event Sessions | `product_id` | Events |
| Events | `venue_id` | Venues |
| People | `family_id` | Families |

---

## Key Files

| File | Purpose |
|------|---------|
| `nce_sync/__init__.py` | Version + monkey-patches (unsubscribe footer, Reply-To header) |
| `nce_sync/doctype/wordpress_connection/` | DB connection settings, test connection, discover tables |
| `nce_sync/doctype/wp_tables/` | Per-table sync config, mirror/sync actions |
| `nce_sync/doctype/api_connector/` | API service connections, credentials, AI Discover |
| `nce_sync/doctype/api_connector_endpoint/` | Child table: endpoints per connector |
| `nce_sync/utils/schema_mirror.py` | Introspects WP schema, creates Frappe DocTypes |
| `nce_sync/utils/data_sync.py` | Syncs data from WP to Frappe |
| `nce_sync/utils/workspace_utils.py` | Adds/removes DocTypes from NCE Sync workspace |
| `nce_sync/api.py` | Table Links grid/ERD, toggle auto-sync, apply link changes |
| `nce_sync/page/table_links/` | Table Links page with Define + Visualize (Mermaid ERD) tabs |
| `hooks.py` | Scheduler config (runs every 5 min for auto-sync) |

---

## API Connector System

### DocTypes

**API Connector** — stores connection info for external API services:
- Connection: name, service, status, base URL, auth type, timeout, retries
- Credentials: API key, secret, username/password, bearer token, OAuth refresh token
- Endpoints: child table of API Connector Endpoint
- Settings: rate limit, custom headers, notes (Setup Guide)
- Implementation Guide: service overview, usage patterns, docs links
- Test: last tested, result, error

**API Connector Endpoint** — child table per endpoint:
- endpoint_name, endpoint_key, http_method, content_type, path
- auth_override, documentation_url, description
- sample_submission, sample_response (JSON)
- implementation_guide (per-endpoint usage notes)

### Pre-configured Services

WordPress, WooCommerce, Google Sheets, Google Maps, Authorize.net, Stripe, SendGrid, Twilio, Anthropic, Klaviyo, Custom

Seed data CSVs in `Docs/` (gitignored):
- `api_connector_seed.csv` — parent records
- `api_connector_endpoint_seed.csv` — endpoint records
- `api_connector_notes.csv` — setup guide HTML per service
- `api_connector_with_endpoints_import.csv` — combined import

### AI Discover (Interactive Chat)

Button on API Connector list view: **"AI Discover [AI]"**

Flow:
1. Opens chat dialog — user describes use case (e.g., "AWS — I need to send emails")
2. AI asks clarifying questions, narrows to specific service
3. User clicks **"Generate Connector"** when ready
4. AI generates connector JSON (base URL, auth type, endpoints, guides)
5. Confirmation dialog shows details + endpoint table
6. **"Create Connector"** saves the doc with all endpoints

Server-side functions:
- `ai_discover_chat(messages)` — conversational turns
- `ai_discover_generate(messages)` — final JSON generation
- `create_connector_from_ai(connector_data)` — creates the doc

### AI Implementation Guide Generation

Button on connector form: **"Implementation Guide [AI]"**

- If guide exists → opens draggable popup to review
- If empty → calls Claude to generate guides for connector + all endpoints, saves, then opens popup

Server-side: `ai_generate_guide(connector_name)`

### Form Buttons

| Button | Condition | Action |
|--------|-----------|--------|
| Test Connection | Always (saved) | Tests base_url with configured auth |
| Setup Guide | Notes filled | Opens draggable popup with credential instructions |
| Implementation Guide [AI] | Always (saved) | Shows guide or generates via AI |
| Copy (per credential) | Always | Copies decrypted credential to clipboard |

---

## Sync System

### Sync Methods

#### 1. TS Compare (Timestamp Comparison)
- Incremental sync using `modified_timestamp_field` (falls back to `created_timestamp_field`)
- Cutoff calculation: `MAX(GREATEST(COALESCE(modified_ts, created_ts), created_ts))` handles NULL modified_ts
- WordPress query uses `>` (not `>=`) against the cutoff
- Detects deleted records by comparing matching keys

#### 2. Truncate & Replace
- Deletes all Frappe records, re-inserts everything from WP
- Use when data gets out of sync or for small tables

### Sync Robustness

- **Skip-and-continue**: Individual row errors are caught, logged, and skipped (up to 10 error messages stored). Sync continues for remaining rows.
- **Ignore Links**: `doc.flags.ignore_links = True` during sync to skip Link field validation (avoids halting on orphan references)
- **Notification suppression**: Manual/bulk syncs set `frappe.flags.in_import` to prevent notification floods. Cron syncs leave notifications enabled.
- **Rollback per row**: Failed rows trigger `frappe.db.rollback()`, successful batches commit with `frappe.db.commit()`

---

## Email Patches

In `nce_sync/__init__.py`:

1. **Unsubscribe footer**: Monkey-patches `frappe.email.queue.get_unsubscribe_message` to respect empty `unsubscribe_message`
2. **Reply-To header**: Monkey-patches `frappe.email.email_body.EMail.validate` to clear `reply_to` after validation, preventing wrong Reply-To addresses (fixed natively in Frappe v16 PR #36774)

---

## Table Links Page

Custom page at `/app/table-links`:

- **Define tab**: Cross-matrix grid of all mirrored DocTypes showing Link relationships. Click any cell to add/remove Link fields.
- **Visualize tab**: Mermaid ERD auto-generated from actual Link field metadata. Entity names sanitized for rendering.

---

## WP Tables Fields

| Field | Purpose |
|-------|---------|
| `table_name` | WordPress table/view name |
| `table_type` | "Table" or "View" |
| `nce_name` | Custom display name for the Frappe DocType |
| `modified_timestamp_field` | Column used for incremental sync |
| `created_timestamp_field` | Fallback if no modified timestamp |
| `matching_fields` | Comma-separated unique key fields (max 3) |
| `sync_direction` | "WP to Frappe" / "Frappe to WP" / "Both" |
| `sync_method` | "TS Compare" / "Truncate & Replace" |
| `sync_frequency` | Minutes between auto-syncs |
| `auto_sync_active` | "Yes" / "No" - enables scheduled sync |
| `column_mapping` | JSON: WP column name → Frappe fieldname |
| `last_synced` | Timestamp of last successful sync |
| `last_sync_status` | "Success" / "Error" / "Running" |
| `last_sync_log` | Summary or error message |
| `frappe_doctype` | Name of generated Frappe DocType |
| `mirror_status` | "Pending" / "Mirrored" / "Error" |

---

## Actions (Buttons)

| Button | When Visible | What It Does |
|--------|--------------|--------------|
| Mirror Schema | Always | Introspects WP table, shows field preview dialog, creates DocType |
| Sync Now | When mirrored | Pulls data from WP into Frappe |
| Truncate Data | When mirrored | Deletes all Frappe records (keeps DocType) |
| Regen Column Map | When mirrored | Rebuilds WP→Frappe column mapping |
| Debug Sync | When mirrored | Shows field matching diagnostics |
| Re-mirror | When mirrored | Deletes DocType, resets to Pending |
| Remove Table | When mirrored | Deletes everything (DocType + WP Tables record) |

---

## Scheduler

```python
scheduler_events = {
    "cron": {
        "*/5 * * * *": [
            "nce_sync.utils.data_sync.run_scheduled_syncs"
        ]
    }
}
```

Every 5 minutes, checks all WP Tables where:
- `auto_sync_active == "Yes"`
- `mirror_status == "Mirrored"`
- `now - last_synced >= sync_frequency`

---

## Roadmap

### Phase 2 — Functional Connectors
- [ ] Separate connector rows per functional area (e.g., "Stripe – Payments", "Stripe – Customers")
- [ ] Per-area Setup + Implementation Guides with specific doc links
- [ ] AI Discover chat produces focused, use-case-specific connectors

### Phase 2.5 — AI Data Analysis & Business Intelligence
- [ ] Generic "AI Analyze" button on list views / reports
- [ ] Profanity / fake-entry detection on contact/member data
- [ ] Retention rate, drop-off, trend analysis on registration data
- [ ] Natural language query engine across all DocTypes
- [ ] Cross-table business logic (ratings, enrollment timing, capacity, selection criteria)
- [ ] Actionable output — targeted lists, incentive suggestions, trigger emails/records
- [ ] Metabase integration — AI builds charts, pivots, dashboards from plain English
- [ ] Embed Metabase visualizations directly in Frappe

### Phase 3 — AI-Powered App Documentation
- [ ] "User Guide" DocType — AI-generated per-form/function guides
- [ ] AI button on every form, list, and report
- [ ] AI traverses app structure to auto-build documentation
- [ ] Help Panel — slide-out sidebar with context-aware guides
- [ ] Auto-regenerate guides when DocType definitions change

---

## Development Workflow

### After Code Changes

| Change Type | Action Required |
|-------------|-----------------|
| Python (.py) | Refresh browser |
| JavaScript (.js) | `bench clear-cache` + refresh |
| DocType JSON | `bench migrate` + `bench clear-cache` + refresh |
| hooks.py | Restart bench |

### Common Issues

**"Address already in use" (Redis ports)**
```bash
killall redis-server
bench start
```

**Python changes not loading** — Restart bench or `bench clear-cache`

**New DocType fields not appearing** — `bench --site <site> migrate`

**Stale DocType after delete/recreate** — `bench --site <site> clear-cache`

---

## Bench Commands Reference

```bash
# Apply DocType changes
bench --site <site> migrate

# Clear all caches
bench --site <site> clear-cache

# Access MariaDB directly
bench --site <site> mariadb

# Check logs
tail -f logs/frappe.log
```

---

*Last updated: March 7, 2026*
