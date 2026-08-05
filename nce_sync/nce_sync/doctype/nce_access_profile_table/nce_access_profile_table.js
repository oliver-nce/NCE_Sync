// Copyright (c) 2026, Oliver Reid and contributors
// For license information, please see license.txt

frappe.ui.form.on("NCE Access Profile Table", {
	manage_fields(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.document_type) {
			frappe.msgprint(__("Pick a Document Type on this row first."));
			return;
		}
		show_manage_fields_dialog(row.document_type);
	},
});

function show_manage_fields_dialog(document_type) {
	const d = new frappe.ui.Dialog({
		title: __("Restricted Fields — {0}", [document_type]),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "help_html",
				options: `<p class="text-muted">${__(
					"Checking a field here marks it Restricted (Permission Level 1) for every role in the app, not just this profile. A role only sees Restricted fields if it has base Read on this DocType, and can only edit them if its Table Access row has \"Write Restricted Fields\" checked."
				)}</p>`,
			},
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
					html +=
						"<tr>" +
						`<td>${frappe.utils.escape_html(f.label || f.fieldname)}</td>` +
						`<td><code>${frappe.utils.escape_html(f.fieldname)}</code></td>` +
						`<td><input type="checkbox" class="field-restrict-toggle" data-fieldname="${frappe.utils.escape_html(
							f.fieldname
						)}" ${f.permlevel > 0 ? "checked" : ""}></td>` +
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

	d.show();
	render();
}
