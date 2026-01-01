// DN Return - State Buttons
// Purpose: Add custom buttons for DN Return state transitions
// Apply To: Delivery Note

frappe.ui.form.on("Delivery Note", {
	refresh: function (frm) {
		// Hide "Create" button if DN Return status is "Return Issued"
		// This prevents manual Credit Note creation until return is received
		if (
			frm.doc.is_return === 1 &&
			frm.doc.custom_return_status === "Return Issued"
		) {
			// Remove the "Create" menu to prevent Credit Note creation
			setTimeout(function () {
				frm.page.remove_inner_button("Sales Invoice", "Create");
				frm.page.remove_inner_button("Sales Return", "Create");
			}, 500);
		}

		// Only show custom buttons for DN Returns
		if (frm.doc.is_return !== 1) {
			return;
		}

		// Remove standard buttons for returns
		frm.page.clear_primary_action();
		frm.page.clear_secondary_action();

		// Button 1: Issue Return (Draft to Return Issued)
		if (frm.doc.docstatus === 0 && frm.doc.custom_return_status === "Draft") {
			frm
				.add_custom_button(__("Issue Return"), function () {
					// Save form first
					frm.save().then(function () {
						// Call API to issue return
						frappe.call({
							method: "electro_zone.electro_zone.handlers.delivery_note.issue_dn_return",
							args: {
								dn_return_name: frm.doc.name,
							},
							callback: function (r) {
								if (r.message && r.message.success) {
									frappe.msgprint(r.message.message);
									frm.reload_doc();
								} else {
									frappe.msgprint({
										title: __("Error"),
										message: r.message
											? r.message.message
											: "Failed to issue return",
										indicator: "red",
									});
								}
							},
						});
					});
				})
				.addClass("btn-primary");
		}

		// Button 2: Receive Return (Return Issued to Return Received)
		if (
			frm.doc.docstatus === 1 &&
			frm.doc.custom_return_status === "Return Issued"
		) {
			frm
				.add_custom_button(__("Receive Return"), function () {
					frappe.confirm(
						"This will create a Credit Note and update customer balance. Continue?",
						function () {
							// Call API to receive return
							frappe.call({
								method: "electro_zone.electro_zone.handlers.delivery_note.receive_dn_return",
								args: {
									dn_return_name: frm.doc.name,
								},
								callback: function (r) {
									if (r.message && r.message.success) {
										frappe.msgprint({
											title: __("Success"),
											message: r.message.message,
											indicator: "green",
										});
										frm.reload_doc();
									} else {
										frappe.msgprint({
											title: __("Error"),
											message: r.message
												? r.message.message
												: "Failed to receive return",
											indicator: "red",
										});
									}
								},
							});
						}
					);
				})
				.addClass("btn-primary");
		}

		// Disable form editing after Issue Return (soft submit lock)
		if (frm.doc.docstatus === 1) {
			frm.set_df_property("items", "read_only", 1);
			frm.disable_save();
		}

		// Show status indicator
		if (frm.doc.custom_return_status) {
			var color = "grey";
			if (frm.doc.custom_return_status === "Draft") {
				color = "grey";
			} else if (frm.doc.custom_return_status === "Return Issued") {
				color = "orange";
			} else if (frm.doc.custom_return_status === "Return Received") {
				color = "green";
			}

			frm.dashboard.add_indicator(
				__("Return Status: " + frm.doc.custom_return_status),
				color
			);
		}
	},
});
