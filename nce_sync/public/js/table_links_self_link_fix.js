// Table Links: replace diagonal "—" cells even if a cached page script still draws them.
// Loaded on every Desk page via app_include_js; no-ops unless the Table Links grid is present.

(function () {
	"use strict";

	let inFlight = false;
	let debounceTimer = null;

	function isTableLinksPage() {
		const path = (window.location && window.location.pathname) || "";
		if (path.indexOf("table-links") !== -1) return true;
		try {
			const route = frappe.get_route ? frappe.get_route() : [];
			return route.indexOf("table-links") !== -1;
		} catch (e) {
			return false;
		}
	}

	function makeButton(dt, label, cellLinks) {
		const count = Array.isArray(cellLinks) ? cellLinks.length : 0;
		const $btn = $('<button type="button" class="btn btn-sm nce-link-btn"></button>');
		$btn.attr("data-source", dt);
		$btn.attr("data-source-label", label);
		$btn.attr("data-target", dt);
		$btn.attr("data-target-label", label);
		if (count === 0) {
			$btn.addClass("btn-default").text(__("Link"));
		} else {
			$btn.addClass("btn-success").text(count > 1 ? "✓ " + count : "✓");
			$btn.attr("data-links", JSON.stringify(cellLinks));
		}
		$btn.on("click", function (e) {
			e.preventDefault();
			e.stopPropagation();
			let links = $(this).data("links") || [];
			if (typeof links === "string") {
				try {
					links = JSON.parse(links);
				} catch (err) {
					links = [];
				}
			}
			const $grid = $(".nce-table-links-grid");
			if (typeof show_link_dialog === "function") {
				show_link_dialog(
					{
						source: dt,
						sourceLabel: label,
						target: dt,
						targetLabel: label,
						links: links,
					},
					$grid,
				);
			}
		});
		return $btn;
	}

	function paint(data) {
		const $table = $(".nce-links-grid");
		if (!$table.length || !data) return;
		const tables = data.tables || [];
		const links = data.links || {};
		$table.find("tbody tr").each(function (rowIdx) {
			const t = tables[rowIdx];
			if (!t) return;
			const $cell = $(this).children("td").eq(rowIdx + 1);
			if (!$cell.length) return;
			const dt = t.doctype;
			const label = t.label;
			const cellLinks = (links[dt] && links[dt][dt]) || [];
			$cell
				.removeClass("nce-grid-diagonal")
				.addClass("nce-grid-cell nce-grid-action")
				.empty()
				.append(makeButton(dt, label, cellLinks));
		});
	}

	function run() {
		if (!isTableLinksPage()) return;
		if (!$(".nce-links-grid").length) return;
		if (inFlight) return;
		inFlight = true;
		frappe.call({
			method: "nce_sync.api.get_table_links_grid_data",
			callback: function (r) {
				inFlight = false;
				if (r.exc || !r.message) return;
				paint(r.message);
			},
			error: function () {
				inFlight = false;
			},
		});
	}

	function schedule() {
		if (!isTableLinksPage()) return;
		clearTimeout(debounceTimer);
		debounceTimer = setTimeout(run, 150);
	}

	$(document).on("page-change", schedule);
	$(function () {
		schedule();
	});
})();
