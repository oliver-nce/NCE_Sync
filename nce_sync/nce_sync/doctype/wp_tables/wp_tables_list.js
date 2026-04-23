// Copyright (c) 2026, Oliver Reid and contributors
// For license information, please see license.txt

frappe.listview_settings["WP Tables"] = {
	add_fields: ["auto_sync_active", "mirror_status", "last_sync_status"],

	get_indicator: function (doc) {
		if (doc.mirror_status === "Error") {
			return [__("Error"), "red", "mirror_status,=,Error"];
		}
		if (doc.mirror_status === "Pending") {
			return [__("Pending"), "orange", "mirror_status,=,Pending"];
		}
		if (doc.last_sync_status === "Running") {
			return [__("Syncing"), "blue", "last_sync_status,=,Running"];
		}
		if (doc.last_sync_status === "Warning") {
			return [__("Sync warning"), "orange", "last_sync_status,=,Warning"];
		}
		if (doc.last_sync_status === "Error") {
			return [__("Sync Error"), "red", "last_sync_status,=,Error"];
		}
		if (doc.last_sync_status === "Success") {
			return [__("Synced"), "green", "last_sync_status,=,Success"];
		}
		// Mirrored but never synced
		return [__("Mirrored"), "blue", "mirror_status,=,Mirrored"];
	},

	button: {
		show: function (doc) {
			// Show toggle button for all mirrored tables
			return doc.mirror_status === "Mirrored";
		},
		get_label: function (doc) {
			return doc.auto_sync_active ? __("Disable Sync") : __("Enable Sync");
		},
		get_description: function (doc) {
			return doc.auto_sync_active
				? __("Disable automatic sync for this table")
				: __("Enable automatic sync for this table");
		},
		action: function (doc) {
			frappe.call({
				method: "nce_sync.api.toggle_auto_sync",
				args: {
					table_names: [doc.name],
				},
				callback: function (r) {
					if (r.message) {
						frappe.show_alert({
							message: r.message,
							indicator: "green",
						});
						cur_list.refresh();
					}
				},
			});
		},
	},

	onload: function (listview) {
		// Add "Resize Columns" button (from list_auto_size.js)
		if (listview.page.add_inner_button) {
			listview.page.add_inner_button(__("Resize Columns"), function () {
				window.__autoSizeColumns && window.__autoSizeColumns(listview);
			});
		}

		// Add "Toggle Auto Sync" button for bulk operations
		listview.page.add_action_item(__("Toggle Auto Sync"), function () {
			const selected = listview.get_checked_items();
			if (selected.length === 0) {
				frappe.msgprint(__("Please select at least one table"));
				return;
			}

			const table_names = selected.map((d) => d.name);

			frappe.call({
				method: "nce_sync.api.toggle_auto_sync",
				args: {
					table_names: table_names,
				},
				callback: function (r) {
					if (r.message) {
						frappe.show_alert({
							message: r.message,
							indicator: "green",
						});
						listview.refresh();
					}
				},
			});
		});
	},
};
