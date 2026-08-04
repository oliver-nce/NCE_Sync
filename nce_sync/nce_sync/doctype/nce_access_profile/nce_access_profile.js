// Copyright (c) 2026, Oliver Reid and contributors
// For license information, please see license.txt

frappe.ui.form.on("NCE Access Profile", {
	refresh(frm) {
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
			{ fieldtype: "Data", fieldname: "invite_name", label: __("Full Name") },
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
		const full_name = d.get_value("invite_name");
		if (!email || !full_name) {
			frappe.msgprint(__("Enter both email and full name."));
			return;
		}
		frappe.call({
			method:
				"nce_sync.nce_sync.doctype.nce_access_profile.nce_access_profile.invite_user_to_profile",
			args: { name: frm.doc.name, email, full_name },
			freeze: true,
			freeze_message: __("Sending invitation..."),
			callback() {
				frappe.show_alert({ message: __("Invitation sent to {0}", [email]), indicator: "green" });
				d.set_value("invite_email", "");
				d.set_value("invite_name", "");
				render_users();
			},
		});
	});

	d.show();
	render_users();
}
