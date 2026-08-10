// Copyright (c) 2026, Oliver Reid and contributors
// For license information, please see license.txt

frappe.ui.form.on("NCE Access Profile", {
	refresh(frm) {
		setup_table_access_manage_fields_buttons(frm);

		if (frm.is_new()) {
			return;
		}

		frm.add_custom_button(__("Apply Access"), () => {
			frappe.call({
				method:
					"nce_sync.nce_sync.doctype.nce_access_profile.nce_access_profile.apply_access",
				args: { name: frm.doc.name },
				freeze: true,
				freeze_message: __("Applying permissions..."),
				callback() {
					frappe.show_alert({
						message: __("Access applied for {0}", [frm.doc.role]),
						indicator: "green",
					});
				},
			});
		});

		frm.add_custom_button(__("Manage Users"), () => show_manage_users_dialog(frm));
	},
});

// Table Access row button. NCE Access Profile Table is a child (istable=1)
// doctype -- it never has its own page/route, so Frappe never auto-loads a
// co-located nce_access_profile_table.js for it. Declaring the handler here,
// in the parent's script (which does load, since Apply Access / Manage Users
// above already work), is what actually gets it registered.
frappe.ui.form.on("NCE Access Profile Table", {
	write(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.write) {
			frappe.model.set_value(cdt, cdn, "restrict_write", 0);
		}
	},
	restrict_write(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (row.restrict_write) {
			frappe.model.set_value(cdt, cdn, "write", 0);
			if (!row.read) {
				frappe.model.set_value(cdt, cdn, "read", 1);
			}
		}
	},
	manage_fields(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.document_type) {
			frappe.msgprint(__("Pick a Document Type on this row first."));
			return;
		}
		if (!row.restrict_write) {
			frappe.msgprint(__("Manage Fields is available when Restricted Write is checked."));
			return;
		}
		show_manage_fields_dialog(row.document_type);
	},
});

function setup_table_access_manage_fields_buttons(frm) {
	const grid = frm.fields_dict.table_access?.grid;
	if (!grid || !grid.wrapper) {
		return;
	}

	const refreshCells = () => refresh_table_access_manage_fields_cells(grid);

	if (!grid._nce_manage_fields_bound) {
		grid._nce_manage_fields_bound = true;
		grid.wrapper.on("grid-row-render.nce_manage_fields", refreshCells);
		grid.wrapper.on("grid-row-added.nce_manage_fields", refreshCells);
		grid.wrapper.on("grid-row-removed.nce_manage_fields", refreshCells);
	}
	refreshCells();
}

function refresh_table_access_manage_fields_cells(grid) {
	(grid.grid_rows || []).forEach((gridRow) => {
		const doc = gridRow.doc || {};
		const $cell =
			gridRow.columns?.manage_fields?.$wrapper ||
			(gridRow.row ? $(gridRow.row).find('[data-fieldname="manage_fields"]') : null);
		if (!$cell || !$cell.length) {
			return;
		}

		$cell.find(".nce-manage-fields-btn").remove();
		if (!doc.restrict_write || !doc.document_type) {
			return;
		}

		const $btn = $(
			'<button type="button" class="btn btn-default btn-xs nce-manage-fields-btn"></button>',
		)
			.text(__("Manage Fields"))
			.on("click", (e) => {
				e.preventDefault();
				e.stopPropagation();
				show_manage_fields_dialog(doc.document_type);
			});
		$cell.empty().append($btn);
	});
}

function show_manage_fields_dialog(document_type) {
	const d = new frappe.ui.Dialog({
		title: __("Restricted Fields — {0}", [document_type]),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "help_html",
				options: `<p class="text-muted">${__(
					"Restricted fields cannot be edited when a profile row uses Restricted Write. Full Write allows editing Restricted fields too. Restricted is app-wide on this DocType. Fields that are read-only in the DocType schema are locked here and always non-editable."
				)}</p>`,
			},
			{ fieldtype: "Button", fieldname: "restrict_all_btn", label: __("Restrict All") },
			{ fieldtype: "Column Break" },
			{ fieldtype: "Button", fieldname: "unrestrict_all_btn", label: __("Unrestrict All") },
			{ fieldtype: "Section Break" },
			{ fieldtype: "HTML", fieldname: "fields_html" },
		],
	});

	function render() {
		frappe.call({
			method:
				"nce_sync.nce_sync.doctype.nce_access_profile.nce_access_profile.get_doctype_fields",
			args: { doctype: document_type },
			freeze: true,
			callback(r) {
				const fields = r.message || [];
				let html =
					'<table class="table table-bordered"><thead><tr>' +
					`<th>${__("Field")}</th><th>${__("Fieldname")}</th><th>${__(
						"Restricted"
					)}</th></tr></thead><tbody>`;
				fields.forEach((f) => {
					const locked = f.locked || f.read_only;
					const restricted = (f.permlevel || 0) > 0 || locked;
					const lockNote = locked
						? ` <span class="text-muted">(${__("locked — read only in schema")})</span>`
						: "";
					html +=
						"<tr>" +
						`<td>${frappe.utils.escape_html(f.label || f.fieldname)}${lockNote}</td>` +
						`<td><code>${frappe.utils.escape_html(f.fieldname)}</code></td>` +
						`<td><input type="checkbox" class="field-restrict-toggle" data-fieldname="${frappe.utils.escape_html(
							f.fieldname
						)}" data-locked="${locked ? "1" : "0"}" ${restricted ? "checked" : ""}${
							locked ? " disabled" : ""
						}></td>` +
						"</tr>";
				});
				html += "</tbody></table>";
				if (!fields.length) {
					html = `<p class="text-muted">${__(
						"No restrictable fields found on this DocType."
					)}</p>`;
				}
				d.fields_dict.fields_html.$wrapper.html(html);
				d.fields_dict.fields_html.$wrapper.find(".field-restrict-toggle").on("change", function () {
					if ($(this).data("locked")) {
						return;
					}
					const fieldname = $(this).data("fieldname");
					const restricted = $(this).is(":checked");
					frappe.call({
						method:
							"nce_sync.nce_sync.doctype.nce_access_profile.nce_access_profile.set_field_restricted",
						args: { doctype: document_type, fieldname, restricted },
						callback() {
							frappe.show_alert({
								message: restricted
									? __("{0} marked Restricted", [fieldname])
									: __("{0} unrestricted", [fieldname]),
								indicator: restricted ? "orange" : "green",
							});
						},
					});
				});
			},
		});
	}

	d.fields_dict.restrict_all_btn.$input.on("click", () => bulk_set(1));
	d.fields_dict.unrestrict_all_btn.$input.on("click", () => bulk_set(0));

	function bulk_set(restricted) {
		frappe.call({
			method:
				"nce_sync.nce_sync.doctype.nce_access_profile.nce_access_profile.set_all_fields_restricted",
			args: { doctype: document_type, restricted },
			freeze: true,
			freeze_message: restricted ? __("Restricting all fields...") : __("Unrestricting all fields..."),
			callback() {
				frappe.show_alert({
					message: restricted
						? __("All fields on {0} marked Restricted", [document_type])
						: __("All fields on {0} unrestricted", [document_type]),
					indicator: restricted ? "orange" : "green",
				});
				render();
			},
		});
	}

	d.show();
	render();
}

function show_manage_users_dialog(frm) {
	const d = new frappe.ui.Dialog({
		title: __("Users with role: {0}", [frm.doc.role]),
		size: "large",
		fields: [
			{ fieldtype: "HTML", fieldname: "users_html" },
			{ fieldtype: "Section Break", label: __("Add Existing User") },
			{ fieldtype: "Link", fieldname: "add_user", label: __("User"), options: "User" },
			{ fieldtype: "Button", fieldname: "add_user_btn", label: __("Add") },
			{ fieldtype: "Section Break", label: __("Invite New User") },
			{ fieldtype: "Data", fieldname: "invite_email", label: __("Email") },
			{ fieldtype: "Data", fieldname: "invite_first_name", label: __("First Name"), reqd: 1 },
			{ fieldtype: "Data", fieldname: "invite_last_name", label: __("Last Name") },
			{ fieldtype: "Button", fieldname: "invite_btn", label: __("Send Invitation") },
		],
	});

	function render_users() {
		frappe.call({
			method:
				"nce_sync.nce_sync.doctype.nce_access_profile.nce_access_profile.get_profile_users",
			args: { name: frm.doc.name },
			callback(r) {
				const users = r.message || [];
				let html =
					'<table class="table table-bordered"><thead><tr>' +
					`<th>${__("User")}</th><th>${__("Name")}</th><th>${__(
						"Status"
					)}</th><th></th></tr></thead><tbody>`;
				users.forEach((u) => {
					html +=
						"<tr>" +
						`<td>${frappe.utils.escape_html(u.user)}</td>` +
						`<td>${frappe.utils.escape_html(u.full_name || "")}</td>` +
						`<td>${u.enabled ? __("Active") : __("Disabled")}</td>` +
						`<td><button class="btn btn-xs btn-danger remove-user" data-user="${frappe.utils.escape_html(
							u.user
						)}">${__("Remove")}</button></td>` +
						"</tr>";
				});
				html += "</tbody></table>";
				if (!users.length) {
					html = `<p class="text-muted">${__("No users assigned yet.")}</p>` + html;
				}
				d.fields_dict.users_html.$wrapper.html(html);
				d.fields_dict.users_html.$wrapper.find(".remove-user").on("click", function () {
					const user = $(this).data("user");
					frappe.confirm(__("Remove {0} from this role?", [user]), () => {
						frappe.call({
							method:
								"nce_sync.nce_sync.doctype.nce_access_profile.nce_access_profile.remove_user_from_profile",
							args: { name: frm.doc.name, user },
							callback(res) {
								if (res.message && res.message.disabled) {
									frappe.show_alert({
										message: __(
											"{0} had no other roles and was disabled.",
											[user]
										),
										indicator: "orange",
									});
								}
								render_users();
							},
						});
					});
				});
			},
		});
	}

	d.fields_dict.add_user_btn.$input.on("click", () => {
		const user = d.get_value("add_user");
		if (!user) return;
		frappe.call({
			method:
				"nce_sync.nce_sync.doctype.nce_access_profile.nce_access_profile.add_user_to_profile",
			args: { name: frm.doc.name, user },
			callback() {
				d.set_value("add_user", "");
				render_users();
			},
		});
	});

	d.fields_dict.invite_btn.$input.on("click", () => {
		const email = d.get_value("invite_email");
		const first_name = (d.get_value("invite_first_name") || "").trim();
		const last_name = (d.get_value("invite_last_name") || "").trim();
		if (!email || !first_name) {
			frappe.msgprint(__("Enter email and first name."));
			return;
		}
		frappe.call({
			method:
				"nce_sync.nce_sync.doctype.nce_access_profile.nce_access_profile.invite_user_to_profile",
			args: { name: frm.doc.name, email, first_name, last_name },
			freeze: true,
			freeze_message: __("Sending invitation..."),
			callback(r) {
				if (r.exc) {
					return;
				}
				const message =
					r.message && r.message.created
						? __("Invitation sent to {0}", [email])
						: __("User {0} added to this profile", [email]);
				frappe.show_alert({ message, indicator: "green" });
				d.hide();
			},
		});
	});

	d.show();
	render_users();
}
