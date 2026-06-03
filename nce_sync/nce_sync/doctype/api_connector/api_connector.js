// Copyright (c) 2026, Oliver Reid and contributors
// For license information, please see license.txt

const CREDENTIAL_FIELDS = [
	"api_key",
	"api_secret",
	"password",
	"bearer_token",
	"oauth_refresh_token",
];

function refresh_credential_labels(frm) {
	const is_secret = frm.doc.auth_type === "Secret";
	if (frm.fields_dict.api_secret) {
		frm.set_df_property(
			"api_secret",
			"label",
			is_secret ? __("Secret") : __("API Secret")
		);
	}
}

frappe.ui.form.on("API Connector", {
	refresh(frm) {
		refresh_credential_labels(frm);
		if (!frm.is_new()) {
			frm.add_custom_button(__("Test Connection"), function () {
				frappe.call({
					method: "nce_sync.nce_sync.doctype.api_connector.api_connector.test_connection",
					args: { connector_name: frm.doc.name },
					freeze: true,
					freeze_message: __("Testing connection..."),
					callback: function (r) {
						frm.reload_doc();
						if (r.message && r.message.success) {
							frappe.show_alert({
								message: __("Connection successful"),
								indicator: "green",
							});
						} else {
							frappe.show_alert({
								message: __("Connection failed: {0}", [
									r.message ? r.message.error : "Unknown error",
								]),
								indicator: "red",
							});
						}
					},
				});
			});

		if (frm.doc.notes) {
			frm.add_custom_button(__("Setup Guide"), function () {
				show_guide_popup(frm, "Setup Guide", frm.doc.notes);
			});
		}

		let $guide_btn = frm.add_custom_button(
			__("Implementation Guide"),
			function () {
				if (frm.doc.implementation_guide) {
					show_guide_popup(
						frm,
						"Implementation Guide",
						frm.doc.implementation_guide
					);
				} else {
					generate_implementation_guide(frm);
				}
			}
		);
		$guide_btn.append(
			' <span style="background:#7c3aed;color:#fff;font-size:9px;' +
				"padding:1px 5px;border-radius:3px;font-weight:600;" +
				'vertical-align:middle;margin-left:2px;">AI</span>'
		);

			add_copy_buttons(frm);
		}
	},
	auth_type(frm) {
		refresh_credential_labels(frm);
	},
});

function add_copy_buttons(frm) {
	CREDENTIAL_FIELDS.forEach(function (fieldname) {
		if (!frm.fields_dict[fieldname]) return;
		let $wrapper = frm.fields_dict[fieldname].$wrapper;
		if ($wrapper.find(".copy-credential-btn").length) return;

		let $btn = $(`<button class="btn btn-xs btn-default copy-credential-btn"
			style="position: absolute; right: 4px; top: 28px; z-index: 1; padding: 2px 8px;"
			title="${__("Copy to clipboard")}">
			<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"
				fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
				stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
				<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
		</button>`);

		$wrapper.css("position", "relative");
		$wrapper.append($btn);

		$btn.on("click", function (e) {
			e.preventDefault();
			e.stopPropagation();
			frappe.call({
				method: "nce_sync.nce_sync.doctype.api_connector.api_connector.get_credential",
				args: { connector_name: frm.doc.name, fieldname: fieldname },
				callback: function (r) {
					if (r.message) {
						frappe.utils.copy_to_clipboard(r.message);
						frappe.show_alert({
							message: __("Copied {0}", [frm.fields_dict[fieldname].df.label]),
							indicator: "green",
						});
					} else {
						frappe.show_alert({
							message: __("No value to copy"),
							indicator: "orange",
						});
					}
				},
			});
		});
	});

	// Username is a Data field — copy directly
	if (frm.fields_dict.username && frm.doc.username) {
		let $wrapper = frm.fields_dict.username.$wrapper;
		if (!$wrapper.find(".copy-credential-btn").length) {
			let $btn = $(`<button class="btn btn-xs btn-default copy-credential-btn"
				style="position: absolute; right: 4px; top: 28px; z-index: 1; padding: 2px 8px;"
				title="${__("Copy to clipboard")}">
				<svg xmlns="http://www.w3.org/2000/svg" width="14" height="14" viewBox="0 0 24 24"
					fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"
					stroke-linejoin="round"><rect x="9" y="9" width="13" height="13" rx="2" ry="2"/>
					<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/></svg>
			</button>`);

			$wrapper.css("position", "relative");
			$wrapper.append($btn);

			$btn.on("click", function (e) {
				e.preventDefault();
				e.stopPropagation();
				frappe.utils.copy_to_clipboard(frm.doc.username);
				frappe.show_alert({
					message: __("Copied Username"),
					indicator: "green",
				});
			});
		}
	}
}

function generate_implementation_guide(frm) {
	frappe.call({
		method:
			"nce_sync.nce_sync.doctype.api_connector.api_connector.ai_generate_guide",
		args: { connector_name: frm.doc.name },
		freeze: true,
		freeze_message: __("AI is generating the implementation guide…"),
		callback(r) {
			if (r.message) {
				frm.reload_doc();
				frappe.show_alert(
					{
						message: __("Generated guide for {0} ({1} endpoint guides)", [
							frm.doc.connector_name,
							r.message.endpoint_count,
						]),
						indicator: "green",
					},
					7
				);
				if (r.message.connector_guide) {
					show_guide_popup(frm, "Implementation Guide", r.message.connector_guide);
				}
			}
		},
	});
}

function show_guide_popup(frm, guide_type, content) {
	let cache_key = "_guide_dialog_" + guide_type.replace(/\s/g, "_").toLowerCase();
	if (frm[cache_key] && frm[cache_key].display) {
		frm[cache_key].show();
		return;
	}

	let ns = guide_type.replace(/\s/g, "").toLowerCase();

	let d = new frappe.ui.Dialog({
		title: __("{0} — {1}", [frm.doc.connector_name, guide_type]),
		size: "large",
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "guide_content",
			},
		],
		primary_action_label: __("Close"),
		primary_action() {
			d.hide();
		},
	});

	d.fields_dict.guide_content.$wrapper.html(
		'<div style="' +
			"padding: 15px; " +
			"font-size: 14px; " +
			"line-height: 1.6; " +
			"max-height: 70vh; " +
			"overflow-y: auto;" +
			'">' +
			content +
			"</div>"
	);

	d.$wrapper.find(".modal-dialog").css({
		"max-width": "700px",
		cursor: "move",
	});

	let modal_dialog = d.$wrapper.find(".modal-dialog");
	let header = d.$wrapper.find(".modal-header");
	let isDragging = false;
	let offsetX, offsetY;

	d.$wrapper.find(".modal").css({
		display: "flex",
		"align-items": "flex-start",
		"justify-content": "flex-end",
		"padding-top": "60px",
		"padding-right": "20px",
	});

	d.$wrapper.find(".modal-backdrop").css("display", "none");
	d.$wrapper.find(".modal").css("pointer-events", "none");
	modal_dialog.css("pointer-events", "auto");

	header.on("mousedown", function (e) {
		if ($(e.target).closest("button").length) return;
		isDragging = true;
		let rect = modal_dialog[0].getBoundingClientRect();
		offsetX = e.clientX - rect.left;
		offsetY = e.clientY - rect.top;
		modal_dialog.css("transition", "none");
		e.preventDefault();
	});

	$(document).on("mousemove." + ns, function (e) {
		if (!isDragging) return;
		modal_dialog.css({
			position: "fixed",
			left: e.clientX - offsetX + "px",
			top: e.clientY - offsetY + "px",
			margin: 0,
			transform: "none",
		});
	});

	$(document).on("mouseup." + ns, function () {
		isDragging = false;
	});

	d.onhide = function () {
		$(document).off("mousemove." + ns);
		$(document).off("mouseup." + ns);
	};

	frm[cache_key] = d;
	d.show();
}
