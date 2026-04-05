# PathFinder Extraction Plan — Standalone Frappe App

**Date:** 2026-04-04
**Goal:** Extract the PathFinder tool from NCE Studio into a standalone Frappe v15 app called `pathfinder`, installable via `bench get-app` / `bench install-app`, usable by any app on the bench.

**Key use case:** Let users create *virtual fields* on any DocType by pointing to a related field via link chains (e.g. `Sales Order > Customer > Territory > name`), usable directly from Desk form views.

---

## 1. What We're Extracting

### Source Files (from NCE Studio)

All paths relative to `NCE Studio/studio/frontend/src/`

| File | Role | NCE Coupling |
|------|------|-------------|
| `nce/components/PathFinder/PathFinderCore.vue` | Multi-column field navigator | Clean — only uses `frappe-ui`, `PathColumn` |
| `nce/components/PathFinder/PathColumn.vue` | Single column of fields with drill-down | Clean — uses `frappe-ui`, `useFieldMeta` |
| `nce/components/PathFinder/PathFinderDialog.vue` | Dialog wrapper with field + action tabs | Calls `studio.api.get_whitelisted_methods` (doesn't exist) |
| `nce/components/PathFinder/PathFinderPanel.vue` | Panel with DocType selector + binding insert | **Tightly coupled** to `canvasStore`, `studioStore` |
| `nce/components/PathFinder/PathFinderPanelDesigner.vue` | Thin wrapper around Core | Clean |
| `nce/components/PathFinder/index.ts` | Barrel export | Clean |
| `nce/composables/useFieldMeta.ts` | Cached field metadata fetcher | Calls `studio.api.get_doctype_fields` |
| `nce/utils/formBinding.ts` | Path validation, binding helpers | Uses `FormDefinition` type from NCE |
| `nce/types/index.ts` | Type definitions | Only `PathSegment`, `FieldPath`, `FieldMeta` needed |

### Backend (from `NCE Studio/studio/studio/api/__init__.py`)

| Function | Lines | Role |
|----------|-------|------|
| `get_doctype_fields(doctype)` | 83–108 | Returns field metadata for a DocType (excludes layout fields) |
| `get_whitelisted_methods` | *Missing* | Referenced by PathFinderDialog but never implemented |

---

## 2. Dependencies to Sever

### Hard couplings that MUST be removed:

1. **`PathFinderPanel.vue`** imports `@/stores/canvasStore` and `@/stores/studioStore` — these are NCE Studio internals (canvas blocks, selected blocks, `setProp()`). The entire "Insert Binding" / "Insert as Expression" section is Studio-specific.

2. **`useFieldMeta.ts`** calls `studio.api.get_doctype_fields` — must be repointed to `pathfinder.api.get_doctype_fields`.

3. **`PathFinderDialog.vue`** calls `studio.api.get_whitelisted_methods` — must be repointed and the endpoint actually implemented.

4. **`formBinding.ts`** depends on `FormDefinition` type — this type is NCE Studio's form schema concept. The generic path-validation functions (`validateFieldPath`, `getTerminalFieldName`, `getParentPath`, `isDirectField`, `formatPathForDisplay`) are portable. The functions that mutate `FormDefinition` (`findPathMatches`, `addFieldBinding`, `isPathMapped`) are Studio-specific and should NOT be extracted.

5. **`@nce/types`** and **`@nce/composables/`** path aliases — must be replaced with the new app's own aliases.

### Safe dependencies (keep as-is):

- `frappe-ui` (`FeatherIcon`, `Autocomplete`, `Button`, `call`) — standard Frappe frontend library
- `vue` / `vue-sonner` — standard

---

## 3. New App Architecture

### 3.1 App Scaffold

```
pathfinder/
  pathfinder/
    __init__.py
    hooks.py
    install.py
    api/
      __init__.py              # whitelist hub
      pathfinder_api.py        # get_doctype_fields, get_whitelisted_methods,
                               #   get_virtual_field_value, resolve_virtual_fields
    pathfinder/                # "pathfinder" module (Frappe module)
      doctype/
        pathfinder_virtual_field/
          pathfinder_virtual_field.json   # DocType definition
          pathfinder_virtual_field.py     # Controller
          test_pathfinder_virtual_field.py
    public/
      js/
        pathfinder.bundle.js   # Built frontend bundle
    www/                       # (empty, no web pages needed)
    templates/                 # (empty)
    frontend/                  # Vue source (built by frappe)
      src/
        components/
          PathFinderCore.vue
          PathColumn.vue
          PathFinderDialog.vue
          PathFinderPanel.vue          # Refactored: no canvas coupling
          PathFinderPanelDesigner.vue
          index.ts
        composables/
          useFieldMeta.ts
        utils/
          pathUtils.ts                 # Portable functions from formBinding.ts
        types/
          index.ts                     # PathSegment, FieldPath, FieldMeta only
        desk/
          VirtualFieldManager.vue      # New: Desk-integrated UI for managing virtual fields
          pathfinder_control.js        # New: Custom Frappe control for Desk forms
      package.json
      vite.config.ts
  setup.py
  pyproject.toml
  MANIFEST.in
  requirements.txt
  license.txt
  README.md
```

### 3.2 New DocType: `Pathfinder Virtual Field`

This is the core data model that makes PathFinder useful outside NCE Studio.

```
Pathfinder Virtual Field
  Fields:
    - source_doctype    (Link → DocType)     — the DocType this virtual field belongs to
    - field_label       (Data)               — display label
    - field_path        (Small Text)         — dot-notation path, e.g. "customer.territory.name"
    - terminal_fieldtype (Data, read-only)   — auto-resolved fieldtype of the terminal field
    - terminal_options  (Data, read-only)    — auto-resolved options (for Link fields)
    - show_in_form      (Check)             — whether to render on the Desk form
    - show_in_list      (Check)             — whether to include in list view
    - column_order      (Int)               — sort order when displayed
    - description       (Small Text)        — optional help text
    - enabled           (Check, default 1)  — soft disable

  Naming: {source_doctype}-{field_label} (slug)

  Permissions:
    - System Manager: full CRUD
    - All: read (so the client can fetch definitions)
```

### 3.3 Backend API Endpoints

All endpoints go in `pathfinder/api/pathfinder_api.py` and are re-exported from `pathfinder/api/__init__.py`.

#### Endpoint 1: `pathfinder.api.get_doctype_fields`
Copied directly from NCE Studio's `get_doctype_fields`. Returns field metadata for a DocType, excluding layout break fields. Used by the PathColumn component.

#### Endpoint 2: `pathfinder.api.get_whitelisted_methods`
New implementation. Returns the list of whitelisted methods for a DocType's controller class. Used by PathFinderDialog's "Button Action" tab.

```python
@frappe.whitelist()
def get_whitelisted_methods(doctype: str) -> list[str]:
    frappe.has_permission(doctype, throw=True)
    controller = frappe.get_meta(doctype).get_controller()
    methods = []
    for attr_name in dir(controller):
        attr = getattr(controller, attr_name, None)
        if callable(attr) and getattr(attr, "is_whitelisted", False):
            methods.append(attr_name)
    return sorted(methods)
```

#### Endpoint 3: `pathfinder.api.get_virtual_fields`
Returns all enabled virtual field definitions for a given DocType.

```python
@frappe.whitelist()
def get_virtual_fields(doctype: str) -> list[dict]:
    frappe.has_permission(doctype, throw=True)
    return frappe.get_all(
        "Pathfinder Virtual Field",
        filters={"source_doctype": doctype, "enabled": 1},
        fields=["name", "field_label", "field_path", "terminal_fieldtype",
                "terminal_options", "show_in_form", "show_in_list", "column_order"],
        order_by="column_order asc"
    )
```

#### Endpoint 4: `pathfinder.api.resolve_virtual_fields`
Resolves actual values for a document's virtual fields by traversing link chains.

```python
@frappe.whitelist()
def resolve_virtual_fields(doctype: str, docname: str) -> dict:
    """
    Returns {field_label: resolved_value} for all enabled virtual fields.
    Traverses dot-notation paths through linked documents.
    """
    frappe.has_permission(doctype, "read", docname, throw=True)
    vfields = get_virtual_fields(doctype)
    result = {}
    doc = frappe.get_doc(doctype, docname)

    for vf in vfields:
        result[vf["field_label"]] = _resolve_path(doc, vf["field_path"])

    return result


def _resolve_path(doc, path: str):
    """Walk a dot-notation path through linked documents."""
    segments = path.split(".")
    current = doc

    for i, segment in enumerate(segments):
        if current is None:
            return None

        if i < len(segments) - 1:
            # Intermediate segment: must be a Link field — fetch the linked doc
            link_value = current.get(segment)
            if not link_value:
                return None
            meta = frappe.get_meta(current.doctype)
            field = meta.get_field(segment)
            if not field or field.fieldtype != "Link" or not field.options:
                return None
            current = frappe.get_doc(field.options, link_value)
        else:
            # Terminal segment: return the value
            return current.get(segment)

    return None
```

### 3.4 Frontend Changes

#### PathFinderPanel.vue — Refactored

Remove all NCE Studio canvas coupling. Replace with an **event-driven API** so any consuming app can listen:

```
Props:
  - rootDoctype?: string        (optional pre-selected DocType)
  - mode?: "select" | "manage"  ("select" = pick a path, "manage" = CRUD virtual fields)

Emits:
  - path-selected(path: string)
  - virtual-field-created(definition: object)

Remove:
  - All canvasStore / studioStore imports
  - insertBinding() function
  - insertAsExpression() function
  - canInsertBinding computed
  - The entire "Insert Binding" footer section

Replace with:
  - A simpler footer that emits the selected path
  - In "manage" mode: a "Create Virtual Field" button that calls the API
```

#### PathFinderDialog.vue — Repoint API

Change `studio.api.get_whitelisted_methods` to `pathfinder.api.get_whitelisted_methods`.

#### useFieldMeta.ts — Repoint API

Change `studio.api.get_doctype_fields` to `pathfinder.api.get_doctype_fields`.

#### types/index.ts — Strip to essentials

Keep ONLY:
- `FieldMeta`
- `PathSegment`
- `FieldPath`

Remove everything else (EditLock, FormDefinition, ThemeSettings, etc.).

#### utils/pathUtils.ts — Portable subset of formBinding.ts

Extract ONLY these functions (they have zero NCE dependencies):
- `validateFieldPath`
- `getTerminalFieldName`
- `getParentPath`
- `isDirectField`
- `formatPathForDisplay`
- `createFieldFromPath`
- `bindPathFinderToField`

Do NOT extract: `findPathMatches`, `addFieldBinding`, `isPathMapped` (these depend on `FormDefinition`).

#### NEW: desk/pathfinder_control.js — Custom Frappe Control for Desk

This is the key integration piece. It registers a custom control that can be added to any DocType form via Custom Field or client script. When rendered, it shows the resolved virtual field values.

```javascript
// Register as frappe.ui.form.ControlPathfinderVirtual
// Renders a read-only display of the resolved virtual field value
// On form refresh: calls pathfinder.api.resolve_virtual_fields
// Injects resolved values into a section on the form
```

#### NEW: desk/VirtualFieldManager.vue — Management UI

A standalone page (or dialog) where users can:
1. Select a DocType
2. Use PathFinderCore to navigate and select a field path
3. Save it as a Pathfinder Virtual Field record
4. See all existing virtual fields for that DocType
5. Enable/disable/reorder them

This replaces the NCE Studio-specific "Insert Binding" workflow with a generic Desk-compatible one.

### 3.5 Hooks Integration

In `hooks.py`:

```python
app_name = "pathfinder"
app_title = "Pathfinder"
app_publisher = "NCE"
app_description = "Visual field-path navigator for Frappe — create virtual fields by traversing DocType link chains"

# Include JS bundle on Desk pages
app_include_js = "/assets/pathfinder/js/pathfinder.bundle.js"

# Doc events: auto-resolve virtual fields when documents load
doc_events = {
    "*": {
        "onload": "pathfinder.api.pathfinder_api.inject_virtual_fields"
    }
}
```

The `inject_virtual_fields` hook fetches virtual field definitions for the document's DocType and attaches them as transient attributes so the client-side control can display them.

---

## 4. Migration Path for NCE Studio

After `pathfinder` is extracted and installed:

1. NCE Studio adds `pathfinder` as a dependency in its `pyproject.toml`
2. Replace all `nce/components/PathFinder/*` imports with `import { PathFinderCore, PathColumn, ... } from 'pathfinder'`
3. Keep `PathFinderPanel.vue` in NCE Studio as a **Studio-specific wrapper** that imports `PathFinderCore` from pathfinder and adds back the canvas binding logic
4. Remove `get_doctype_fields` from `studio/api/__init__.py`
5. Update `useFieldMeta.ts` to import from pathfinder's composables

---

## 5. Build and Install

### Scaffold the app:

```bash
cd ~/frappe-bench
bench new-app pathfinder
# Answer prompts: title="Pathfinder", publisher="NCE", etc.
```

### Install on site:

```bash
bench --site <sitename> install-app pathfinder
```

### Frontend build:

The app uses Frappe's standard Vue build pipeline. The `frontend/` folder is built via `bench build --app pathfinder`.

---

## 6. File-by-File Handoff

```
[TASK] Extract PathFinder into standalone Frappe app

Phase 1 — Backend scaffold
Files:
  - pathfinder/hooks.py                          (NEW)
  - pathfinder/install.py                        (NEW)
  - pathfinder/api/__init__.py                   (NEW)
  - pathfinder/api/pathfinder_api.py             (NEW)
  - pathfinder/pathfinder/doctype/pathfinder_virtual_field/  (NEW DocType)
Changes:
  1. Run bench new-app pathfinder
  2. Create the Pathfinder Virtual Field DocType with fields listed in §3.2
  3. Implement 4 API endpoints in pathfinder_api.py (§3.3)
  4. Re-export all endpoints from api/__init__.py
  5. Configure hooks.py per §3.5
  6. Write unit tests for _resolve_path (happy path, broken link, missing field, circular)
Frappe notes:
  - All whitelisted endpoints must include frappe.has_permission() checks
  - _resolve_path must handle Table fields differently (they return child lists, not single docs)
  - Use frappe.get_meta() for field introspection — never query DocField table directly
  - The doc_events "*" hook fires for ALL doctypes — keep inject_virtual_fields lightweight
    (skip if no virtual fields exist for that doctype)

Phase 2 — Frontend extraction
Files:
  - pathfinder/frontend/src/components/PathFinderCore.vue      (COPY from NCE Studio)
  - pathfinder/frontend/src/components/PathColumn.vue          (COPY from NCE Studio)
  - pathfinder/frontend/src/components/PathFinderDialog.vue    (COPY + MODIFY)
  - pathfinder/frontend/src/components/PathFinderPanel.vue     (REWRITE — remove canvas coupling)
  - pathfinder/frontend/src/components/PathFinderPanelDesigner.vue  (COPY)
  - pathfinder/frontend/src/components/index.ts                (COPY)
  - pathfinder/frontend/src/composables/useFieldMeta.ts        (COPY + MODIFY)
  - pathfinder/frontend/src/utils/pathUtils.ts                 (EXTRACT subset from formBinding.ts)
  - pathfinder/frontend/src/types/index.ts                     (EXTRACT subset)
Changes:
  1. Copy PathFinderCore.vue — change import `@nce/types` → `../types`
  2. Copy PathColumn.vue — change `@nce/composables/useFieldMeta` → `../composables/useFieldMeta`
  3. Copy PathFinderDialog.vue — change API call from `studio.api.get_whitelisted_methods`
     to `pathfinder.api.get_whitelisted_methods`
  4. Rewrite PathFinderPanel.vue per §3.4 — remove canvasStore/studioStore entirely,
     replace with event emitter pattern
  5. Copy useFieldMeta.ts — change API call from `studio.api.get_doctype_fields`
     to `pathfinder.api.get_doctype_fields`
  6. Create pathUtils.ts with 7 portable functions from formBinding.ts (§3.4)
  7. Create types/index.ts with FieldMeta, PathSegment, FieldPath only
  8. Update all import path aliases to use relative imports (no @nce/ prefix)

Phase 3 — Desk integration
Files:
  - pathfinder/frontend/src/desk/VirtualFieldManager.vue       (NEW)
  - pathfinder/frontend/src/desk/pathfinder_control.js         (NEW)
  - pathfinder/public/js/pathfinder.bundle.js                  (BUILT)
Changes:
  1. Create VirtualFieldManager.vue — management page per §3.4
  2. Create pathfinder_control.js — custom Frappe control that:
     a. On form refresh, calls pathfinder.api.resolve_virtual_fields
     b. Renders resolved values in a "Virtual Fields" section on the form
     c. Provides a "Manage Virtual Fields" button that opens VirtualFieldManager
  3. Bundle into pathfinder.bundle.js via Frappe's build system
  4. Register the page route in hooks.py website_route_rules

Phase 4 — Tests
Files:
  - pathfinder/api/test_pathfinder_api.py                      (NEW)
  - pathfinder/pathfinder/doctype/.../test_pathfinder_virtual_field.py (NEW)
  - pathfinder/frontend/src/tests/pathUtils.test.ts            (NEW)
Changes:
  1. Backend: test all 4 API endpoints + _resolve_path edge cases
  2. Backend: test DocType validation (duplicate field_label per doctype, invalid path format)
  3. Frontend: port relevant tests from formBinding.test.ts for the extracted functions

Frappe notes:
  - Virtual field resolution is READ-ONLY — never modify documents through virtual fields
  - Permission inheritance: if user can't read the linked DocType, _resolve_path must
    return None (not throw), and the UI should show "No access" gracefully
  - The doc_events "*" onload hook should be gated: check frappe.cache().hget() for
    whether the doctype has any virtual fields before doing any DB queries
  - Table field traversal: the current _resolve_path only handles Link fields;
    Table fields need special handling (return list of child values or first match)
  - The frontend bundle must be included via app_include_js so it's available on
    all Desk pages, not just within the app's own SPA
```

---

## 7. Risks and Open Questions

1. **Table field traversal** — The current PathFinder allows drilling into Table fields, but `_resolve_path` only handles Link traversal. Decision needed: should virtual fields support paths through Table/child tables? If yes, what value is returned (first child? all children? aggregation)?

2. **Performance at scale** — `doc_events["*"]["onload"]` fires on every document load. Must be zero-cost when no virtual fields exist for that doctype. Use a cached set of doctypes-with-virtual-fields.

3. **Dynamic Link fields** — PathFinderCore marks Dynamic Link as drillable, but the target doctype isn't known at definition time. Should we support this or exclude it from virtual field creation?

4. **Custom app dependency** — NCE Studio will depend on pathfinder. Need to ensure the migration doesn't break existing NCE Studio installations. Suggest: keep PathFinder files in NCE Studio temporarily with deprecation warnings, remove in next major version.
