// Copyright (c) 2026, Oliver Reid and contributors
// For license information, please see license.txt

frappe.pages["table-links"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Table Links"),
		single_column: true,
	});

	page.main.addClass("nce-table-links-page");

	page.main.append(
		"<style>" +
			".nce-grid-cell{text-align:center;vertical-align:middle}" +
			".nce-grid-row-label{background:#fafafa}" +
			".nce-links-grid td,.nce-links-grid th{padding:8px 12px}" +
			".nce-tab-bar{border-bottom:1px solid #d1d8dd;margin-bottom:15px}" +
			".nce-tab-bar .btn{border:none;border-bottom:2px solid transparent;border-radius:0;margin-right:10px;padding:8px 16px;font-weight:500;color:#8d99a6}" +
			".nce-tab-bar .btn.active{color:#171717;border-bottom-color:var(--primary)}" +
			".nce-erd-wrapper{padding:1rem;text-align:center;min-height:200px}" +
			".nce-erd-wrapper svg{max-width:100%}" +
			"</style>",
	);

	// Tab bar
	const $tabBar = $('<div class="nce-tab-bar"></div>');
	const $defineBtn = $('<button class="btn active">' + __("Define") + "</button>");
	const $vizBtn = $('<button class="btn">' + __("Visualize") + "</button>");
	$tabBar.append($defineBtn).append($vizBtn);
	page.main.append($tabBar);

	// Tab content
	const $definePane = $('<div class="nce-tab-define"></div>');
	const $vizPane = $('<div class="nce-tab-visualize" style="display:none;"></div>');
	page.main.append($definePane).append($vizPane);

	// Grid container inside Define tab
	const $grid = $('<div class="nce-table-links-grid"></div>');
	$definePane.append($grid);

	// ERD container inside Visualize tab
	const $erd = $('<div class="nce-erd-wrapper"></div>');
	$vizPane.append($erd);

	// Tab switching
	let lastData = null;

	$defineBtn.on("click", function () {
		$defineBtn.addClass("active");
		$vizBtn.removeClass("active");
		$definePane.show();
		$vizPane.hide();
	});

	$vizBtn.on("click", function () {
		$vizBtn.addClass("active");
		$defineBtn.removeClass("active");
		$vizPane.show();
		$definePane.hide();
		if (lastData) {
			render_erd($erd, lastData);
		}
	});

	page.set_primary_action(__("Refresh"), function () {
		load_data(function (data) {
			lastData = data;
			render_grid($grid, data);
			if ($vizPane.is(":visible")) {
				render_erd($erd, data);
			}
		});
	});

	// Initial load
	load_data(function (data) {
		lastData = data;
		render_grid($grid, data);
	});
};

// ─── Data Loading ───────────────────────────────────────────────────────────

function load_data(callback) {
	frappe.call({
		method: "nce_sync.api.get_table_links_grid_data",
		callback: function (r) {
			if (r.exc) {
				frappe.show_alert({ message: __("Failed to load grid data"), indicator: "red" });
				return;
			}
			callback(r.message);
		},
	});
}

// ─── Define Tab: Grid ───────────────────────────────────────────────────────

function load_and_render_grid($container) {
	$container.html(
		'<div class="text-muted" style="padding: 2rem; text-align: center;">' +
			__("Loading...") +
			"</div>",
	);

	load_data(function (data) {
		render_grid($container, data);
	});
}

function render_grid($container, data) {
	const tables = data.tables || [];
	const links = data.links || {};

	if (tables.length === 0) {
		$container.html(
			'<div class="text-muted" style="padding: 2rem; text-align: center;">' +
				__(
					"No tables yet. Mirror a WP table or link a native DocType from WP Tables first.",
				) +
				"</div>",
		);
		return;
	}

	let html = '<div class="nce-links-grid-wrapper" style="overflow-x: auto;">';
	html += '<table class="table table-bordered nce-links-grid">';
	html += "<thead><tr>";
	html += '<th style="min-width: 120px;">' + __("Source \\ Target") + "</th>";
	tables.forEach(function (t) {
		html += '<th style="min-width: 100px;">' + frappe.utils.escape_html(t.label) + "</th>";
	});
	html += "</tr></thead><tbody>";

	tables.forEach(function (sourceRow) {
		html += "<tr>";
		html +=
			'<td class="nce-grid-row-label"><strong>' +
			frappe.utils.escape_html(sourceRow.label) +
			"</strong></td>";

		tables.forEach(function (targetCol) {
			const sourceDt = sourceRow.doctype;
			const targetDt = targetCol.doctype;

			const cellLinks = (links[sourceDt] && links[sourceDt][targetDt]) || [];
			const count = cellLinks.length;

			if (count === 0) {
				html +=
					'<td class="nce-grid-cell nce-grid-action">' +
					'<button class="btn btn-sm btn-default nce-link-btn" ' +
					'data-source="' +
					frappe.utils.escape_html(sourceDt) +
					'" data-source-label="' +
					frappe.utils.escape_html(sourceRow.label) +
					'" data-target="' +
					frappe.utils.escape_html(targetDt) +
					'" data-target-label="' +
					frappe.utils.escape_html(targetCol.label) +
					'">' +
					__("Link") +
					"</button></td>";
			} else {
				const display = count > 1 ? "✓ " + count : "✓";
				html +=
					'<td class="nce-grid-cell nce-grid-linked">' +
					'<button class="btn btn-sm btn-success nce-link-btn" ' +
					'data-source="' +
					frappe.utils.escape_html(sourceDt) +
					'" data-source-label="' +
					frappe.utils.escape_html(sourceRow.label) +
					'" data-target="' +
					frappe.utils.escape_html(targetDt) +
					'" data-target-label="' +
					frappe.utils.escape_html(targetCol.label) +
					'" data-links="' +
					frappe.utils.escape_html(JSON.stringify(cellLinks)) +
					'">' +
					frappe.utils.escape_html(display) +
					"</button></td>";
			}
		});
		html += "</tr>";
	});

	html += "</tbody></table></div>";
	$container.html(html);

	$container.find(".nce-link-btn").on("click", function () {
		const $btn = $(this);
		show_link_dialog(
			{
				source: $btn.data("source"),
				sourceLabel: $btn.data("source-label"),
				target: $btn.data("target"),
				targetLabel: $btn.data("target-label"),
				links: $btn.data("links") || [],
			},
			$container,
		);
	});
}

// ─── Visualize Tab: Mermaid ERD ─────────────────────────────────────────────

let mermaidLoaded = false;
let mermaidLoading = false;
const MERMAID_CDN = "https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js";

function load_mermaid(callback) {
	if (mermaidLoaded) {
		callback();
		return;
	}
	if (mermaidLoading) {
		const check = setInterval(function () {
			if (mermaidLoaded) {
				clearInterval(check);
				callback();
			}
		}, 100);
		return;
	}
	mermaidLoading = true;
	const script = document.createElement("script");
	script.src = MERMAID_CDN;
	script.onload = function () {
		window.mermaid.initialize({
			startOnLoad: false,
			theme: "default",
			securityLevel: "loose",
		});
		mermaidLoaded = true;
		mermaidLoading = false;
		callback();
	};
	script.onerror = function () {
		mermaidLoading = false;
		callback(true);
	};
	document.head.appendChild(script);
}

function build_mermaid_erd(data) {
	const tables = data.tables || [];
	const links = data.links || {};
	const lines = ["erDiagram"];

	// Collect unique relationships (deduplicate the bidirectional entries)
	const seen = new Set();

	tables.forEach(function (t) {
		const src = t.doctype;
		const srcLinks = links[src] || {};
		Object.keys(srcLinks).forEach(function (tgt) {
			srcLinks[tgt].forEach(function (l) {
				const manyDt = l.many_doctype;
				const oneDt = manyDt === src ? tgt : src;
				const key = manyDt + "|" + l.field + "|" + oneDt;
				if (seen.has(key)) return;
				seen.add(key);

				const manyName = safe_mermaid_name(find_label(tables, manyDt));
				const oneName = safe_mermaid_name(find_label(tables, oneDt));
				lines.push("    " + oneName + " ||--o{ " + manyName + ' : "' + l.field + '"');
			});
		});
	});

	// Add orphan tables (no links)
	if (lines.length === 1) {
		tables.forEach(function (t) {
			lines.push("    " + safe_mermaid_name(t.label) + " { }");
		});
	}

	return lines.join("\n");
}

function safe_mermaid_name(name) {
	return name.replace(/[^a-zA-Z0-9]/g, "_");
}

function find_label(tables, doctype) {
	for (let i = 0; i < tables.length; i++) {
		if (tables[i].doctype === doctype) return tables[i].label;
	}
	return doctype;
}

function render_erd($container, data) {
	$container.html(
		'<div class="text-muted" style="padding: 2rem;">' + __("Loading diagram...") + "</div>",
	);

	load_mermaid(function (err) {
		if (err) {
			$container.html(
				'<div class="text-muted" style="padding: 2rem;">' +
					__("Could not load Mermaid library. Check your internet connection.") +
					"</div>",
			);
			return;
		}

		const spec = build_mermaid_erd(data);

		if (spec.trim() === "erDiagram") {
			$container.html(
				'<div class="text-muted" style="padding: 2rem;">' +
					__("No relationships defined yet. Use the Define tab to create links.") +
					"</div>",
			);
			return;
		}

		const id = "nce-erd-" + Date.now();
		$container.html('<div class="mermaid" id="' + id + '"></div>');

		try {
			window.mermaid
				.render(id + "-svg", spec)
				.then(function (result) {
					$container.html(
						'<div style="overflow-x: auto; padding: 1rem;">' + result.svg + "</div>",
					);
				})
				.catch(function (e) {
					$container.html(
						'<div class="text-danger" style="padding: 2rem;">' +
							__("Mermaid render error: {0}", [e.message || e]) +
							"<pre style='text-align:left;font-size:12px;margin-top:10px;'>" +
							frappe.utils.escape_html(spec) +
							"</pre></div>",
					);
				});
		} catch (e) {
			$container.html(
				'<div class="text-danger" style="padding: 2rem;">' +
					__("Failed to render diagram: {0}", [e.message || e]) +
					"<pre style='text-align:left;font-size:12px;margin-top:10px;'>" +
					frappe.utils.escape_html(spec) +
					"</pre></div>",
			);
		}
	});
}

// ─── Link Dialog ────────────────────────────────────────────────────────────

function show_link_dialog(opts, $gridContainer) {
	const esc = frappe.utils.escape_html;
	const source = opts.source;
	const target = opts.target;
	const sourceLabel = opts.sourceLabel;
	const targetLabel = opts.targetLabel;

	const serverLinks = Array.isArray(opts.links) ? opts.links.slice() : [];

	// Pending changeset
	const toAdd = []; // { many_doctype, field_name, label }
	const toDelete = []; // { many_doctype, field_name }

	const d = new frappe.ui.Dialog({
		title: __("{0} ↔ {1}", [sourceLabel, targetLabel]),
		size: "large",
		fields: [{ fieldtype: "HTML", fieldname: "body" }],
		primary_action_label: __("Done"),
		primary_action: function () {
			apply_changes();
		},
		secondary_action_label: __("Revert"),
		secondary_action: function () {
			toAdd.length = 0;
			toDelete.length = 0;
			render_body();
			frappe.show_alert({ message: __("Reverted all pending changes"), indicator: "blue" });
		},
	});

	const $body = d.fields_dict.body.$wrapper;

	function render_body() {
		let html = "";

		// --- Existing links ---
		html += '<div class="nce-dlg-section">';
		html += '<div class="text-muted small mb-2">' + __("Existing links") + "</div>";

		const visibleServer = serverLinks.filter(
			(l) =>
				!toDelete.find(
					(x) => x.many_doctype === l.many_doctype && x.field_name === l.field,
				),
		);
		const markedForDelete = serverLinks.filter((l) =>
			toDelete.find((x) => x.many_doctype === l.many_doctype && x.field_name === l.field),
		);

		if (visibleServer.length === 0 && markedForDelete.length === 0 && toAdd.length === 0) {
			html += '<p class="text-muted">' + __("No links between these tables.") + "</p>";
		}

		visibleServer.forEach(function (l) {
			const manyLabel = l.many_doctype === source ? sourceLabel : targetLabel;
			const oneLabel = l.many_doctype === source ? targetLabel : sourceLabel;
			html +=
				'<div class="nce-link-row d-flex align-items-center mb-1" style="padding: 6px 8px; background: #f8f9fa; border-radius: 4px;">' +
				'<span class="flex-grow-1">' +
				"<strong>" +
				esc(manyLabel) +
				"." +
				esc(l.field) +
				"</strong>" +
				' <span class="text-muted">→</span> ' +
				esc(oneLabel) +
				' <span class="text-muted small">(' +
				__("many → one") +
				")</span>" +
				"</span>" +
				'<button class="btn btn-xs btn-danger nce-dlg-delete" ' +
				'data-many="' +
				esc(l.many_doctype) +
				'" data-field="' +
				esc(l.field) +
				'"' +
				' title="' +
				__("Mark for deletion") +
				'">✕</button>' +
				"</div>";
		});

		markedForDelete.forEach(function (l) {
			const manyLabel = l.many_doctype === source ? sourceLabel : targetLabel;
			const oneLabel = l.many_doctype === source ? targetLabel : sourceLabel;
			html +=
				'<div class="nce-link-row d-flex align-items-center mb-1" style="padding: 6px 8px; background: #fff3f3; border-radius: 4px; text-decoration: line-through; opacity: 0.6;">' +
				'<span class="flex-grow-1">' +
				"<strong>" +
				esc(manyLabel) +
				"." +
				esc(l.field) +
				"</strong>" +
				' <span class="text-muted">→</span> ' +
				esc(oneLabel) +
				"</span>" +
				'<button class="btn btn-xs btn-default nce-dlg-undo-delete" ' +
				'data-many="' +
				esc(l.many_doctype) +
				'" data-field="' +
				esc(l.field) +
				'"' +
				' title="' +
				__("Undo delete") +
				'">↩</button>' +
				"</div>";
		});

		// --- Pending additions ---
		toAdd.forEach(function (a, idx) {
			const manyLabel = a.many_doctype === source ? sourceLabel : targetLabel;
			const oneLabel = a.many_doctype === source ? targetLabel : sourceLabel;
			html +=
				'<div class="nce-link-row d-flex align-items-center mb-1" style="padding: 6px 8px; background: #eafbea; border-radius: 4px;">' +
				'<span class="flex-grow-1">' +
				"<strong>" +
				esc(manyLabel) +
				"." +
				esc(a.field_name) +
				"</strong>" +
				' <span class="text-muted">→</span> ' +
				esc(oneLabel) +
				' <span class="text-muted small">(' +
				__("pending") +
				")</span>" +
				"</span>" +
				'<button class="btn btn-xs btn-danger nce-dlg-remove-add" data-idx="' +
				idx +
				'"' +
				' title="' +
				__("Remove") +
				'">✕</button>' +
				"</div>";
		});

		html += "</div>";

		// --- Add new link form ---
		html += "<hr>";
		html += '<div class="nce-dlg-add-section">';
		html += '<div class="text-muted small mb-2">' + __("Add new link") + "</div>";
		html += '<div class="row">';
		html += '<div class="col-sm-5">';
		html +=
			'<label class="control-label">' + __("Many side (gets the Link field)") + "</label>";
		html += '<select class="form-control form-control-sm nce-dlg-many-select">';
		html += '<option value="' + esc(source) + '">' + esc(sourceLabel) + "</option>";
		if (source !== target) {
			html += '<option value="' + esc(target) + '">' + esc(targetLabel) + "</option>";
		}
		html += "</select>";
		html += "</div>";
		html += '<div class="col-sm-5">';
		html += '<label class="control-label">' + __("Field name") + "</label>";
		html += '<div class="input-group">';
		html +=
			'<input type="text" class="form-control form-control-sm nce-dlg-field-input" placeholder="e.g. venue_id">';
		html +=
			'<span class="input-group-btn"><button class="btn btn-sm btn-primary nce-dlg-add-btn">+ ' +
			__("Add") +
			"</button></span>";
		html += "</div>";
		html += "</div>";
		html += "</div>";
		html +=
			'<div class="nce-dlg-field-list-wrapper mt-2" style="max-height: 180px; overflow-y: auto; border: 1px solid #d1d8dd; border-radius: 4px; display: none;">';
		html += '<div class="nce-dlg-field-list"></div>';
		html += "</div>";
		html +=
			'<div class="text-muted small mt-1 nce-dlg-field-list-hint" style="display:none;">' +
			__("Click a field to use its name") +
			"</div>";
		html += "</div>";

		$body.html(html);

		// Bind events
		$body.find(".nce-dlg-delete").on("click", function () {
			toDelete.push({
				many_doctype: $(this).data("many"),
				field_name: $(this).data("field"),
			});
			render_body();
		});

		$body.find(".nce-dlg-undo-delete").on("click", function () {
			const many = $(this).data("many");
			const field = $(this).data("field");
			const idx = toDelete.findIndex(
				(x) => x.many_doctype === many && x.field_name === field,
			);
			if (idx >= 0) toDelete.splice(idx, 1);
			render_body();
		});

		$body.find(".nce-dlg-remove-add").on("click", function () {
			toAdd.splice($(this).data("idx"), 1);
			render_body();
		});

		$body.find(".nce-dlg-add-btn").on("click", function () {
			const manyDt = $body.find(".nce-dlg-many-select").val();
			const fieldName = $body
				.find(".nce-dlg-field-input")
				.val()
				.trim()
				.toLowerCase()
				.replace(/\s+/g, "_");

			if (!fieldName) {
				frappe.show_alert({ message: __("Enter a field name"), indicator: "orange" });
				return;
			}
			if (!/^[a-z][a-z0-9_]*$/.test(fieldName)) {
				frappe.show_alert({
					message: __(
						"Field name must start with a letter and contain only lowercase letters, numbers, underscores",
					),
					indicator: "orange",
				});
				return;
			}
			if (toAdd.find((a) => a.many_doctype === manyDt && a.field_name === fieldName)) {
				frappe.show_alert({ message: __("Already pending"), indicator: "orange" });
				return;
			}
			if (serverLinks.find((l) => l.many_doctype === manyDt && l.field === fieldName)) {
				frappe.show_alert({ message: __("Field already exists"), indicator: "orange" });
				return;
			}

			const oneDt = manyDt === source ? target : source;
			toAdd.push({ many_doctype: manyDt, one_doctype: oneDt, field_name: fieldName });
			$body.find(".nce-dlg-field-input").val("");
			render_body();
		});

		$body
			.find(".nce-dlg-many-select")
			.on("change", function () {
				populate_field_list();
			})
			.trigger("change");
	}

	const fieldCache = {};

	function populate_field_list() {
		const manyDt = $body.find(".nce-dlg-many-select").val();
		const $wrapper = $body.find(".nce-dlg-field-list-wrapper");
		const $list = $body.find(".nce-dlg-field-list");
		const $hint = $body.find(".nce-dlg-field-list-hint");
		const $input = $body.find(".nce-dlg-field-input");
		const oneDt = manyDt === source ? target : source;
		$input.attr("placeholder", oneDt.toLowerCase().replace(/\s+/g, "_"));

		if (fieldCache[manyDt]) {
			render_field_list(fieldCache[manyDt], manyDt);
			return;
		}

		$list.html('<div class="text-muted small p-2">' + __("Loading fields...") + "</div>");
		$wrapper.show();
		$hint.hide();

		frappe.model.with_doctype(manyDt, function () {
			const meta = frappe.get_meta(manyDt);
			const skipTypes = new Set([
				"Section Break",
				"Column Break",
				"Tab Break",
				"HTML",
				"Table",
				"Table MultiSelect",
			]);
			const skipNames = new Set([
				"name",
				"owner",
				"creation",
				"modified",
				"modified_by",
				"docstatus",
				"idx",
				"amended_from",
			]);
			const fields = (meta.fields || []).filter(function (f) {
				return (
					!skipTypes.has(f.fieldtype) &&
					!skipNames.has(f.fieldname) &&
					!f.fieldname.startsWith("_")
				);
			});
			fieldCache[manyDt] = fields;
			render_field_list(fields, manyDt);
		});
	}

	function render_field_list(fields, manyDt) {
		const $wrapper = $body.find(".nce-dlg-field-list-wrapper");
		const $list = $body.find(".nce-dlg-field-list");
		const $hint = $body.find(".nce-dlg-field-list-hint");

		if (!fields.length) {
			$wrapper.hide();
			$hint.hide();
			return;
		}

		let html = '<table class="table table-sm table-hover mb-0" style="font-size: 12px;">';
		html +=
			"<thead><tr><th>" +
			__("Field") +
			"</th><th>" +
			__("Type") +
			"</th><th>" +
			__("Label") +
			"</th></tr></thead><tbody>";
		fields.forEach(function (f) {
			const isLink = f.fieldtype === "Link";
			const rowClass = isLink ? ' class="text-muted"' : ' style="cursor:pointer;"';
			html +=
				"<tr" +
				rowClass +
				' data-fieldname="' +
				esc(f.fieldname) +
				'" data-is-link="' +
				(isLink ? "1" : "0") +
				'">';
			html += "<td><code>" + esc(f.fieldname) + "</code></td>";
			html +=
				"<td>" + esc(f.fieldtype) + (isLink ? " → " + esc(f.options || "") : "") + "</td>";
			html += "<td>" + esc(f.label || "") + "</td>";
			html += "</tr>";
		});
		html += "</tbody></table>";

		$list.html(html);
		$wrapper.show();
		$hint.show();

		$list.find("tr[data-fieldname]").on("click", function () {
			if ($(this).data("is-link") === 1) return;
			$body.find(".nce-dlg-field-input").val($(this).data("fieldname"));
		});
	}

	function apply_changes() {
		if (toAdd.length === 0 && toDelete.length === 0) {
			d.hide();
			return;
		}

		const deleteItems = toDelete.map(function (x) {
			return { many_doctype: x.many_doctype, field_name: x.field_name };
		});

		const affectedDoctypes = new Set();
		toAdd.forEach(function (a) {
			affectedDoctypes.add(a.many_doctype);
		});
		toDelete.forEach(function (x) {
			affectedDoctypes.add(x.many_doctype);
		});

		frappe.call({
			method: "nce_sync.api.apply_table_link_changes",
			args: {
				to_add: JSON.stringify(toAdd),
				to_delete: JSON.stringify(deleteItems),
			},
			freeze: true,
			freeze_message: __("Applying changes..."),
			callback: function (r) {
				if (r.exc) {
					frappe.show_alert({ message: __("Error applying changes"), indicator: "red" });
					return;
				}
				// Clear client-side meta cache for affected DocTypes
				affectedDoctypes.forEach(function (dt) {
					if (frappe.model && frappe.model.docinfo) {
						delete frappe.model.docinfo[dt];
					}
					if (frappe.boot && frappe.boot.docs) {
						frappe.boot.docs = frappe.boot.docs.filter(function (d) {
							return !(d.doctype === "DocType" && d.name === dt);
						});
					}
					delete locals["DocType"][dt];
					delete fieldCache[dt];
				});

				frappe.show_alert({ message: r.message || __("Done"), indicator: "green" });
				d.hide();
				if ($gridContainer) {
					load_and_render_grid($gridContainer);
				}
			},
		});
	}

	// Enrich serverLinks with many_doctype so we know which side owns the field
	serverLinks.forEach(function (l) {
		if (!l.many_doctype) {
			l.many_doctype = l.owner_doctype || source;
		}
	});

	render_body();
	d.show();
}
