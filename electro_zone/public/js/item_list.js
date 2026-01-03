// ============================================================================
// Item - Download Excel Button (List View)
// Purpose: Add "Download Excel" button with ExcelJS client-side generation
// ============================================================================

// Load ExcelJS library for Excel generation
if (!window.ExcelJS) {
	frappe.require("https://cdn.jsdelivr.net/npm/exceljs@4.3.0/dist/exceljs.min.js", function () {
		console.log("ExcelJS library loaded successfully");
	});
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
			message: __("ExcelJS library is still loading. Please try again in a moment."),
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
		method: "electro_zone.electro_zone.handlers.item.item_list_get_items_with_stock",
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
						const rowData = [item.item_code, item.custom_item_model, item.description];

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
					const timestamp = frappe.datetime.now_datetime().replace(/[\s:]/g, "_");
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
