// ====================================================================================
// CUSTOMER - CLIENT SCRIPTS COLLECTION
// ====================================================================================
// All Customer client scripts consolidated in one file
// ====================================================================================

// ====================================================================================
// SCRIPT 1: Customer - Recalculate Balance Button (Debug)
// ====================================================================================
// Purpose: Add button to manually recalculate customer balance

// DISABLED: custom_current_balance functionality
// frappe.ui.form.on("Customer", {
// 	refresh: function (frm) {
// 		// Add custom button only if the form is saved (not new)
// 		if (!frm.is_new()) {
// 			frm.add_custom_button(__("Recalculate Balance"), function () {
// 				frappe.call({
// 					method: "electro_zone.electro_zone.handlers.customer.recalculate_customer_balance",
// 					args: {
// 						customer: frm.doc.name,
// 					},
// 					freeze: true,
// 					freeze_message: __("Recalculating Customer Balance..."),
// 					callback: function (r) {
// 						if (r.message) {
// 							// Display detailed success message
// 							let message = "";

// 							if (typeof r.message === "object") {
// 								// If the response is an object, format it nicely
// 								message = '<div style="text-align: left;">';

// 								for (let key in r.message) {
// 									if (r.message.hasOwnProperty(key)) {
// 										let label = key
// 											.replace(/_/g, " ")
// 											.replace(/\b\w/g, (l) => l.toUpperCase());
// 										let value = r.message[key];

// 										// Format numbers with commas if they are numeric
// 										if (typeof value === "number") {
// 											value = value.toLocaleString("en-US", {
// 												minimumFractionDigits: 2,
// 												maximumFractionDigits: 2,
// 											});
// 										}

// 										message += `<strong>${label}:</strong> ${value}<br>`;
// 									}
// 								}
// 								message += "</div>";
// 							} else {
// 								// If it's a simple string message
// 								message = r.message;
// 							}

// 							frappe.msgprint({
// 								title: __("Balance Recalculated Successfully"),
// 								indicator: "green",
// 								message: message,
// 							});

// 							// Reload the document to show updated values
// 							frm.reload_doc();
// 						}
// 					},
// 					error: function (r) {
// 						frappe.msgprint({
// 							title: __("Error"),
// 							indicator: "red",
// 							message: __(
// 								"Failed to recalculate balance. Please check the console for details."
// 							),
// 						});
// 						console.error("Error details:", r);
// 					},
// 				});
// 			}).addClass("btn-primary");
// 		}
// 	},
// });
