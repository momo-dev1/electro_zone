// Copyright (c) 2026, Electro Zone and contributors
// For license information, please see license.txt

frappe.listview_settings["Platform Order"] = {
	onload: function (listview) {
		// Add bulk action button
		listview.page.add_actions_menu_item(__("Bulk Update Status"), function () {
			let selected = listview.get_checked_items();

			if (selected.length === 0) {
				frappe.msgprint(__("Please select at least one Platform Order"));
				return;
			}

			let d = new frappe.ui.Dialog({
				title: __("Bulk Update Delivery Status"),
				fields: [
					{
						fieldname: "info",
						fieldtype: "HTML",
						options: `<div class="alert alert-info">
                            <p class="mb-0">${__("Selected {0} Platform Orders", [
								selected.length,
							])}</p>
                        </div>`,
					},
					{
						fieldname: "new_status",
						fieldtype: "Select",
						label: __("New Status"),
						options: "Delivered\nCanceled\nDelivery Failed\nReturned",
						reqd: 1,
						description: __(
							"Note: Use individual form buttons for Ready to Ship and Shipped statuses"
						),
					},
				],
				primary_action_label: __("Update"),
				primary_action: function (values) {
					frappe.call({
						method: "electro_zone.electro_zone.doctype.platform_order.platform_order.bulk_update_status",
						args: {
							platform_orders: selected.map((item) => item.name),
							new_status: values.new_status,
						},
						freeze: true,
						freeze_message: __("Updating status..."),
						callback: function (r) {
							if (r.message) {
								let message = __("Updated: {0}, Failed: {1}", [
									r.message.updated,
									r.message.failed,
								]);

								// Show failed items if any
								if (r.message.failed > 0 && r.message.details.failed.length > 0) {
									let failed_html = "<br><br><b>Failed items:</b><ul>";
									r.message.details.failed.forEach((item) => {
										failed_html += `<li>${item.name}: ${item.error}</li>`;
									});
									failed_html += "</ul>";
									message += failed_html;
								}

								frappe.msgprint({
									title: __("Bulk Update Complete"),
									message: message,
									indicator: r.message.failed > 0 ? "yellow" : "green",
								});

								listview.refresh();
								d.hide();
							}
						},
					});
				},
			});

			d.show();
		});

		// Add Bulk Import from Excel button
		listview.page.add_inner_button(__("Bulk Import from Excel"), function () {
			show_bulk_import_dialog(listview);
		});
	},

	// Custom indicator colors based on delivery status
	get_indicator: function (doc) {
		const status_colors = {
			Pending: "orange",
			"Ready to Ship": "blue",
			Shipped: "purple",
			Delivered: "green",
			Canceled: "red",
			"Delivery Failed": "red",
			Returned: "yellow",
		};

		// Priority 1: Show match_status if not fully matched
		if (doc.match_status && doc.match_status !== "Fully Matched") {
			return [
				__(doc.match_status),
				doc.match_status === "Unmatched" ? "red" : "orange",
				"match_status,=," + doc.match_status,
			];
		}

		// Priority 2: Show stock_status if insufficient
		if (doc.stock_status && doc.stock_status !== "Stock Available") {
			return [
				__(doc.stock_status),
				doc.stock_status === "No Stock" ? "red" : "orange",
				"stock_status,=," + doc.stock_status,
			];
		}

		// Priority 3: Show delivery_status
		return [
			__(doc.delivery_status),
			status_colors[doc.delivery_status] || "gray",
			"delivery_status,=," + doc.delivery_status,
		];
	},

	// Add custom buttons to each row
	formatters: {
		order_number: function (value, field, doc) {
			// Make order number bold and clickable
			return `${value}`;
		},
	},
};

// Bulk Import Functions
function load_sheetjs_library() {
	if (typeof XLSX !== "undefined") {
		return; // Already loaded
	}

	// Load SheetJS from CDN
	const script = document.createElement("script");
	script.src = "https://cdn.sheetjs.com/xlsx-0.20.1/package/dist/xlsx.full.min.js";
	script.async = true;
	document.head.appendChild(script);
}

function show_bulk_import_dialog(listview) {
	let d = new frappe.ui.Dialog({
		title: __("Bulk Import Platform Orders from Excel"),
		fields: [
			{
				fieldname: "excel_file",
				fieldtype: "Attach",
				label: __("Excel File"),
				reqd: 1,
				description: __(
					"Upload Excel file with columns: Platform, Platform Date, Order Number, Asin/Sku, Quantity, Unit Price, Total Price"
				),
				onchange: function () {
					let file_url = d.get_value("excel_file");
					if (file_url) {
						process_bulk_excel_file(file_url, d);
					}
				},
			},
			{
				fieldname: "help_section",
				fieldtype: "Section Break",
				label: __("Excel Format"),
			},
			{
				fieldname: "preview_section",
				fieldtype: "Section Break",
				label: __("Preview"),
			},
			{
				fieldname: "preview_html",
				fieldtype: "HTML",
			},
		],
		size: "extra-large",
		primary_action_label: __("Import"),
		primary_action: function (values) {
			if (!d.excel_data || d.excel_data.length === 0) {
				frappe.msgprint(__("Please upload and process an Excel file first"));
				return;
			}

			frappe.confirm(
				__("This will create {0} Platform Order(s). Continue?", [d.orders_count || 0]),
				function () {
					frappe.call({
						method: "electro_zone.electro_zone.doctype.platform_order.platform_order.bulk_import_platform_orders_from_excel",
						args: {
							data: JSON.stringify(d.excel_data),
						},
						freeze: true,
						freeze_message: __("Creating Platform Orders..."),
						callback: function (r) {
							if (r.message && r.message.success) {
								show_bulk_import_results(r.message.results, listview);
								d.hide();
							} else {
								frappe.msgprint({
									title: __("Import Failed"),
									message: r.message.message || __("Unknown error"),
									indicator: "red",
								});
							}
						},
					});
				}
			);
		},
	});

	d.show();

	// Load SheetJS library
	load_sheetjs_library();
}

function process_bulk_excel_file(file_url, dialog) {
	fetch(file_url)
		.then((response) => response.arrayBuffer())
		.then((data) => {
			if (typeof XLSX === "undefined") {
				frappe.msgprint(
					__("Excel library is still loading. Please try again in a moment.")
				);
				return;
			}

			const workbook = XLSX.read(data, { type: "array" });
			const first_sheet = workbook.Sheets[workbook.SheetNames[0]];
			const json_data = XLSX.utils.sheet_to_json(first_sheet);

			dialog.excel_data = json_data;

			show_bulk_preview(json_data, dialog);
		})
		.catch((error) => {
			frappe.msgprint({
				title: __("File Processing Error"),
				message: __("Could not process the Excel file: {0}", [error.message]),
				indicator: "red",
			});
		});
}

function show_bulk_preview(data, dialog) {
	if (!data || data.length === 0) {
		dialog.fields_dict.preview_html.$wrapper.html(
			'<p class="text-muted">No data found in Excel file</p>'
		);
		return;
	}

	// Count unique orders
	let unique_orders = new Set();
	data.forEach((row) => {
		let order_num = row["Order Number"] || "Unknown";
		unique_orders.add(order_num);
	});
	dialog.orders_count = unique_orders.size;

	let html = `
        <div class="alert alert-success">
            <p class="mb-0"><strong>${data.length}</strong> rows will create <strong>${unique_orders.size}</strong> Platform Order(s)</p>
        </div>
        <div style="max-height: 400px; overflow-y: auto;">
            <table class="table table-sm table-bordered mb-0">
                <thead>
                    <tr>
                        <th>Excel Row</th>
                        <th>Platform</th>
                        <th>ASIN/SKU</th>
                        <th>Order Number</th>
                        <th>Qty</th>
                        <th>Unit Price</th>
                        <th>Total Price</th>
                    </tr>
                </thead>
                <tbody>
    `;

	data.forEach((row, idx) => {
		let quantity = parseFloat(row["Quantity"] || 0);
		let unit_price = parseFloat(row["Unit Price"] || 0);
		let total_price = quantity * unit_price;

		html += `
            <tr>
                <td>${idx + 2}</td>
                <td>${row["Platform"] || ""}</td>
                <td>${row["Asin/Sku"] || ""}</td>
                <td>${row["Order Number"] || ""}</td>
                <td>${quantity}</td>
                <td>${unit_price.toFixed(2)}</td>
                <td>${total_price.toFixed(2)}</td>
            </tr>
        `;
	});

	html += `
                </tbody>
            </table>
        </div>
    `;

	dialog.fields_dict.preview_html.$wrapper.html(html);
}

function show_bulk_import_results(results, listview) {
	let summary = results.summary;

	let html = `
        <div class="alert ${summary.failed > 0 ? "alert-warning" : "alert-success"}">
            <h6>Import Summary</h6>
            <ul class="mb-0">
                <li><strong>${summary.created}</strong> Platform Orders created successfully</li>
                <li><strong>${summary.failed}</strong> failed</li>
                <li><strong>${summary.warnings}</strong> warnings</li>
            </ul>
        </div>
    `;

	// Show created orders
	if (results.created.length > 0) {
		html += `
            <h6 class="mt-3">Created Orders:</h6>
            <table class="table table-bordered table-sm">
                <thead>
                    <tr>
                        <th>Platform Order</th>
                        <th>Order Number</th>
                        <th>Items</th>
                        <th>Unmatched</th>
                    </tr>
                </thead>
                <tbody>
        `;

		results.created.forEach((order) => {
			html += `
                <tr class="text-success">
                    <td><a href="/app/platform-order/${order.name}" target="_blank">${
				order.name
			}</a></td>
                    <td>${order.order_number}</td>
                    <td>${order.items_count}</td>
                    <td>${
						order.unmatched_count > 0
							? '<span class="text-warning">' + order.unmatched_count + "</span>"
							: "0"
					}</td>
                </tr>
            `;
		});

		html += `</tbody></table>`;
	}

	// Show failed orders
	if (results.failed.length > 0) {
		html += `
            <h6 class="mt-3 text-danger">Failed Orders:</h6>
            <table class="table table-bordered table-sm">
                <thead>
                    <tr>
                        <th>Order Number</th>
                        <th>Error</th>
                    </tr>
                </thead>
                <tbody>
        `;

		results.failed.forEach((failure) => {
			html += `
                <tr class="text-danger">
                    <td>${failure.order_number}</td>
                    <td>${failure.error}</td>
                </tr>
            `;
		});

		html += `</tbody></table>`;
	}

	// Show warnings
	if (results.warnings.length > 0) {
		let stock_warnings = results.warnings.filter((w) => w.type === "low_stock");
		let item_warnings = results.warnings.filter((w) => w.type === "item_not_found");
		let asin_warnings = results.warnings.filter((w) => w.type === "asin_mismatch");

		if (stock_warnings.length > 0) {
			html += `
                <h6 class="mt-3 text-warning">Stock Warnings (${stock_warnings.length}):</h6>
                <div style="max-height: 200px; overflow-y: auto;">
                    <table class="table table-bordered table-sm">
                        <thead>
                            <tr>
                                <th>Order Number</th>
                                <th>Item</th>
                                <th>Required</th>
                                <th>Available</th>
                                <th>Short</th>
                            </tr>
                        </thead>
                        <tbody>
            `;

			stock_warnings.slice(0, 20).forEach((warning) => {
				html += `
                    <tr class="text-warning">
                        <td>${warning.order_number}</td>
                        <td>${warning.item_code}</td>
                        <td>${warning.required}</td>
                        <td>${warning.available}</td>
                        <td>${warning.short}</td>
                    </tr>
                `;
			});

			html += `</tbody></table>`;

			if (stock_warnings.length > 20) {
				html += `<p class="text-muted"><small>Showing first 20 of ${stock_warnings.length} stock warnings</small></p>`;
			}

			html += `</div>`;
		}

		if (item_warnings.length > 0) {
			html += `
                <h6 class="mt-3 text-warning">Items Not Found (${item_warnings.length}):</h6>
                <div style="max-height: 200px; overflow-y: auto;">
                    <table class="table table-bordered table-sm">
                        <thead>
                            <tr>
                                <th>Order Number</th>
                                <th>Excel Row</th>
                                <th>ASIN/SKU</th>
                            </tr>
                        </thead>
                        <tbody>
            `;

			item_warnings.slice(0, 20).forEach((warning) => {
				html += `
                    <tr class="text-warning">
                        <td>${warning.order_number}</td>
                        <td>${warning.row}</td>
                        <td>${warning.asin_sku}</td>
                    </tr>
                `;
			});

			html += `</tbody></table>`;

			if (item_warnings.length > 20) {
				html += `<p class="text-muted"><small>Showing first 20 of ${item_warnings.length} item warnings</small></p>`;
			}

			html += `</div>`;
		}

		if (asin_warnings.length > 0) {
			html += `
                <h6 class="mt-3 text-warning">ASIN Mismatches (${asin_warnings.length}):</h6>
                <div style="max-height: 200px; overflow-y: auto;">
                    <table class="table table-bordered table-sm">
                        <thead>
                            <tr>
                                <th>Order Number</th>
                                <th>Item Code</th>
                                <th>Excel ASIN</th>
                                <th>Marketplace ASIN</th>
                                <th>Platform</th>
                            </tr>
                        </thead>
                        <tbody>
            `;

			asin_warnings.slice(0, 20).forEach((warning) => {
				html += `
                    <tr class="text-warning">
                        <td>${warning.order_number}</td>
                        <td>${warning.item_code}</td>
                        <td>${warning.excel_asin}</td>
                        <td>${warning.marketplace_asin}</td>
                        <td>${warning.platform}</td>
                    </tr>
                `;
			});

			html += `</tbody></table>`;

			if (asin_warnings.length > 20) {
				html += `<p class="text-muted"><small>Showing first 20 of ${asin_warnings.length} ASIN mismatch warnings</small></p>`;
			}

			html += `</div>`;
		}
	}

	frappe.msgprint({
		title: __("Bulk Import Complete"),
		message: html,
		indicator: summary.failed > 0 ? "orange" : "green",
		wide: true,
	});

	// Refresh list view
	listview.refresh();
}
