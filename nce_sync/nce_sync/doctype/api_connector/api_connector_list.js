// Copyright (c) 2026, Oliver Reid and contributors
// For license information, please see license.txt
//
// code-index:begin
// {
//  "purpose": "List view script for API Connector with AI Discover chat dialog and connector creation flow.",
//  "read_when": "When changing AI Discover list button, chat UI, or create-connector-from-chat workflow.",
//  "gotchas": [],
//  "keywords": ["api connector", "list view", "ai discover", "chat dialog", "create connector", "desk"],
//  "format": 1
// }
// code-index:end

frappe.listview_settings["API Connector"] = {
	onload(listview) {
		let $btn = listview.page.add_inner_button(
			__("AI Discover"),
			() => open_ai_chat(listview)
		);
		$btn.append(
			' <span style="background:#7c3aed;color:#fff;font-size:9px;' +
				"padding:1px 5px;border-radius:3px;font-weight:600;" +
				'vertical-align:middle;margin-left:2px;">AI</span>'
		);
	},
};

const AI_BADGE =
	'<span style="background:#7c3aed;color:#fff;font-size:9px;' +
	"padding:1px 5px;border-radius:3px;font-weight:600;" +
	'vertical-align:middle;margin-left:4px;">AI</span>';

const CHAT_STYLES = `<style>
.ai-chat-messages {
	height: 450px; overflow-y: auto; padding: 18px;
	background: var(--bg-light-gray, #f8f9fa); border-radius: 8px;
	display: flex; flex-direction: column; gap: 14px;
}
.ai-msg { display: flex; }
.ai-msg-user { justify-content: flex-end; }
.ai-bubble {
	max-width: 80%; padding: 12px 16px; border-radius: 14px;
	font-size: 15px; line-height: 1.6; word-wrap: break-word;
	color: #1a1a1a;
}
.ai-msg-assistant .ai-bubble {
	background: #fff; border: 1px solid var(--border-color, #d1d8dd);
	border-bottom-left-radius: 4px;
}
.ai-msg-user .ai-bubble {
	background: #e3f2fd; border-bottom-right-radius: 4px;
}
.ai-bubble p { margin: 0 0 10px; }
.ai-bubble p:last-child { margin-bottom: 0; }
.ai-bubble ul, .ai-bubble ol { margin: 6px 0; padding-left: 22px; }
.ai-bubble li { margin-bottom: 4px; }
.ai-bubble strong { color: #111; }
.ai-typing {
	display: flex; align-items: center; gap: 4px; padding: 12px 16px;
	background: #fff; border: 1px solid var(--border-color, #d1d8dd);
	border-radius: 14px; border-bottom-left-radius: 4px;
	width: fit-content;
}
.ai-typing .dot {
	width: 7px; height: 7px; border-radius: 50%; background: #888;
	animation: ai-bounce 1.2s infinite;
}
.ai-typing .dot:nth-child(2) { animation-delay: 0.2s; }
.ai-typing .dot:nth-child(3) { animation-delay: 0.4s; }
@keyframes ai-bounce {
	0%, 60%, 100% { transform: translateY(0); }
	30% { transform: translateY(-4px); }
}
.ai-chat-input {
	display: flex; gap: 8px; margin-top: 14px; align-items: center;
}
.ai-chat-input input {
	flex: 1; border-radius: 8px; font-size: 15px;
	padding: 8px 12px; color: #1a1a1a;
}
.ai-chat-input button { font-size: 14px; padding: 7px 16px; }
.ai-chat-dialog .modal-dialog {
	max-width: 800px; cursor: move; resize: both; overflow: auto;
	min-width: 400px; min-height: 300px;
}
.ai-chat-dialog .modal-header { cursor: move; }
.ai-chat-dialog .modal-dialog::after {
	content: ""; position: absolute; bottom: 0; right: 0;
	width: 16px; height: 16px; cursor: nwse-resize;
}
</style>`;

// ── Chat dialog ────────────────────────────────────────────────────────────

function open_ai_chat(listview) {
	let messages = [];
	let busy = false;

	let d = new frappe.ui.Dialog({
		title: __("AI Discover API Connector") + " " + AI_BADGE,
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "chat_html" }],
		primary_action_label: __("Generate Connector"),
		primary_action() {
			if (messages.length < 2) return;
			generate_from_chat(messages, listview, d);
		},
		secondary_action_label: __("Close"),
	});

	let $area = d.fields_dict.chat_html.$wrapper;
	$area.html(
		CHAT_STYLES +
			'<div class="ai-chat-messages"></div>' +
			'<div class="ai-chat-input">' +
			'  <input type="text" class="form-control" placeholder="' +
			__("e.g. AWS — I need to send transactional emails…") +
			'">' +
			'  <button class="btn btn-primary btn-sm">' +
			__("Send") +
			"</button>" +
			"</div>"
	);

	let $msgs = $area.find(".ai-chat-messages");
	let $input = $area.find(".ai-chat-input input");
	let $sendBtn = $area.find(".ai-chat-input button");
	let $genBtn = d.get_primary_btn();
	$genBtn.prop("disabled", true);

	append_msg(
		$msgs,
		"assistant",
		"What API service would you like to connect?\n\n" +
			"Describe your use case — for example *\"AWS – I need to send " +
			'transactional emails"* — and I\'ll help narrow down the right ' +
			"service and endpoints."
	);

	function set_busy(state) {
		busy = state;
		$sendBtn.prop("disabled", state);
		$input.prop("disabled", state);
		if (!state) $input.focus();
	}

	function send_message() {
		if (busy) return;
		let text = $input.val().trim();
		if (!text) return;

		append_msg($msgs, "user", text);
		$input.val("");
		messages.push({ role: "user", content: text });

		set_busy(true);
		show_typing($msgs);

		frappe.call({
			method:
				"nce_sync.nce_sync.doctype.api_connector.api_connector.ai_discover_chat",
			args: { messages: messages },
			callback(r) {
				hide_typing($msgs);
				set_busy(false);
				if (r.message && r.message.reply) {
					let reply = r.message.reply;
					messages.push({ role: "assistant", content: reply });
					append_msg($msgs, "assistant", reply);
					$genBtn.prop("disabled", false);
				}
			},
			error() {
				hide_typing($msgs);
				set_busy(false);
			},
		});
	}

	$sendBtn.on("click", send_message);
	$input.on("keydown", function (e) {
		if (e.key === "Enter") {
			e.preventDefault();
			e.stopPropagation();
			send_message();
		}
	});

	d.show();

	// Mark dialog for custom styling
	d.$wrapper.addClass("ai-chat-dialog");

	// Make draggable via header
	let $modal = d.$wrapper.find(".modal-dialog");
	let $header = d.$wrapper.find(".modal-header");
	let isDragging = false;
	let dragOffsetX, dragOffsetY;

	$header.on("mousedown", function (e) {
		if ($(e.target).closest("button").length) return;
		isDragging = true;
		let rect = $modal[0].getBoundingClientRect();
		dragOffsetX = e.clientX - rect.left;
		dragOffsetY = e.clientY - rect.top;
		$modal.css("transition", "none");
		e.preventDefault();
	});

	$(document).on("mousemove.aichat", function (e) {
		if (!isDragging) return;
		$modal.css({
			position: "fixed",
			left: e.clientX - dragOffsetX + "px",
			top: e.clientY - dragOffsetY + "px",
			margin: 0,
			transform: "none",
		});
	});

	$(document).on("mouseup.aichat", function () {
		isDragging = false;
	});

	d.onhide = function () {
		$(document).off("mousemove.aichat");
		$(document).off("mouseup.aichat");
	};

	$input.focus();
}

// ── Chat helpers ───────────────────────────────────────────────────────────

function append_msg($msgs, role, text) {
	let html = render_md(text);
	$msgs.append(
		'<div class="ai-msg ai-msg-' +
			role +
			'">' +
			'<div class="ai-bubble">' +
			html +
			"</div></div>"
	);
	$msgs.scrollTop($msgs[0].scrollHeight);
}

function show_typing($msgs) {
	$msgs.append(
		'<div class="ai-typing">' +
			'<span class="dot"></span><span class="dot"></span><span class="dot"></span>' +
			"</div>"
	);
	$msgs.scrollTop($msgs[0].scrollHeight);
}

function hide_typing($msgs) {
	$msgs.find(".ai-typing").remove();
}

function render_md(text) {
	try {
		if (typeof marked !== "undefined") {
			return marked.parse(text);
		}
	} catch (e) {
		// fall through
	}
	return frappe.utils.escape_html(text).replace(/\n/g, "<br>");
}

// ── Generate + create ──────────────────────────────────────────────────────

function generate_from_chat(messages, listview, chat_dialog) {
	frappe.call({
		method:
			"nce_sync.nce_sync.doctype.api_connector.api_connector.ai_discover_generate",
		args: { messages: messages },
		freeze: true,
		freeze_message: __("AI is generating the connector…"),
		callback(r) {
			if (r.message) {
				chat_dialog.hide();
				show_discovery_results(r.message, listview);
			}
		},
	});
}

function show_discovery_results(data, listview) {
	let endpoint_rows = (data.endpoints || [])
		.map(
			(ep, i) =>
				"<tr>" +
				`<td>${i + 1}</td>` +
				`<td><strong>${frappe.utils.escape_html(ep.endpoint_name)}</strong></td>` +
				`<td><code>${ep.http_method}</code></td>` +
				`<td><code>${frappe.utils.escape_html(ep.path)}</code></td>` +
				`<td>${frappe.utils.escape_html(ep.description || "")}</td>` +
				"</tr>"
		)
		.join("");

	let html =
		'<div style="margin-bottom:15px;">' +
		"<h5>Connector Details</h5>" +
		'<table class="table table-bordered" style="font-size:13px;">' +
		`<tr><td style="width:140px;font-weight:600;">Name</td><td>${frappe.utils.escape_html(data.connector_name)}</td></tr>` +
		`<tr><td style="font-weight:600;">Service</td><td>${frappe.utils.escape_html(data.service)}</td></tr>` +
		`<tr><td style="font-weight:600;">Base URL</td><td><code>${frappe.utils.escape_html(data.base_url)}</code></td></tr>` +
		`<tr><td style="font-weight:600;">Auth Type</td><td>${frappe.utils.escape_html(data.auth_type)}</td></tr>` +
		`<tr><td style="font-weight:600;">Rate Limit</td><td>${data.rate_limit_rpm || "Not set"} req/min</td></tr>` +
		"</table></div>" +
		"<div>" +
		`<h5>Endpoints (${(data.endpoints || []).length})</h5>` +
		'<div style="max-height:300px;overflow-y:auto;">' +
		'<table class="table table-bordered table-striped" style="font-size:12px;">' +
		"<thead><tr><th>#</th><th>Name</th><th>Method</th><th>Path</th><th>Description</th></tr></thead>" +
		`<tbody>${endpoint_rows}</tbody></table></div></div>`;

	let confirm_dialog = new frappe.ui.Dialog({
		title:
			__("AI Discovery Results — {0}", [data.connector_name]) +
			" " +
			AI_BADGE,
		size: "extra-large",
		fields: [{ fieldtype: "HTML", fieldname: "results_html" }],
		primary_action_label: __("Create Connector"),
		primary_action() {
			confirm_dialog.hide();
			create_connector(data, listview);
		},
		secondary_action_label: __("Cancel"),
	});

	confirm_dialog.fields_dict.results_html.$wrapper.html(html);
	confirm_dialog.show();
}

function create_connector(data, listview) {
	frappe.call({
		method:
			"nce_sync.nce_sync.doctype.api_connector.api_connector.create_connector_from_ai",
		args: { connector_data: data },
		freeze: true,
		freeze_message: __("Creating {0} connector…", [data.connector_name]),
		callback(r) {
			if (r.message) {
				frappe.show_alert(
					{
						message: __("Created {0} with {1} endpoints", [
							r.message.name,
							r.message.endpoint_count,
						]),
						indicator: "green",
					},
					7
				);
				listview.refresh();
				frappe.set_route("Form", "API Connector", r.message.name);
			}
		},
	});
}
