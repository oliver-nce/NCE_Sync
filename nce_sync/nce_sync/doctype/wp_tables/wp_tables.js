// Copyright (c) 2026, Oliver Reid and contributors
// For license information, please see license.txt

// Available Frappe field types for the dropdown
const FRAPPE_FIELD_TYPES = [
	"Data",
	"Small Text",
	"Text",
	"Long Text",
	"Int",
	"Float",
	"Check",
	"Date",
	"Datetime",
	"Time",
	"Select",
	"JSON",
	"Password",
	"Currency",
	"Percent",
	"Rating",
];

const RESERVED_FIELDNAMES = [
	"name",
	"parent",
	"creation",
	"owner",
	"modified",
	"modified_by",
	"parentfield",
	"parenttype",
	"file_list",
	"flags",
	"docstatus",
];

function _scrub_fieldname(label) {
	return (label || "")
		.toLowerCase()
		.replace(/\s+/g, "_")
		.replace(/[^a-z0-9_]/g, "");
}

frappe.ui.form.on("WP Tables", {
	after_save: function (frm) {
		let desired = (
			frm.doc.nce_name ||
			frm.doc.table_name ||
			frm.doc.frappe_doctype ||
			""
		).trim();
		if (desired && frm.doc.name !== desired) {
			frappe
				.xcall("frappe.client.rename_doc", {
					doctype: "WP Tables",
					old_name: frm.doc.name,
					new_name: desired,
				})
				.then(() => {
					frappe.set_route("Form", "WP Tables", desired);
				})
				.catch((e) => {
					frappe.msgprint({
						title: __("Rename Failed"),
						message: e.message || __("Could not rename to {0}", [desired]),
						indicator: "red",
					});
				});
		}
	},

	refresh: function (frm) {
		if (frm.is_new()) return;

		// Set form title badge based on sync + mirror state
		if (frm.doc.mirror_status === "Pending") {
			frm.page.set_indicator(__("Pending"), "orange");
		} else if (frm.doc.last_sync_status === "Running") {
			frm.page.set_indicator(__("Syncing"), "blue");
		} else if (frm.doc.last_sync_status === "Warning") {
			frm.page.set_indicator(__("Sync warning"), "orange");
		} else if (frm.doc.last_sync_status === "Error") {
			frm.page.set_indicator(__("Sync Error"), "red");
		} else if (frm.doc.last_sync_status === "Success") {
			frm.page.set_indicator(__("Synced"), "green");
		} else if (frm.doc.mirror_status === "Mirrored") {
			frm.page.set_indicator(__("Mirrored"), "blue");
		} else if (frm.doc.mirror_status === "Linked") {
			frm.page.set_indicator(__("Linked"), "purple");
		}

		let is_mirrored =
			(frm.doc.mirror_status === "Mirrored" || frm.doc.mirror_status === "Linked") &&
			frm.doc.frappe_doctype;

		// Mirror Schema — only when not yet mirrored (and not Native mode)
		if (!is_mirrored && frm.doc.doctype_source !== "Native") {
			frm.add_custom_button(
				__("Mirror Schema"),
				function () {
					frappe.call({
						method: "preview_schema",
						doc: frm.doc,
						freeze: true,
						freeze_message: __("Introspecting table schema..."),
						callback: function (r) {
							if (r.message) {
								show_preview_dialog(frm, r.message);
							}
						},
					});
				},
				__("Actions"),
			);
		}

		// Native mode: Link / Unlink buttons
		if (frm.doc.doctype_source === "Native") {
			if (!is_mirrored) {
				frm.add_custom_button(
					__("Link DocType"),
					function () {
						if (!frm.doc.frappe_doctype) {
							frappe.msgprint(__("Please select a Frappe DocType first."));
							return;
						}
						frm.save().then(function () {
							frappe.call({
								method: "link_external_doctype",
								doc: frm.doc,
								freeze: true,
								freeze_message: __("Linking..."),
								callback: function () {
									frm.reload_doc();
								},
							});
						});
					},
					__("Actions"),
				);
			} else {
				frm.add_custom_button(
					__("Unlink"),
					function () {
						frappe.confirm(
							__(
								"This will unlink the Native DocType from this entry. The DocType itself will NOT be deleted. Continue?",
							),
							function () {
								frappe.call({
									method: "unlink_external_doctype",
									doc: frm.doc,
									freeze: true,
									freeze_message: __("Unlinking..."),
									callback: function () {
										frm.reload_doc();
									},
								});
							},
						);
					},
					__("Actions"),
				);
			}
		}

		if (is_mirrored) {
			// Sync Now
			frm.add_custom_button(
				__("Sync Now"),
				function () {
					frappe.call({
						method: "sync_now",
						doc: frm.doc,
						callback: function () {
							show_sync_progress_dialog(frm);
						},
						error: function () {
							frm.reload_doc();
						},
					});
				},
				__("Actions"),
			);

			// Test Sync — run sync_method against first N rows (1–2999)
			frm.add_custom_button(
				__("Test Sync"),
				function () {
					frappe.prompt(
						[
							{
								fieldtype: "Int",
								fieldname: "row_limit",
								label: __("Rows to sync (1–2999)"),
								default: 500,
								reqd: 1,
								description: __(
									"Test Sync runs the configured Sync Method against the first N rows. " +
									"Must be under 3000 so the run fits in one batch. " +
									"last_synced is NOT updated, so the next real sync still sees these rows.",
								),
							},
						],
						function (values) {
							const n = parseInt(values.row_limit, 10);
							if (!Number.isFinite(n) || n < 1 || n > 2999) {
								frappe.msgprint({
									title: __("Invalid row count"),
									message: __("Choose a value between 1 and 2999."),
									indicator: "red",
								});
								return;
							}
							frappe.call({
								method: "test_sync",
								doc: frm.doc,
								args: { row_limit: n },
								callback: function () {
									show_sync_progress_dialog(frm);
								},
								error: function () {
									frm.reload_doc();
								},
							});
						},
						__("Test Sync"),
						__("Run"),
					);
				},
				__("Actions"),
			);

			// Preview Sync Counts
			if ((frm.doc.sync_direction || "WP to Frappe") === "WP to Frappe") {
				frm.add_custom_button(
					__("Preview Sync Counts"),
					function () {
						frappe.call({
							method: "preview_sync_counts",
							doc: frm.doc,
							freeze: true,
							freeze_message: __("Calculating sync preview..."),
							callback: function (r) {
								if (r.message) {
									show_preview_sync_counts_dialog(frm, r.message);
								}
							},
						});
					},
					__("Actions"),
				);
			}

			// Truncate Data — clears all records, keeps DocType structure
			frm.add_custom_button(
				__("Truncate Data"),
				function () {
					frappe.confirm(
						__(
							"This will delete ALL records from '{0}' in Frappe. The DocType structure will remain. Continue?",
							[frm.doc.frappe_doctype],
						),
						function () {
							frappe.call({
								method: "truncate_data",
								doc: frm.doc,
								freeze: true,
								freeze_message: __("Deleting all records..."),
								callback: function () {
									frm.reload_doc();
									frappe.show_alert({
										message: __("All records deleted"),
										indicator: "green",
									});
								},
							});
						},
					);
				},
				__("Actions"),
			);

			// Remap — re-read source schema, add new columns, rebuild mapping, truncate + repopulate
			frm.add_custom_button(
				__("Remap"),
				function () {
					let d = new frappe.ui.Dialog({
						title: __("Remap — {0}", [frm.doc.frappe_doctype]),
						fields: [
							{
								fieldtype: "HTML",
								fieldname: "info",
								options: `<p class="text-muted">${__(
									"This will truncate all data in '{0}', re-read the source schema (adding any new columns), and rebuild the column mapping. The DocType and its table are preserved.",
									[frm.doc.frappe_doctype],
								)}</p>`,
							},
							{
								fieldtype: "Data",
								fieldname: "new_table_name",
								label: __("Source Table Name"),
								default: frm.doc.table_name,
								description: __(
									"Change this if the WordPress table has been renamed.",
								),
							},
						],
						primary_action_label: __("Continue"),
						primary_action: function (values) {
							d.hide();
							let new_name = (values.new_table_name || "").trim();
							let table_name_override =
								new_name && new_name !== frm.doc.table_name ? new_name : undefined;

							frappe.call({
								method: "preview_schema",
								doc: frm.doc,
								args: { table_name_override: table_name_override },
								freeze: true,
								freeze_message: __("Introspecting table schema..."),
								callback: function (r) {
									if (r.message) {
										show_preview_dialog(frm, r.message, "remap", new_name);
									}
								},
							});
						},
					});
					d.show();
				},
				__("Actions"),
			);

			// Reconfigure — full teardown (Mirror mode only)
			if (frm.doc.doctype_source !== "Native") {
				frm.add_custom_button(
					__("Reconfigure"),
					function () {
						frappe.confirm(
							__(
								"This will delete the DocType '{0}', its data, and remove it from the workspace. You can then Mirror Schema again with different settings. Continue?",
								[frm.doc.frappe_doctype],
							),
							function () {
								frappe.call({
									method: "delete_mirror",
									doc: frm.doc,
									freeze: true,
									freeze_message: __("Reconfiguring..."),
									callback: function () {
										frappe.ui.toolbar.clear_cache();
										frm.reload_doc();
									},
								});
							},
						);
					},
					__("Actions"),
				);
			}

			// "Add to Workspace" shown outside Actions menu when shortcut is missing
			frappe
				.xcall("nce_sync.utils.workspace_utils.is_in_workspace", {
					doctype_name: frm.doc.frappe_doctype,
				})
				.then((in_ws) => {
					if (!in_ws) {
						let btn = frm.add_custom_button(__("Add to Workspace"), function () {
							frappe.call({
								method: "add_to_workspace",
								doc: frm.doc,
								callback: function () {
									frappe.ui.toolbar.clear_cache();
									frm.reload_doc();
								},
							});
						});
						btn.css({
							"background-color": "pink",
							color: "red",
							"font-weight": "bold",
						});
					}
				});
		}
	},
});

function show_preview_dialog(frm, preview_data, mode, new_table_name) {
	mode = mode || "mirror";
	let fields = preview_data.fields;
	let doctype_name = preview_data.doctype_name;
	let previous_matching = preview_data.previous_matching_fields || [];
	let previous_name_column = preview_data.previous_name_field_column || null;

	let dialog_title =
		mode === "remap"
			? __("Remap Schema — {0}", [doctype_name])
			: __("Review Field Types — {0}", [doctype_name]);
	let action_label = mode === "remap" ? __("Save & remap") : __("Confirm & Create");

	let dialog_config = {
		title: dialog_title,
		size: "extra-large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "field_preview",
			},
		],
		primary_action_label: action_label,
		primary_action: function () {
			// ─── Collect values from BOTH tabs (shared collectors) ───

			// Tab 2: Collect label overrides
			// For reserved columns (e.g. "name"), ALWAYS include the label even if
			// unchanged — the fieldname is derived from the label, so resolve_fieldname
			// needs it to avoid falling back to the "_field" suffix.
			let label_overrides = {};
			d.$wrapper.find(".field-label-input").each(function () {
				let col_name = $(this).data("column");
				let label = $(this).val().trim();
				let original = $(this).data("original");
				let is_reserved = RESERVED_FIELDNAMES.includes(col_name.toLowerCase());
				if (label && (label !== original || is_reserved)) {
					label_overrides[col_name] = label;
				}
			});

			// Tab 2: Collect Title field selection (optional)
			let title_field_column = d.$wrapper.find(".title-field-radio:checked").val() || "";

			// Tab 2: Collect Read Only columns
			let read_only_columns = [];
			d.$wrapper.find(".read-only-checkbox:checked").each(function () {
				read_only_columns.push($(this).data("column"));
			});

			// Tab 2: Collect Pick List columns
			let pick_list_columns = [];
			d.$wrapper.find(".pick-list-checkbox:checked").each(function () {
				pick_list_columns.push($(this).data("column"));
			});

			// Tab 2: Collect Bold columns
			let bold_columns = [];
			d.$wrapper.find(".bold-checkbox:checked").each(function () {
				bold_columns.push($(this).data("column"));
			});

			// Tab 2: Script default values (optional; validated server-side by field type)
			let column_defaults = {};
			d.$wrapper.find(".default-value-input").each(function () {
				let col_name = $(this).data("column");
				let v = $(this).val();
				column_defaults[col_name] = v == null ? "" : String(v);
			});

			// Validate reserved column labels (Tab 2)
			let reserved_errors = [];
			d.$wrapper.find(".field-label-input.reserved-source-col").each(function () {
				let col_name = $(this).data("column");
				let label = $(this).val().trim();
				let scrubbed = _scrub_fieldname(label);
				if (!label || RESERVED_FIELDNAMES.includes(scrubbed)) {
					reserved_errors.push(col_name);
					$(this).css({ "border-color": "#dc3545", "background-color": "#fff0f0" });
				}
			});
			if (reserved_errors.length > 0) {
				frappe.msgprint(
					__(
						"Reserved column(s) need a unique label: <strong>{0}</strong>.<br>Choose a label that doesn't resolve to a reserved name (e.g. 'Event Name' instead of 'Name').",
						[reserved_errors.join(", ")],
					),
				);
				return;
			}

			let field_overrides_light = {};
			d.$wrapper.find(".field-type-select").each(function () {
				let col_name = $(this).data("column");
				field_overrides_light[col_name] = $(this).val();
			});

			// ─── Determine which path to take based on dirty tabs ───
			let mapping_dirty = d.$wrapper.find("#tab-mapping").data("dirty");

			// ─── REMAP without Data Mapping edits: always hit server — reconciles DocType
			// fields against live WordPress schema (no truncate). ───
			if (mode === "remap" && !mapping_dirty) {
				d.get_primary_btn().prop("disabled", true).text(__("Saving…"));

				frappe.call({
					method: "update_field_settings",
					doc: frm.doc,
					args: {
						title_field_column: title_field_column || undefined,
						label_overrides: JSON.stringify(label_overrides),
						read_only_columns: read_only_columns.join(",") || undefined,
						pick_list_columns: pick_list_columns.join(",") || undefined,
						bold_columns: bold_columns.join(",") || undefined,
						column_defaults: JSON.stringify(column_defaults),
						field_overrides: JSON.stringify(field_overrides_light),
					},
					freeze: true,
					freeze_message: __("Applying field settings..."),
					callback: function (r) {
						d.hide();
						frm.reload_doc();
					},
					error: function (r) {
						d.get_primary_btn().prop("disabled", false).text(action_label);
					},
				});
				return;
			}

			// ─── FULL PATH: Tab 1 changed (with or without Tab 2) ───

			// Tab 1: Collect field type overrides
			let field_overrides = field_overrides_light;

			// Tab 1: Collect "Use as Name" selection
			let name_field_column = d.$wrapper.find(".name-field-radio:checked").val() || "";

			// Tab 1: Collect matching fields
			let matching_fields = [];
			d.$wrapper.find(".matching-field-checkbox:checked").each(function () {
				matching_fields.push($(this).data("column"));
			});

			// Tab 1: Collect auto-generated columns
			let auto_generated_columns = [];
			d.$wrapper.find(".auto-generated-checkbox:checked").each(function () {
				auto_generated_columns.push($(this).data("column"));
			});

			// Tab 1: Collect timestamp field selections
			let modified_ts_field = d.$wrapper.find(".mod-ts-radio:checked").val() || "";
			let created_ts_field = d.$wrapper.find(".crt-ts-radio:checked").val() || "";

			// Validate: Title cannot be the same column as Frappe ID
			if (title_field_column && title_field_column === name_field_column) {
				frappe.msgprint(
					__("Title field cannot be the same as Frappe ID — the ID column does not create a DocType field."),
				);
				return;
			}

			// Validate: max 3 matching fields (when not using Name)
			if (!name_field_column && matching_fields.length > 3) {
				frappe.msgprint(__("Please select a maximum of 3 matching fields."));
				return;
			}
			if (!name_field_column && matching_fields.length === 0) {
				frappe.msgprint(
					__("Please select at least one matching field, or use a column as Name."),
				);
				return;
			}

			// Validate: Modified TS is mandatory
			if (!modified_ts_field) {
				frappe.msgprint(__("Please select a Modified Timestamp field (Mod TS column)."));
				return;
			}

			// Disable button to prevent double-clicks while processing
			let busy_text = mode === "remap" ? __("Saving…") : __("Creating…");
			d.get_primary_btn().prop("disabled", true).text(busy_text);

			let call_method = mode === "remap" ? "remap_schema" : "mirror_schema";
			let freeze_msg =
				mode === "remap" ? __("Applying changes...") : __("Creating DocType...");

			let call_args = {
				field_overrides: JSON.stringify(field_overrides),
				label_overrides: JSON.stringify(label_overrides),
				matching_fields: matching_fields.join(","),
				name_field_column: name_field_column || undefined,
				title_field_column: title_field_column || undefined,
				auto_generated_columns: auto_generated_columns.join(",") || undefined,
				modified_ts_field: modified_ts_field || undefined,
				created_ts_field: created_ts_field || undefined,
				read_only_columns: read_only_columns.join(",") || undefined,
				pick_list_columns: pick_list_columns.join(",") || undefined,
				bold_columns: bold_columns.join(",") || undefined,
				column_defaults: JSON.stringify(column_defaults),
			};
			if (mode === "remap" && new_table_name && new_table_name !== frm.doc.table_name) {
				call_args.new_table_name = new_table_name;
			}

			frappe.call({
				method: call_method,
				doc: frm.doc,
				args: call_args,
				freeze: true,
				freeze_message: freeze_msg,
				callback: function (r) {
					d.hide();
					if (r.message && r.message.has_saved_layout) {
						frappe.confirm(
							__("Restore previous form customization?"),
							function () {
								frappe.call({
									method: "restore_saved_layout",
									doc: frm.doc,
									callback: function () {
										frappe.ui.toolbar.clear_cache();
										frm.reload_doc();
									},
								});
							},
							function () {
								frappe.ui.toolbar.clear_cache();
								frm.reload_doc();
							},
						);
					} else {
						frappe.ui.toolbar.clear_cache();
						frm.reload_doc();
					}
				},
				error: function (r) {
					d.get_primary_btn().prop("disabled", false).text(action_label);
				},
			});
		},
	};

	if (mode === "remap") {
		dialog_config.secondary_action_label = __("Refresh from WordPress");
		dialog_config.secondary_action = function () {
			let prev_cols = new Set(fields.map((f) => f.column_name));
			let table_name_override =
				new_table_name && new_table_name !== frm.doc.table_name ? new_table_name : undefined;
			frappe.call({
				method: "preview_schema",
				doc: frm.doc,
				args: { table_name_override: table_name_override },
				freeze: true,
				freeze_message: __("Refreshing schema from WordPress..."),
				callback: function (r) {
					if (!r.message) {
						return;
					}
					let new_cols = new Set(r.message.fields.map((f) => f.column_name));
					let schema_changed =
						prev_cols.size !== new_cols.size ||
						[...prev_cols].some((c) => !new_cols.has(c)) ||
						[...new_cols].some((c) => !prev_cols.has(c));
					if (!schema_changed) {
						frappe.show_alert({
							message: __("Source schema unchanged."),
							indicator: "blue",
						});
						return;
					}
					d.hide();
					show_preview_dialog(frm, r.message, mode, new_table_name);
				},
			});
		};
	}

	let d = new frappe.ui.Dialog(dialog_config);

	// Build the two-tab preview
	let remap_footer_hint =
		mode === "remap"
			? `<p class="text-muted" style="margin: 0 0 10px 0;">${__(
					"Use both tabs as needed. Save & remap (dialog footer) applies everything in one step.",
			  )}</p>`
			: "";
	let html = `
		${remap_footer_hint}
		<ul class="nav nav-tabs schema-tabs" role="tablist" style="margin-bottom: 0;">
			<li class="nav-item">
				<a class="nav-link active schema-tab-link" data-tab-target="tab-mapping" href="javascript:void(0)" role="tab">${__("Data Mapping")}</a>
			</li>
			<li class="nav-item">
				<a class="nav-link schema-tab-link" data-tab-target="tab-settings" href="javascript:void(0)" role="tab">${__("Frappe Field Settings")}</a>
			</li>
		</ul>
		<div class="tab-content" style="border: 1px solid var(--border-color, #d1d8dd); border-top: none; border-radius: 0 0 var(--border-radius, 6px) var(--border-radius, 6px);">

		<!-- ═══ TAB 1: Data Mapping ═══ -->
		<div class="tab-pane active" id="tab-mapping" data-dirty="false" role="tabpanel">
		<div style="padding: 10px 10px 0;">
			<span class="text-muted">${__(
				"Review the proposed field types below. Adjust any that look incorrect before creating the DocType.",
			)}</span>
			<br>
			<span class="text-muted"><strong>${__("Matching Fields:")}</strong> ${__(
				"Select up to 3 fields to use for matching records during sync (useful when the table lacks unique keys).",
			)}</span>
		<br>
		<span class="text-muted"><strong>${__("Frappe ID:")}</strong> ${__(
			"Select one column to use as Frappe's record ID (skips field creation, enables fast direct lookup).",
		)}</span>
		<br>
		<span class="text-muted"><strong>${__("Auto:")}</strong> ${__(
			"Mark columns that are auto-generated by the source (e.g. auto_increment). These will be skipped when writing records back to the source.",
		)}</span>
		<br>
		<span class="text-muted"><strong>${__("Mod TS / Created TS:")}</strong> ${__(
			"Pick the modified-timestamp field (required) and optionally the created-timestamp field. Only datetime/timestamp columns are selectable.",
		)}</span>
		</div>
		<div class="schema-grid-scroll" style="overflow: auto;">
			<table class="table table-bordered table-sm" style="font-size: 13px;">
				<thead style="position: sticky; top: 0; background: var(--fg-color, #fff); z-index: 1;">
					<tr>
						<th style="width: 5%;">${__("Match")}</th>
						<th style="width: 5%;" title="${__("Map this column directly to Frappe\'s record ID (name field)")}">${__("Frappe ID")}</th>
						<th style="width: 5%;">${__("Auto")}</th>
						<th style="width: 6%;" title="${__("Modified timestamp — required")}"><span style="color:#d44;">${__("Mod TS")}</span></th>
						<th style="width: 6%;" title="${__("Created timestamp — optional")}">${__("Crt TS")}</th>
						<th style="width: 16%;">${__("Column")}</th>
						<th style="width: 12%;">${__("DB Type")}</th>
						<th style="width: 16%;">${__("Frappe Type")}</th>
						<th style="width: 7%;">${__("Nullable")}</th>
						<th style="width: 12%;">${__("Keys")}</th>
					</tr>
				</thead>
				<tbody>
	`;

	// Track which column gets auto-selected as Title (only first match)
	let _title_auto_detected = false;
	let previous_title_column = preview_data.previous_title_field_column || null;

	// Track previous read-only / pick-list columns (for remap — wired in later commits)
	let previous_read_only = preview_data.previous_read_only_columns || [];
	let previous_pick_list = preview_data.previous_pick_list_columns || [];
	let previous_bold = preview_data.previous_bold_columns || [];

	// Accumulate Tab 2 (Frappe Field Settings) rows separately
	let settings_html = "";

	fields.forEach(function (f) {
		// Build keys badges
		let keys = [];
		if (f.is_primary_key) keys.push('<span class="badge badge-danger">PK</span>');
		if (f.is_unique) keys.push('<span class="badge badge-warning">UQ</span>');
		if (f.is_indexed) keys.push('<span class="badge badge-info">IDX</span>');
		if (f.is_virtual) keys.push('<span class="badge badge-secondary">VIRTUAL</span>');
		let keys_html = keys.length > 0 ? keys.join(" ") : "—";

		// Build select dropdown for Frappe type
		let options_html = FRAPPE_FIELD_TYPES.map(function (ft) {
			let selected = ft === f.proposed_fieldtype ? "selected" : "";
			return `<option value="${ft}" ${selected}>${ft}</option>`;
		}).join("");

		// Highlight if the DB type suggests the auto-detection might be off
		// (e.g., longtext for a view column that could be anything)
		let row_class = f.db_type === "longtext" ? 'style="background: #fff8e1;"' : "";

		// Pre-check matching field checkbox:
		// 1. If previously selected by user (from saved matching_fields)
		// 2. Or if it's a PK or unique field (for new mirrors)
		// When "Use as Name" is selected for a column, Match is disabled (that column IS the match key)
		let checked = "";
		if (previous_matching.length > 0) {
			// Use previous user selection
			checked = previous_matching.includes(f.column_name.toLowerCase()) ? "checked" : "";
		} else {
			// Default: check PK and unique fields
			checked = f.is_primary_key || f.is_unique ? "checked" : "";
		}

		// Pre-select "Use as Name" radio: previous selection, or only PK if exactly one
		let pk_count = fields.filter((x) => x.is_primary_key).length;
		let name_checked = "";
		if (previous_name_column && previous_name_column === f.column_name) {
			name_checked = "checked";
		} else if (!previous_name_column && pk_count === 1 && f.is_primary_key) {
			name_checked = "checked";
		}

		// Pre-select "Title" radio: previous selection, or first text-ish column
		// matching common title patterns (but NOT the Frappe ID column)
		let title_checked = "";
		if (previous_title_column && previous_title_column === f.column_name) {
			title_checked = "checked";
		} else if (!previous_title_column && !_title_auto_detected && !name_checked) {
			if (/title|label|subject|_name$/.test(f.column_name.toLowerCase())) {
				title_checked = "checked";
				_title_auto_detected = true;
			}
		}

		// Pre-check "Auto" if column is auto_increment (or user previously set it)
		let previous_auto_generated = preview_data.previous_auto_generated_columns || [];
		let auto_checked = "";
		if (previous_auto_generated.includes(f.column_name.toLowerCase())) {
			auto_checked = "checked";
		} else if (!previous_auto_generated.length && f.is_auto_increment) {
			auto_checked = "checked";
		}

		// Timestamp radio buttons — only enabled for datetime/timestamp columns
		let is_datetime = ["datetime", "timestamp", "Datetime"].some((t) =>
			f.db_type.toLowerCase().includes(t.toLowerCase()),
		);
		let previous_mod_ts = (preview_data.previous_modified_ts || "").toLowerCase();
		let previous_crt_ts = (preview_data.previous_created_ts || "").toLowerCase();
		let col_lower = f.column_name.toLowerCase();

		let mod_ts_cell = "";
		let crt_ts_cell = "";
		if (is_datetime) {
			let mod_checked = previous_mod_ts && previous_mod_ts === col_lower ? "checked" : "";
			// Default: pre-select first field named like "post_modified" / "updated_at" / "modified"
			if (!mod_checked && !previous_mod_ts) {
				if (/modif|updated/.test(col_lower)) mod_checked = "checked";
			}
			let crt_checked = previous_crt_ts && previous_crt_ts === col_lower ? "checked" : "";
			if (!crt_checked && !previous_crt_ts) {
				if (/creat|post_date(?!_gmt)/.test(col_lower)) crt_checked = "checked";
			}
			mod_ts_cell = `<input type="radio" name="mod_ts_radio" class="mod-ts-radio"
				value="${f.column_name}" data-column="${f.column_name}" ${mod_checked}>`;
			crt_ts_cell = `<input type="radio" name="crt_ts_radio" class="crt-ts-radio"
				value="${f.column_name}" data-column="${f.column_name}" ${crt_checked}>`;
		} else {
			mod_ts_cell = `<span style="color:#ccc;" title="${__("Not a datetime column")}">—</span>`;
			crt_ts_cell = `<span style="color:#ccc;" title="${__("Not a datetime column")}">—</span>`;
		}

		// Read Only checkbox — forced on for virtual, Frappe ID, Mod/Crt TS only.
		// Matching-key columns no longer lock read-only (users can clear it; server respects unchecked).
		let ro_forced =
			f.is_virtual ||
			!!name_checked ||
			!!mod_ts_cell.includes("checked") ||
			!!crt_ts_cell.includes("checked");
		let ro_saved = previous_read_only.includes(f.column_name.toLowerCase());
		let ro_checked = (ro_forced || ro_saved) ? "checked" : "";

		// Pick List checkbox — restore from previous
		let pl_checked = previous_pick_list.includes(f.column_name.toLowerCase()) ? "checked" : "";

		// Bold checkbox — restore from previous
		let bold_checked = previous_bold.includes(f.column_name.toLowerCase()) ? "checked" : "";

		let is_reserved_col = RESERVED_FIELDNAMES.includes(f.column_name.toLowerCase());
		let reserved_cls = is_reserved_col ? " reserved-source-col" : "";
		let label_styles = "";
		let label_readonly = "";
		let reserved_hint = "";

		// In remap mode, existing columns get a read-only label
		let is_locked = mode === "remap" && f.is_existing;
		if (is_locked) {
			label_styles = "opacity:0.6;cursor:not-allowed;";
			label_readonly = " readonly";
		}

		if (is_reserved_col && !is_locked) {
			let scrubbed = _scrub_fieldname(f.label);
			let still_bad = RESERVED_FIELDNAMES.includes(scrubbed);
			label_styles += still_bad
				? "border-color:#dc3545;background-color:#fff0f0;"
				: "border-color:#28a745;background-color:#f0fff0;";
			reserved_hint = `<div class="reserved-label-hint text-danger" style="font-size:11px;margin-top:2px;${still_bad ? "" : "display:none;"}">⚠ "${f.column_name}" is reserved — choose a unique label</div>`;
		}
		let label_style_attr = label_styles ? ` style="${label_styles}"` : "";

		// ═══ Tab 1 row: Data Mapping ═══
		html += `
			<tr ${row_class}>
				<td style="text-align: center;">
					<input type="checkbox" class="matching-field-checkbox"
						data-column="${f.column_name}" ${checked}>
				</td>
				<td style="text-align: center;">
					<input type="radio" name="name_field_radio" class="name-field-radio"
						value="${f.column_name}" data-column="${f.column_name}" ${name_checked}>
				</td>
				<td style="text-align: center;">
					<input type="checkbox" class="auto-generated-checkbox"
						data-column="${f.column_name}" ${auto_checked}>
				</td>
				<td style="text-align: center;">${mod_ts_cell}</td>
				<td style="text-align: center;">${crt_ts_cell}</td>
				<td><strong>${f.column_name}</strong></td>
				<td><code>${f.db_type}</code></td>
				<td>
					<select class="form-control form-control-sm field-type-select"
						data-column="${f.column_name}"
						data-original="${f.proposed_fieldtype}">
						${options_html}
					</select>
				</td>
				<td>${f.is_nullable === "YES" ? "Yes" : "<strong>No</strong>"}</td>
				<td>${keys_html}</td>
			</tr>
		`;

		// ═══ Tab 2 row: Frappe Field Settings ═══
		// Virtual fields cannot be title — Frappe resolves title via SQL (no DB column)
		let title_disabled = f.is_virtual ? 'disabled style="opacity: 0.3;"' : "";
		if (f.is_virtual && title_checked) title_checked = "";  // clear if previously set on a virtual

		// Resolve Frappe fieldname for display in Tab 2
		let display_fieldname = f.column_name.toLowerCase();
		if (RESERVED_FIELDNAMES.includes(display_fieldname)) {
			let lbl = f.label || "";
			let scrubbed = _scrub_fieldname(lbl);
			if (scrubbed && !RESERVED_FIELDNAMES.includes(scrubbed)) {
				display_fieldname = scrubbed;
			} else {
				display_fieldname = display_fieldname + "_field";
			}
		}

		settings_html += `
			<tr>
				<td style="font-size: 12px; color: var(--text-muted);">${f.column_name}</td>
				<td style="font-size: 12px;"><code>${display_fieldname}</code></td>
				<td style="text-align: center;">
					<input type="radio" name="title_field_radio" class="title-field-radio"
						value="${f.column_name}" data-column="${f.column_name}" ${title_checked} ${title_disabled}>
				</td>
				<td>
					<input type="text" class="form-control form-control-sm field-label-input${reserved_cls}"
						data-column="${f.column_name}"
						data-original="${f.label}"
						value="${f.label}"${label_style_attr}${label_readonly}>
					${reserved_hint}
				</td>
				<td>
					<input type="text" class="form-control form-control-sm default-value-input"
						data-column="${f.column_name}"
						autocomplete="off"
						placeholder="${__("Optional")}">
				</td>
				<td style="text-align: center;">
					<input type="checkbox" class="read-only-checkbox"
						data-column="${f.column_name}" data-virtual="${f.is_virtual ? 1 : 0}" ${ro_checked}>
				</td>
				<td style="text-align: center;">
					<input type="checkbox" class="pick-list-checkbox"
						data-column="${f.column_name}" ${pl_checked}>
				</td>
				<td style="text-align: center;">
					<input type="checkbox" class="bold-checkbox"
						data-column="${f.column_name}" ${bold_checked}>
				</td>
			</tr>
		`;
	});

	// Close Tab 1 table + pane
	html += `
				</tbody>
			</table>
		</div>
		</div><!-- /tab-mapping -->

		<!-- ═══ TAB 2: Frappe Field Settings ═══ -->
		<div class="tab-pane" id="tab-settings" data-dirty="false" role="tabpanel">
		<div style="padding: 10px 10px 0;">
			<span class="text-muted"><strong>${__("Title:")}</strong> ${__(
				"Select one column to use as the display title in list views and link fields.",
			)}</span>
			<br>
			<span class="text-muted"><strong>${__("Read Only:")}</strong> ${__(
				"Mark fields as read-only on the Frappe form. Auto-checked for ID, virtual, matching, and timestamp fields.",
			)}</span>
			<br>
			<span class="text-muted"><strong>${__("Pick List:")}</strong> ${__(
				"Convert field to a Select dropdown populated from distinct source values.",
			)}</span>
			<br>
			<span class="text-muted"><strong>${__("Bold:")}</strong> ${__(
				"Display the field value in bold on the form — same as bold in Customize Form.",
			)}</span>
			<br>
			<span class="text-muted"><strong>${__("Default value:")}</strong> ${__(
				"Optional hint for your scripts when creating new records — not applied automatically. Use SQL-style date (YYYY-MM-DD), datetime (YYYY-MM-DD HH:MM:SS), 24-hour time (HH:MM:SS), valid JSON for JSON fields; other types are checked (integer, number, check, rating).",
			)}</span>
		</div>
		<div class="schema-grid-scroll" style="overflow: auto;">
			<table class="table table-bordered table-sm" style="font-size: 13px;">
				<thead style="position: sticky; top: 0; background: var(--fg-color, #fff); z-index: 1;">
					<tr>
						<th style="width: 12%;">${__("WP Column")}</th>
						<th style="width: 11%;">${__("Frappe Field")}</th>
						<th style="width: 5%;" title="${__("Display title in list views and link fields")}">${__("Title")}</th>
						<th style="width: 18%;">${__("Label")}</th>
						<th style="width: 16%;" title="${__("Optional script hint when creating new records; validated by field type")}">${__("Default value")}</th>
						<th style="width: 7%;" title="${__("Read-only on Frappe form")}">${__("Read Only")}</th>
						<th style="width: 7%;" title="${__("Populate Select from distinct source values")}">${__("Pick List")}</th>
						<th style="width: 7%;" title="${__("Display field value in bold on form")}">${__("Bold")}</th>
					</tr>
				</thead>
				<tbody>
					${settings_html}
				</tbody>
			</table>
		</div>
		</div><!-- /tab-settings -->

		</div><!-- /tab-content -->
	`;

	d.fields_dict.field_preview.$wrapper.html(html);

	fields.forEach(function (f) {
		d.$wrapper
			.find(".default-value-input")
			.filter(function () {
				return $(this).data("column") === f.column_name;
			})
			.val(f.default_value == null ? "" : f.default_value);
	});

	// ═══ Manual tab switching (avoids Frappe router intercepting # anchors) ═══
	d.$wrapper.on("click", ".schema-tab-link", function (e) {
		e.preventDefault();
		let target = $(this).data("tab-target");
		// Deactivate all tabs and panes
		d.$wrapper.find(".schema-tab-link").removeClass("active");
		d.$wrapper.find(".tab-pane").removeClass("active show");
		// Activate clicked tab and matching pane
		$(this).addClass("active");
		d.$wrapper.find("#" + target).addClass("active show");
	});

	// ═══ Dirty tracking per tab ═══
	// Mark Tab 1 (Data Mapping) dirty on any change inside it
	d.$wrapper.on(
		"change",
		"#tab-mapping input, #tab-mapping select",
		function () {
			d.$wrapper.find("#tab-mapping").data("dirty", true);
		},
	);
	// Mark Tab 2 (Frappe Field Settings) dirty on any change inside it
	d.$wrapper.on(
		"change input",
		"#tab-settings input, #tab-settings select",
		function () {
			d.$wrapper.find("#tab-settings").data("dirty", true);
		},
	);

	// When "Frappe ID" radio is selected:
	//  • Grey out the Frappe Type dropdown for that row (type is irrelevant — no field created)
	//  • If the same column is selected as Title, clear the Title radio
	//  • Disable the Title radio for the Frappe ID column (no field = can't be title)
	function _refresh_frappe_id_state() {
		let $checked_radio = d.$wrapper.find(".name-field-radio:checked");
		let name_selected = $checked_radio.length > 0;
		let id_col = name_selected ? $checked_radio.val() : null;

		// Match checkboxes — always enabled, even when Frappe ID is selected
		// (user may need a separate match key, e.g. auto-increment PK for WP push-back)
		d.$wrapper.find(".matching-field-checkbox").each(function () {
			let $cb = $(this);
			$cb.prop("disabled", false);
		});

		// Title radio (in Tab 2) — disable for the Frappe ID column (no DocType field = can't be title)
		d.$wrapper.find(".title-field-radio").each(function () {
			let col = $(this).data("column");
			if (id_col && col === id_col) {
				if ($(this).prop("checked")) {
					$(this).prop("checked", false);
					// Cross-tab side-effect: mark Tab 2 dirty since we changed a Title radio
					d.$wrapper.find("#tab-settings").data("dirty", true);
					frappe.show_alert({
						message: __("Title cleared — the Frappe ID column does not create a field"),
						indicator: "orange",
					});
				}
				$(this).prop("disabled", true).css({ opacity: "0.45" });
			} else {
				$(this).prop("disabled", false).css({ opacity: "" });
			}
		});

		// Frappe Type dropdowns — disable + grey out only the selected ID column
		// Also force its value to "Data" (Frappe name is always varchar)
		d.$wrapper.find(".field-type-select").each(function () {
			let col = $(this).data("column");
			if (id_col && col === id_col) {
				$(this)
					.val("Data")
					.prop("disabled", true)
					.css({ opacity: "0.45", "pointer-events": "none" })
					.attr(
						"title",
						__(
							"This column maps to Frappe's name field (varchar) — no separate field is created",
						),
					);
			} else {
				// Only restore if not overridden by Pick List
				let $pl = d.$wrapper.find(`.pick-list-checkbox[data-column="${col}"]`);
				if ($pl.length && $pl.prop("checked")) {
					// Pick List owns this dropdown — leave it as Select
				} else {
					$(this)
						.prop("disabled", false)
						.css({ opacity: "", "pointer-events": "" })
						.removeAttr("title")
						// Restore original proposed type when deselected
						.val($(this).data("original"));
				}
			}
		});

		// Refresh RO state since Frappe ID change affects auto-check
		_refresh_read_only_state();
	}

	// Refresh Read Only checkboxes: auto-check for virtual, Frappe ID, matching, TS columns
	// Auto-checked items get reduced opacity to signal they're system-set
	function _refresh_read_only_state() {
		let id_col = d.$wrapper.find(".name-field-radio:checked").val() || null;
		let mod_ts_col = d.$wrapper.find(".mod-ts-radio:checked").val() || null;
		let crt_ts_col = d.$wrapper.find(".crt-ts-radio:checked").val() || null;

		d.$wrapper.find(".read-only-checkbox").each(function () {
			let col = $(this).data("column");
			let is_virtual = $(this).data("virtual") == 1;
			// Do not include matching fields — user may turn read-only off for those columns.
			let should_force =
				is_virtual || col === id_col || col === mod_ts_col || col === crt_ts_col;

			if (should_force) {
				$(this).prop("checked", true).data("auto", 1).css({ opacity: "0.5" });
			} else if ($(this).data("auto") == 1) {
				// Was forced (e.g. previously ID/TS) but no longer — uncheck
				$(this).prop("checked", false).data("auto", 0).css({ opacity: "" });
			} else {
				// Manual selection — leave as-is, restore opacity
				$(this).css({ opacity: "" });
			}
		});
	}

	d.$wrapper.on("change", ".name-field-radio", _refresh_frappe_id_state);
	d.$wrapper.on("change", ".title-field-radio", function () {
		let col = $(this).val();

		// Block virtual columns — Frappe resolves title_field via direct SQL,
		// which cannot see virtual (computed) fields
		let field_data = fields.find((f) => f.column_name === col);
		if (field_data && field_data.is_virtual) {
			$(this).prop("checked", false);
			frappe.show_alert({
				message: __("Virtual fields cannot be used as Title — Frappe resolves titles via SQL, which skips computed fields"),
				indicator: "orange",
			});
			return;
		}

		// Validate against Frappe ID
		let id_col = d.$wrapper.find(".name-field-radio:checked").val() || null;
		if (id_col && col === id_col) {
			$(this).prop("checked", false);
			frappe.show_alert({
				message: __("Title field cannot be the same as Frappe ID"),
				indicator: "orange",
			});
		}
	});

	// RO auto-refresh on matching/TS changes
	d.$wrapper.on("change", ".matching-field-checkbox", _refresh_read_only_state);
	d.$wrapper.on("change", ".mod-ts-radio", _refresh_read_only_state);
	d.$wrapper.on("change", ".crt-ts-radio", _refresh_read_only_state);

	// Trigger on load
	_refresh_frappe_id_state();
	_refresh_read_only_state();

	// Limit matching field selection to 3
	d.$wrapper.on("change", ".matching-field-checkbox", function () {
		let checked_count = d.$wrapper.find(".matching-field-checkbox:checked").length;
		if (checked_count > 3) {
			$(this).prop("checked", false);
			frappe.show_alert({
				message: __("Maximum 3 matching fields allowed"),
				indicator: "orange",
			});
		}
	});

	// Pick List checkbox — force Frappe Type to Select when checked, restore when unchecked
	d.$wrapper.on("change", ".pick-list-checkbox", function () {
		let col = $(this).data("column");
		let $select = d.$wrapper.find(`.field-type-select[data-column="${col}"]`);
		if ($(this).prop("checked")) {
			$select
				.val("Select")
				.prop("disabled", true)
				.css({ opacity: "0.45", "pointer-events": "none" })
				.attr("title", __("Type forced to Select — options loaded from source data"));
		} else {
			$select
				.prop("disabled", false)
				.css({ opacity: "", "pointer-events": "" })
				.removeAttr("title")
				.val($select.data("original"));
		}
	});
	// Trigger on load for any pre-checked pick-list columns
	d.$wrapper.find(".pick-list-checkbox:checked").trigger("change");

	// Highlight changed dropdowns
	d.$wrapper.on("change", ".field-type-select", function () {
		let original = $(this).data("original");
		if ($(this).val() !== original) {
			$(this).css("border-color", "#f0ad4e");
			$(this).css("background-color", "#fff8e1");
		} else {
			$(this).css("border-color", "");
			$(this).css("background-color", "");
		}
	});

	// Highlight changed labels (with reserved-column validation)
	d.$wrapper.on("input", ".field-label-input", function () {
		let col_name = $(this).data("column");
		let original = $(this).data("original");
		let label = $(this).val().trim();
		let is_reserved = RESERVED_FIELDNAMES.includes(col_name.toLowerCase());

		if (is_reserved) {
			let scrubbed = _scrub_fieldname(label);
			let $hint = $(this).parent().find(".reserved-label-hint");
			if (!label || RESERVED_FIELDNAMES.includes(scrubbed)) {
				$(this).css({ "border-color": "#dc3545", "background-color": "#fff0f0" });
				$hint.show();
			} else {
				$(this).css({ "border-color": "#28a745", "background-color": "#f0fff0" });
				$hint.hide();
			}
		} else if (label !== original) {
			$(this).css("border-color", "#f0ad4e");
			$(this).css("background-color", "#fff8e1");
		} else {
			$(this).css("border-color", "");
			$(this).css("background-color", "");
		}
	});

	d.show();

	// Make the dialog resizable from the bottom-right corner
	let $modal_dialog = d.$wrapper.find(".modal-dialog");
	let $modal_content = d.$wrapper.find(".modal-content");
	let $modal_body = d.$wrapper.find(".modal-body");

	// Expand to near-full width and set a tall initial height
	$modal_dialog.css({
		"max-width": "95vw",
		"width": "95vw",
	});
	$modal_content.css({
		"resize": "both",
		"overflow": "hidden",
		"min-width": "600px",
		"min-height": "400px",
		"max-width": "98vw",
		"max-height": "90vh",
	});
	$modal_body.css({
		"overflow": "auto",
		"max-height": "calc(90vh - 120px)",
	});
}

function show_sync_progress_dialog(frm) {
	let label = frm.doc.nce_name || frm.doc.table_name || frm.doc.name;
	let last_log = "";
	let poll_timer = null;

	let d = new frappe.ui.Dialog({
		title: __("Sync Progress — {0}", [label]),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "progress_area" }],
		primary_action_label: __("Running…"),
		primary_action: function () {
			_stop_poll();
			d.hide();
			frm.reload_doc();
		},
	});

	d.get_primary_btn().prop("disabled", true);

	d.fields_dict.progress_area.$wrapper.html(`
		<div id="sync-log-box" style="
			font-family: monospace; font-size: 12px;
			background: var(--bg-color, #f8f8f8);
			border: 1px solid var(--border-color, #ddd);
			border-radius: 4px; padding: 12px;
			min-height: 120px; max-height: 360px;
			overflow-y: auto; white-space: pre-wrap; word-break: break-all;">
			<span class="text-muted">${__("Waiting for worker to start…")}</span>
		</div>
	`);

	d.show();

	function _append(text, color) {
		let $box = d.$wrapper.find("#sync-log-box");
		let ts = new Date().toLocaleTimeString();
		let style = color ? `style="color:${color};font-weight:bold;"` : "";
		$box.append(`<div ${style}>[${ts}]  ${text}</div>`);
		$box.scrollTop($box[0].scrollHeight);
	}

	function _stop_poll() {
		if (poll_timer) {
			clearInterval(poll_timer);
			poll_timer = null;
		}
	}

	function _poll() {
		frappe.db.get_value(
			"WP Tables",
			frm.doc.name,
			["last_sync_log", "last_sync_status"],
			function (data) {
				if (!data) return;
				let log = data.last_sync_log || "";
				let status = data.last_sync_status || "";

				if (log && log !== last_log) {
					last_log = log;
					_append(log);
				}

				if (status && status !== "Running") {
					_stop_poll();
					let color =
						status === "Success"
							? "#28a745"
							: status === "Warning"
								? "#fd7e14"
								: "#dc3545";
					_append(__("Sync finished: {0}", [status]), color);
					// Reload form immediately so badge/status updates without waiting for Close
					frm.reload_doc();
					d.set_primary_action(__("Close"), function () {
						d.hide();
					});
					d.get_primary_btn().prop("disabled", false);
				}
			},
		);
	}

	poll_timer = setInterval(_poll, 1500);
	d.$wrapper.on("hide.bs.modal", function () {
		_stop_poll();
	});
}

function show_preview_sync_counts_dialog(frm, data) {
	let label = frm.doc.nce_name || frm.doc.table_name || frm.doc.name;
	let is_truncate = data.sync_method === "Truncate & Replace";
	let html;

	if (is_truncate) {
		html = `
			<p><strong>${__("Sync Method")}:</strong> ${frappe.utils.escape_html(data.sync_method)}</p>
			<p style="font-size: 1.1em; margin-top: 12px;">
				<strong>${__("Source records to upsert")}:</strong>
				<span style="font-size: 1.25em; font-weight: bold;">${data.source_upserts}</span>
			</p>
			<p class="text-muted">${__(
				"Truncate & Replace will reload all rows from the WordPress source table.",
			)}</p>
		`;
	} else {
		html = `
			<p><strong>${__("Sync Method")}:</strong> ${frappe.utils.escape_html(data.sync_method)}</p>
			<table class="table table-bordered" style="margin-top: 12px;">
				<tbody>
					<tr>
						<td>${__("Source records to upsert")}</td>
						<td style="font-weight: bold;">${data.source_upserts}</td>
					</tr>
					<tr>
						<td>${__("Target records to drop")}</td>
						<td style="font-weight: bold;">${data.target_drops}</td>
					</tr>
				</tbody>
			</table>
			${
				data.cutoff
					? `<p class="text-muted"><strong>${__("Cutoff (WP timezone)")}:</strong> ${frappe.utils.escape_html(data.cutoff)}</p>`
					: `<p class="text-muted">${__("No cutoff — first sync or empty Frappe table.")}</p>`
			}
		`;
	}

	let d = new frappe.ui.Dialog({
		title: __("Preview Sync Counts — {0}", [label]),
		fields: [{ fieldtype: "HTML", fieldname: "preview_area" }],
		primary_action_label: __("Close"),
		primary_action: function () {
			d.hide();
		},
	});
	d.fields_dict.preview_area.$wrapper.html(html);
	d.show();
}
