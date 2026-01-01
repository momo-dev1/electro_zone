// Sales Order - Discount Value Calculation
// Purpose: Lock rate field and auto-calculate amount = qty × (rate - discount_value)
// Pattern: Auto-Fill and Lock Fields on Selection (Pattern #4)

// ─────────────────────────────────────────────────────────
// 1. FORM LOAD: Lock rate field on form render
// ─────────────────────────────────────────────────────────
frappe.ui.form.on("Sales Order", {
	refresh: function (frm) {
		// Make rate field read-only in Sales Order Item grid
		if (frm.fields_dict.items && frm.fields_dict.items.grid) {
			frm.fields_dict.items.grid.update_docfield_property(
				"rate",
				"read_only",
				1
			);
		}
	},

	onload: function (frm) {
		// Also lock on initial load
		if (frm.fields_dict.items && frm.fields_dict.items.grid) {
			frm.fields_dict.items.grid.update_docfield_property(
				"rate",
				"read_only",
				1
			);
		}
	},
});

// ─────────────────────────────────────────────────────────
// 2. ITEM EVENTS: Auto-calculate amount when values change
// ─────────────────────────────────────────────────────────
frappe.ui.form.on("Sales Order Item", {
	// When new row added
	items_add: function (frm, cdt, cdn) {
		var item = locals[cdt][cdn];

		// Set default discount_value to 0 if not set
		if (!item.custom_discount_value) {
			frappe.model.set_value(cdt, cdn, "custom_discount_value", 0);
		}

		// Calculate initial amount
		calculate_amount(frm, cdt, cdn);
	},

	// When qty changes
	qty: function (frm, cdt, cdn) {
		calculate_amount(frm, cdt, cdn);
	},

	// When rate changes (via selection, not manual edit)
	rate: function (frm, cdt, cdn) {
		calculate_amount(frm, cdt, cdn);
	},

	// When discount_value changes
	custom_discount_value: function (frm, cdt, cdn) {
		calculate_amount(frm, cdt, cdn);
	},

	// When item_code is selected (rate auto-populated)
	item_code: function (frm, cdt, cdn) {
		// Wait for rate to be populated, then calculate
		setTimeout(function () {
			calculate_amount(frm, cdt, cdn);
		}, 500);
	},
});

// ─────────────────────────────────────────────────────────
// 3. HELPER FUNCTION: Calculate amount
// ─────────────────────────────────────────────────────────
function calculate_amount(frm, cdt, cdn) {
	var item = locals[cdt][cdn];

	// Get values (default to 0 if null/undefined)
	var qty = flt(item.qty) || 0;
	var rate = flt(item.rate) || 0;
	var discount_value = flt(item.custom_discount_value) || 0;

	// Calculate: amount = qty × (rate - discount_value)
	var effective_rate = rate - discount_value;
	var new_amount = qty * effective_rate;

	// Update amount field
	frappe.model.set_value(cdt, cdn, "amount", new_amount);
}
