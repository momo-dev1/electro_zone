// Item - Display Marketplace Listings Tab
// Renders read-only table of latest marketplace listings
// Item - Recalculate Price on Sellout Included Change

frappe.ui.form.on("Item", {
	refresh(frm) {
		if (!frm.is_new()) {
			load_marketplace_listings_tab(frm);
		}
	},

	custom_sellout_included(frm) {
		// Only recalculate if document exists (not new)
		if (!frm.is_new()) {
			recalculate_final_price_and_rebate(frm);
		} else {
			console.log("Skipped: Document is new");
		}
	},
});

function load_marketplace_listings_tab(frm) {
	// Clear existing content
	if (frm.fields_dict.custom_marketplace_listings_tab) {
		frm.fields_dict.custom_marketplace_listings_tab.$wrapper.empty();
	}

	// Call API to get latest listings
	frappe
		.call({
			method: "electro_zone.electro_zone.doctype.marketplace_listing.marketplace_listing.get_latest_marketplace_listings",
			args: {
				item_code: frm.doc.name,
			},
		})
		.then(({ message }) => {
			if (!message || !message.success) {
				render_error_state(frm);
				return;
			}

			const listings = message.listings || [];
			render_listings_table(frm, listings);
		});
}

function render_listings_table(frm, listings) {
	const $wrapper = frm.fields_dict.custom_marketplace_listings_tab.$wrapper;

	let html = `
        <div class="marketplace-listings-display" style="padding: 10px;">
            <div style="margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center;">
                <h5 style="margin: 0;">Latest Marketplace Listings</h5>
                <button class="btn btn-sm btn-primary" onclick="create_new_marketplace_listing('${
					frm.doc.name
				}', '${frm.doc.item_name || ""}')">
                    <i class="fa fa-plus"></i> Create New Marketplace Listing
                </button>
            </div>
    `;

	if (listings.length === 0) {
		html += `
            <div class="alert alert-info" style="margin: 0;">
                <i class="fa fa-info-circle"></i> No marketplace listings found for this item.
                <br><small>Click "Create New Marketplace Listing" to add listings for Amazon, Noon, Jumia, etc.</small>
            </div>
        `;
	} else {
		html += `
            <table class="table table-bordered table-hover" style="margin: 0;">
                <thead style="background-color: #f5f7fa;">
                    <tr>
                        <th style="width: 15%;">Platform</th>
                        <th style="width: 20%;">ASIN / SKU</th>
                        <th style="width: 15%;">Commission</th>
                        <th style="width: 12%;">Shipping Fee</th>
                        <th style="width: 10%;">Status</th>
                        <th style="width: 13%;">Effective Date</th>
                        <th style="width: 15%;">Actions</th>
                    </tr>
                </thead>
                <tbody>
        `;
		listings.forEach((listing) => {
			const commission_display = listing.commission
				? frappe.format(listing.commission, { fieldtype: "Percent" })
				: "-";
			const shipping_display = listing.shipping_fee
				? frappe.format(listing.shipping_fee, { fieldtype: "Currency" })
				: "-";
			const status_color =
				listing.status === "Active"
					? "green"
					: listing.status === "Inactive"
					? "red"
					: "orange";

			html += `
                <tr>
                    <td><strong>${listing.platform}</strong></td>
                    <td style="font-family: monospace;">${listing.asin}</td>
                    <td>${commission_display}</td>
                    <td>${shipping_display}</td>
                    <td><span class="indicator-pill ${status_color}">${listing.status}</span></td>
                    <td>${frappe.datetime.str_to_user(listing.effective_date)}</td>
                    <td>
                        <button class="btn btn-xs btn-default" onclick="view_marketplace_listing('${
							listing.listing_name
						}')">
                            <i class="fa fa-eye"></i> View
                        </button>
            `;

			if (listing.listing_url) {
				html += `
                        <a href="${listing.listing_url}" target="_blank" class="btn btn-xs btn-default" style="margin-left: 5px;">
                            <i class="fa fa-external-link"></i>
                        </a>
                `;
			}

			html += `
                    </td>
                </tr>
            `;
		});

		html += `
                </tbody>
            </table>
            <div style="margin-top: 10px; font-size: 12px; color: #6c757d;">
                <i class="fa fa-info-circle"></i> Showing ${listings.length} latest listing(s).
                If multiple listings exist for the same Platform+ASIN, only the most recent is displayed.
            </div>
        `;
	}

	html += "</div>";

	$wrapper.html(html);
}

function render_error_state(frm) {
	const $wrapper = frm.fields_dict.custom_marketplace_listings_tab.$wrapper;
	$wrapper.html(`
        <div class="alert alert-warning" style="margin: 10px;">
            <i class="fa fa-exclamation-triangle"></i> Unable to load marketplace listings. Please refresh the page.
        </div>
    `);
}

// Global functions for button actions
window.create_new_marketplace_listing = function (item_code, item_name) {
	frappe.new_doc("Marketplace Listing", {
		item_code: item_code,
		item_name: item_name,
	});
};

window.view_marketplace_listing = function (listing_name) {
	frappe.set_route("Form", "Marketplace Listing", listing_name);
};

// ============================================================================
// Item - Recalculate Price on Sellout Included Change (ERPNext v15.8+)
// Purpose:
// - When user changes the "Sellout Included" checkbox, immediately recalculate final price
// - ALWAYS call Rebate List recalculation API to update latest record
// - Provides instant feedback with detailed logging
//
// Business Rule:
// - Checked (1): Final Price = Price List - Promo - Sellout Promo (sellout INCLUDED)
// - Unchecked (0): Final Price = Price List - Promo (sellout EXCLUDED)
// ============================================================================

function recalculate_final_price_and_rebate(frm) {
	// Get current pricing data from Item fields
	let price_list = frm.doc.custom_current_final_price_list || 0;
	let promo = frm.doc.custom_current_final_promo || 0;
	let sellout = frm.doc.custom_current_final_sellout_promo || 0;
	let sellout_included = frm.doc.custom_sellout_included || 0;

	// Calculate final price based on checkbox state
	let final_price = 0;
	let calculation_note = "";

	if (sellout_included) {
		// Checked: Include sellout promo in calculation
		final_price = price_list - promo - sellout;
		calculation_note = `${price_list} - ${promo} - ${sellout} = ${final_price} (Sellout INCLUDED)`;
	} else {
		// Unchecked (default): Exclude sellout promo
		final_price = price_list - promo;
		calculation_note = `${price_list} - ${promo} = ${final_price} (Sellout EXCLUDED)`;
	}

	// Round to 2 decimal places for currency precision
	final_price = Math.round(final_price * 100) / 100;

	// Update the calculated field
	frm.set_value("custom_current_final_price_list_calculated", final_price);

	// Show user feedback
	frappe.show_alert({
		message:
			__("Final price recalculated: ") +
			final_price +
			" (" +
			(sellout_included ? "Sellout INCLUDED" : "Sellout EXCLUDED") +
			")",
		indicator: "green",
	});

	frm
		.save()
		.then(() => {
			// Now call the API to update Rebate List
			call_rebate_recalculation_api(frm);
		})
		.catch((error) => {
			frappe.show_alert({
				message: __("Failed to save Item. Rebate List not updated."),
				indicator: "red",
			});
		});
}

function call_rebate_recalculation_api(frm) {
	frappe.call({
		method: "recalculate_rebate_for_item",
		args: {
			item_code: frm.doc.name,
		},
		callback: function (r) {
			if (r.message && r.message.success) {
				if (r.message.updated_count > 0) {
					frappe.show_alert({
						message: __(
							`✅ Updated ${r.message.updated_count} Rebate List record(s) with new Final Price List: ${r.message.new_final_price_list}`
						),
						indicator: "green",
					});
					frm.reload_doc();
				} else {
					frappe.show_alert({
						message: __("No Rebate List records found to update."),
						indicator: "blue",
					});
				}
			} else if (r.message && !r.message.success) {
				frappe.show_alert({
					message: __(
						r.message.message || "Failed to update Rebate List records."
					),
					indicator: "orange",
				});
			} else {
			}
		},
		error: function (error) {
			frappe.show_alert({
				message: __("Error calling Rebate List recalculation API."),
				indicator: "red",
			});
		},
	});
}

// ============================================================================
// Item - Download Excel Button (List View)
// Purpose: Add "Download Excel" button with ExcelJS client-side generation
// ============================================================================

// Load ExcelJS library for Excel generation
if (!window.ExcelJS) {
	frappe.require(
		"https://cdn.jsdelivr.net/npm/exceljs@4.3.0/dist/exceljs.min.js",
		function () {
			//nothing
		}
	);
}

frappe.listview_settings["Item"] = frappe.listview_settings["Item"] || {};

frappe.listview_settings["Item"].onload = function (listview) {
	// Add custom button to toolbar
	listview.page.add_inner_button(
		__("Download Stock"),
		function () {
			generate_item_stock_excel();
		},
		__("Excel")
	);
};

/**
 * Convert numeric stock quantity to text label
 * @param {number} qty - Numeric stock quantity
 * @returns {string} - Text label (Zero, Low, Mid, High)
 */
function convertQtyToLabel(qty) {
	if (qty === 0) {
		return "Zero";
	} else if (qty >= 1 && qty <= 3) {
		return "Low";
	} else if (qty >= 4 && qty <= 9) {
		return "Mid";
	} else if (qty >= 10) {
		return "High";
	} else {
		// Fallback for negative or invalid values
		return "Zero";
	}
}

/**
 * Get background color for stock level label (ExcelJS format)
 * @param {string} label - Stock level label (Zero, Low, Mid, High)
 * @returns {string|null} - ARGB color code for ExcelJS, or null for no background
 */
function getExcelJSColor(label) {
	const colors = {
		Zero: null, // No background color
		Low: "FFFF6B6B", // Light Red
		Mid: "FFFFD93D", // Light Yellow
		High: "FF6BCF7F", // Light Green
	};

	return colors[label] !== undefined ? colors[label] : null;
}

/**
 * Generate and download Excel file with item stock data
 */
function generate_item_stock_excel() {
	// Check if ExcelJS library is loaded
	if (!window.ExcelJS) {
		frappe.msgprint({
			title: __("Library Not Loaded"),
			message: __(
				"ExcelJS library is still loading. Please try again in a moment."
			),
			indicator: "orange",
		});
		return;
	}

	// Show loading indicator
	frappe.show_alert(
		{
			message: __("Fetching item data..."),
			indicator: "blue",
		},
		5
	);

	// Call server API to get item data
	frappe.call({
		method: "item_list_get_items_with_stock",
		type: "GET",
		freeze: true,
		freeze_message: __("Loading item data..."),
		callback: function (r) {
			if (r.message && r.message.success) {
				// Data fetched successfully - generate Excel
				const items = r.message.items;
				const warehouses = r.message.warehouses;
				const total_count = r.message.total_count;

				if (total_count === 0) {
					frappe.msgprint({
						title: __("No Data"),
						message: __("No stock items found to export."),
						indicator: "orange",
					});
					return;
				}

				// Generate Excel file using ExcelJS
				try {
					// Create workbook and worksheet
					const workbook = new ExcelJS.Workbook();
					const worksheet = workbook.addWorksheet("Items with Stock");

					// Define border style for all cells
					const borderStyle = {
						top: { style: "thin", color: { argb: "FF000000" } },
						left: { style: "thin", color: { argb: "FF000000" } },
						bottom: { style: "thin", color: { argb: "FF000000" } },
						right: { style: "thin", color: { argb: "FF000000" } },
					};

					// Add header row with bold font and borders
					const headerRow = worksheet.addRow([
						"Item Code",
						"Item Model",
						"Description",
						...warehouses,
					]);
					headerRow.font = { bold: true };

					// Apply borders to header cells
					headerRow.eachCell((cell) => {
						cell.border = borderStyle;
					});

					// Add data rows with colors and borders
					items.forEach((item) => {
						// Prepare row data
						const rowData = [
							item.item_code,
							item.custom_item_model,
							item.description,
						];

						// Add warehouse quantities as text labels
						warehouses.forEach((warehouse) => {
							const qty = item[warehouse] || 0;
							rowData.push(convertQtyToLabel(qty));
						});

						// Add row to worksheet
						const row = worksheet.addRow(rowData);

						// Apply borders to all cells in the row
						row.eachCell((cell) => {
							cell.border = borderStyle;
						});

						// Apply colors to warehouse columns (starting at column 4 = D)
						warehouses.forEach((warehouse, idx) => {
							const qty = item[warehouse] || 0;
							const label = convertQtyToLabel(qty);
							const cellIndex = 4 + idx; // Columns 4-9 (D-I)
							const cell = row.getCell(cellIndex);

							// Apply background color based on label (null = no background for Zero)
							const bgColor = getExcelJSColor(label);
							if (bgColor) {
								cell.fill = {
									type: "pattern",
									pattern: "solid",
									fgColor: { argb: bgColor },
								};
							}
						});
					});

					// Set column widths for better readability
					worksheet.getColumn(1).width = 20; // Item Code
					worksheet.getColumn(2).width = 20; // Item Model
					worksheet.getColumn(3).width = 40; // Description
					worksheet.getColumn(4).width = 18; // Store Display - EZ
					worksheet.getColumn(5).width = 20; // Store Warehouse - EZ
					worksheet.getColumn(6).width = 15; // Damage - EZ
					worksheet.getColumn(7).width = 20; // Damage For Sale - EZ
					worksheet.getColumn(8).width = 18; // Zahran Main - EZ
					worksheet.getColumn(9).width = 35; // Hold (Reserved / Pending Shipment) - EZ

					// Generate filename with timestamp
					const timestamp = frappe.datetime
						.now_datetime()
						.replace(/[\s:]/g, "_");
					const filename = `Item_Stock_Export_${timestamp}.xlsx`;

					// Generate Excel file and download
					workbook.xlsx
						.writeBuffer()
						.then((buffer) => {
							// Create blob from buffer
							const blob = new Blob([buffer], {
								type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
							});

							// Create download link
							const url = window.URL.createObjectURL(blob);
							const a = document.createElement("a");
							a.href = url;
							a.download = filename;
							document.body.appendChild(a);
							a.click();

							// Cleanup
							document.body.removeChild(a);
							window.URL.revokeObjectURL(url);

							// Show success message
							frappe.show_alert(
								{
									message: __("Excel file downloaded successfully: {0}", [
										filename,
									]),
									indicator: "green",
								},
								5
							);
						})
						.catch((error) => {
							console.error("Excel generation error:", error);
							frappe.msgprint({
								title: __("Excel Generation Failed"),
								message: __("Failed to generate Excel file. Please try again."),
								indicator: "red",
							});
						});
				} catch (error) {
					console.error("Excel generation error:", error);
					frappe.msgprint({
						title: __("Excel Generation Failed"),
						message: __("Failed to generate Excel file. Please try again."),
						indicator: "red",
					});
				}
			} else {
				frappe.msgprint({
					title: __("Error"),
					message: __("Failed to fetch item data. Please try again."),
					indicator: "red",
				});
			}
		},
		error: function (r) {
			frappe.msgprint({
				title: __("API Error"),
				message: __(
					"Failed to connect to server. Please check your connection and try again."
				),
				indicator: "red",
			});
		},
	});
}
