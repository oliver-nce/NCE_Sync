// Copyright (c) 2026, Oliver Reid and contributors
// For license information, please see license.txt
//
// code-index:begin
// {
//  "purpose": "Placeholder desk script for NCE Access Profile Table child rows loaded via parent form.",
//  "read_when": "When adding client-side behavior for table access child rows on the access profile form.",
//  "gotchas": [
//   "Child DocType with no page route so Frappe never auto-loads this script."
//  ],
//  "keywords": ["access", "nce", "profile", "table", "license", "child table"],
//  "format": 1
// }
// code-index:end

// Intentionally empty. NCE Access Profile Table is a child (istable=1)
// doctype with no page/route of its own, so Frappe never auto-loads this
// file. The row-button handler (manage_fields) and its dialog live in the
// parent's script instead: nce_access_profile/nce_access_profile.js.
