// List View Auto-Size Columns
// Adds "Resize Columns" button to list header. Resizes only visible columns.
//
// code-index:begin
// {
//  "purpose": "Desk list view hook: auto-size visible columns with a toolbar button on supported DocType list views.",
//  "read_when": "Changing list column auto-width behaviour or the resize button on mirrored table list views.",
//  "gotchas": [],
//  "keywords": ["list view", "auto size columns", "column width", "listview settings", "desk hook", "resize"],
//  "format": 1
// }
// code-index:end

(function () {
	"use strict";

	const MAX_CHARS = 75;
	const MIN_WIDTH_PX = 50;
	const SAMPLE_ROWS = 200;

	const columnWidthCache = {};
	let avgCharWidth = null;

	const DB_CONTENT_SAMPLE =
		"eeeeeetttttaaaaoooiiinnnssshhrrdllccuumwfgyypbvk" +
		"ETAOINSHRD" +
		"012345678901" +
		"               " +
		"-_.,/:@#";

	function calculateAvgCharWidth() {
		if (avgCharWidth !== null) return avgCharWidth;

		const canvas = document.createElement("canvas");
		const ctx = canvas.getContext("2d");

		const listEl = document.querySelector(".frappe-list .list-row");
		let font = "13px Inter, -apple-system, BlinkMacSystemFont, sans-serif";
		if (listEl) {
			const computed = getComputedStyle(listEl);
			font = computed.font || `${computed.fontSize} ${computed.fontFamily}`;
		}

		ctx.font = font;
		const totalWidth = ctx.measureText(DB_CONTENT_SAMPLE).width;
		avgCharWidth = totalWidth / DB_CONTENT_SAMPLE.length;

		return avgCharWidth;
	}

	async function fetchSampleRows(doctype, fields) {
		return new Promise((resolve, reject) => {
			frappe.call({
				method: "frappe.client.get_list",
				args: {
					doctype: doctype,
					fields: fields,
					limit_page_length: SAMPLE_ROWS,
					order_by: "creation desc",
				},
				async: true,
				callback: (r) => resolve(r.message || []),
				error: (r) => reject(r),
			});
		});
	}

	function calculateMaxLengths(rows, fields) {
		const maxLengths = {};
		fields.forEach((field) => {
			maxLengths[field] = 0;
		});

		rows.forEach((row) => {
			fields.forEach((field) => {
				const value = row[field];
				if (value !== null && value !== undefined) {
					const len = String(value).length;
					if (len > maxLengths[field]) {
						maxLengths[field] = len;
					}
				}
			});
		});

		return maxLengths;
	}

	function getButtonColumnWidth(listview) {
		const settings = frappe.listview_settings[listview.doctype];
		if (!settings || !settings.button) return null;

		const btn = settings.button;
		let labels = [];

		if (typeof btn.get_label === "function") {
			const samples = [
				{},
				{ auto_sync_active: 1 },
				{ auto_sync_active: 0 },
				{ mirror_status: "Mirrored" },
			];
			samples.forEach((doc) => {
				try {
					const label = btn.get_label(doc);
					if (label && !labels.includes(label)) labels.push(label);
				} catch (e) {}
			});
		}
		if (labels.length === 0) labels = ["Action"];

		const canvas = document.createElement("canvas");
		const ctx = canvas.getContext("2d");
		const listEl = document.querySelector(".frappe-list .list-row");
		let font = "13px Inter, sans-serif";
		if (listEl) font = getComputedStyle(listEl).font;
		ctx.font = font;

		let maxWidth = 0;
		labels.forEach((l) => {
			maxWidth = Math.max(maxWidth, ctx.measureText(l).width);
		});

		return Math.round(maxWidth + 50);
	}

	const SUBJECT_CHECKBOX_PX = 40;

	function lengthsToWidths(maxLengths, listview) {
		const charWidth = calculateAvgCharWidth();
		const widths = {};

		// First column (Subject) has a checkbox inside - needs extra width
		const columns = listview.columns || [];
		const subjectField =
			columns.length > 0
				? columns[0].fieldname || (columns[0].df && columns[0].df.fieldname)
				: null;

		for (const field in maxLengths) {
			const chars = Math.min(maxLengths[field], MAX_CHARS);
			let px = Math.max(chars * charWidth + 24, MIN_WIDTH_PX);
			if (field === subjectField) {
				px += SUBJECT_CHECKBOX_PX;
			}
			widths[field] = Math.round(px);
		}

		const btnWidth = getButtonColumnWidth(listview);
		if (btnWidth) {
			widths["_button"] = Math.max(btnWidth, MIN_WIDTH_PX);
		}

		return widths;
	}

	function applyWidths(listview, widths) {
		// Ensure frappe-list has data-doctype for scoping
		if (listview.$frappe_list) {
			listview.$frappe_list.attr("data-doctype", listview.doctype);
		}

		const styleId = "auto-size-" + listview.doctype.replace(/\s/g, "-");
		const existingStyle = document.getElementById(styleId);
		if (existingStyle) existingStyle.remove();

		let css = "";
		const columns = listview.columns || [];
		const scope = listview.$frappe_list?.length
			? `.frappe-list[data-doctype="${listview.doctype}"]`
			: ".frappe-list";

		// Calculate total px to derive percentages
		let totalPx = 0;
		const colWidths = [];
		columns.forEach((col) => {
			const fieldname = col.fieldname || (col.df && col.df.fieldname);
			const px = widths[fieldname] || MIN_WIDTH_PX;
			colWidths.push(px);
			totalPx += px;
		});

		// Enable horizontal scroll when min-widths exceed viewport
		css += `
			${scope} .result {
				overflow-x: auto !important;
			}
			${scope} .list-row-head .list-header-subject,
			${scope} .list-row .level-left {
				flex-wrap: nowrap !important;
			}
		`;

		// Hybrid: percentage for proportional sizing + min-width in px
		columns.forEach((col, idx) => {
			const pct = ((colWidths[idx] / totalPx) * 100).toFixed(2);
			const minPx = colWidths[idx];
			const nth = idx + 1;
			css += `
				${scope} .list-row-head .list-header-subject > div:nth-child(${nth}),
				${scope} .list-row .level-left > .list-row-col:nth-child(${nth}) {
					flex: 0 0 ${pct}% !important;
					min-width: ${minPx}px !important;
					overflow: hidden;
					text-overflow: ellipsis;
					white-space: nowrap;
				}
			`;
		});

		if (css) {
			const style = document.createElement("style");
			style.id = styleId;
			style.textContent = css;
			document.head.appendChild(style);
		}
	}

	async function autoSizeColumns(listview, silent) {
		const doctype = listview.doctype;

		// Use cached widths if available (no re-fetch within same session)
		if (columnWidthCache[doctype]) {
			applyWidths(listview, columnWidthCache[doctype]);
			return;
		}

		// Get visible columns only
		const columns = listview.columns || [];
		const fields = [];
		columns.forEach((col) => {
			const fieldname = col.fieldname || (col.df && col.df.fieldname);
			if (fieldname && fieldname !== "name") {
				fields.push(fieldname);
			}
		});

		if (fields.length === 0) return;

		fields.unshift("name");

		try {
			const rows = await fetchSampleRows(doctype, fields);
			if (rows.length === 0) return;

			const maxLengths = calculateMaxLengths(rows, fields);
			const widths = lengthsToWidths(maxLengths, listview);

			columnWidthCache[doctype] = widths;
			applyWidths(listview, widths);
		} catch (err) {
			console.error("[AutoSize]", err);
		}
	}

	async function manualResize(listview) {
		// Force recalculate (clear cache for this doctype)
		delete columnWidthCache[listview.doctype];
		await autoSizeColumns(listview);
		frappe.show_alert({
			message: __("Columns resized"),
			indicator: "green",
		});
	}

	function sendAllToExcel(listview) {
		var doctype = listview.doctype;
		var alertEl = null;

		var handler = function (data) {
			frappe.realtime.off("excel_export_ready", handler);
			if (alertEl) {
				alertEl.remove();
				alertEl = null;
			}
			if (data && data.file_url) {
				var a = document.createElement("a");
				a.href = data.file_url;
				a.download = "";
				document.body.appendChild(a);
				a.click();
				document.body.removeChild(a);
			}
		};
		frappe.realtime.on("excel_export_ready", handler);

		frappe.call({
			method: "nce_sync.api.export_all_to_excel",
			args: { doctype: doctype },
			callback: function (r) {
				var total = r.message || 0;
				alertEl = frappe.show_alert({
					message: __("Exporting {0} records…", [total]),
					indicator: "blue",
				}, 300);
			},
			error: function () {
				frappe.realtime.off("excel_export_ready", handler);
				frappe.show_alert({
					message: __("Export failed"),
					indicator: "red",
				});
			},
		});
	}

	function showFamilyGpsResultAlert(result) {
		var processed = result && result.processed;
		if (!processed) {
			frappe.show_alert(
				{
					message: __("No families still need GPS."),
					indicator: "green",
				},
				8
			);
			return;
		}
		var filled = (result.created || 0) + (result.updated || 0);
		var failed = result.geocode_failed || 0;
		if (failed > 0) {
			frappe.show_alert(
				{
					message: __(
						"GPS filled for {0} families. {1} could not be geocoded.",
						[filled, failed]
					),
					indicator: "orange",
				},
				8
			);
		} else {
			frappe.show_alert(
				{
					message: __("GPS filled for {0} families.", [filled]),
					indicator: "green",
				},
				8
			);
		}
	}

	function populateFamilyGps(allRows) {
		var batchId = Math.random().toString(36).slice(2) + Date.now().toString(36);
		var alertEl = null;
		var handler = function (data) {
			if (!data || data.batch_id !== batchId) {
				return;
			}
			frappe.realtime.off("family_gps_populate_done", handler);
			if (alertEl) {
				alertEl.remove();
				alertEl = null;
			}
			showFamilyGpsResultAlert(data);
		};
		frappe.realtime.on("family_gps_populate_done", handler);

		alertEl = frappe.show_alert(
			{
				message: allRows
					? __("Populating GPS for every family that still needs it…")
					: __("Populating GPS for up to 50 families…"),
				indicator: "blue",
			},
			3600
		);

		frappe.call({
			method: "nce_sync.utils.family_gps.populate_family_gps",
			args: { all_rows: allRows ? 1 : 0, batch_id: batchId },
			error: function () {
				frappe.realtime.off("family_gps_populate_done", handler);
				if (alertEl) {
					alertEl.remove();
					alertEl = null;
				}
			},
		});
	}

	function addResizeButton(listview) {
		if (!listview || listview._resizeBtnAdded) return;
		if (!listview.page) return;

		const page = listview.page;
		if (!page.add_inner_button && !page.add_action_item) return;

		listview._resizeBtnAdded = true;

		// Auto-resize silently on load
		setTimeout(() => autoSizeColumns(listview), 200);

		const resizeHandler = () => manualResize(listview);
		const excelHandler = () => sendAllToExcel(listview);

		if (page.add_inner_button) {
			page.add_inner_button(__("Resize Columns"), resizeHandler);
			page.add_inner_button(__("Send All to Excel"), excelHandler);
			if (listview.doctype === "Families") {
				var gpsNormalHandler = function () {
					populateFamilyGps(false);
				};
				var $gpsBtn = page.add_inner_button(__("Populate GPS"), gpsNormalHandler);
				if ($gpsBtn && $gpsBtn.length) {
					$gpsBtn.off("click").on("click", function (e) {
						e.preventDefault();
						populateFamilyGps(!!e.shiftKey);
					});
				}
			}
		} else {
			page.add_action_item(__("Resize Columns"), resizeHandler);
			page.add_action_item(__("Send All to Excel"), excelHandler);
		}
	}

	function tryAddButton() {
		const listview = typeof cur_list !== "undefined" ? cur_list : null;
		if (listview && listview.doctype && !listview._resizeBtnAdded) {
			addResizeButton(listview);
		}
	}

	function init() {
		// Hook 1: When route changes, try to add button (for doctypes without listview_settings)
		if (frappe.router && frappe.router.on) {
			frappe.router.on("change", () => setTimeout(tryAddButton, 500));
		}

		// Hook 2: ListView prototype (if exists)
		if (frappe.views && frappe.views.ListView) {
			const ListView = frappe.views.ListView;
			["refresh", "make"].forEach((method) => {
				if (ListView.prototype[method]) {
					const orig = ListView.prototype[method];
					ListView.prototype[method] = function () {
						const result = orig.apply(this, arguments);
						addResizeButton(this);
						return result;
					};
				}
			});
		}

		// Hook 3: Wrap listview_settings so our onload runs for every doctype
		const origSettings = frappe.listview_settings || {};
		frappe.listview_settings = new Proxy(origSettings, {
			get(target, doctype) {
				const settings = target[doctype];
				if (!settings) return undefined;
				const origOnload = settings.onload;
				return {
					...settings,
					onload(listview) {
						if (origOnload) origOnload(listview);
						addResizeButton(listview);
					},
				};
			},
			set(target, doctype, value) {
				target[doctype] = value;
				return true;
			},
		});

		// Initial load
		setTimeout(tryAddButton, 1000);
	}

	// Expose for doctype-specific list scripts (e.g. wp_tables_list.js)
	window.__autoSizeColumns = manualResize;

	if (document.readyState === "loading") {
		document.addEventListener("DOMContentLoaded", init);
	} else {
		init();
	}
})();
