// Copyright (c) 2025, didy1234567@gmail.com and contributors
// For license information, please see license.txt

// Rebate List – Auto Fetch Item Details (ERPNext v15.8+)
// Purpose:
// - Auto-fetch Item Group, Brand, and Final Price List when Item Code selected
// - Make these fields read-only permanently (cannot be edited)
// - Provide visual feedback to user with alert message
//
// Business Rules:
// - Fetched fields must remain locked to ensure data integrity
// - Final Price List comes from custom field: custom_current_final_price_list_calculated
// - If Item Code cleared, dependent fields also clear
//
// Triggers:
// - item_code: When user selects or changes Item Code
// - refresh: When form loads (to maintain read-only state)
// - onload: Initial form load (to maintain read-only state)

frappe.ui.form.on("Rebate List", {
    item_code(frm) {
        if (frm.doc.item_code) {
            // Fetch Item details via frappe.client.get
            frappe.call({
                method: "frappe.client.get",
                args: {
                    doctype: "Item",
                    name: frm.doc.item_code,
                },
                callback: function(r) {
                    if (r.message) {
                        // Auto-fill Item Group and Brand from Item master
                        frm.set_value("item_group", r.message.item_group);
                        frm.set_value("brand", r.message.brand);

                        // Auto-fill Final Price List from custom field
                        // custom_current_final_price_list_calculated holds the calculated price
                        let final_price = r.message.custom_current_final_price_list_calculated || 0;
                        frm.set_value("final_price_list", final_price);

                        // PERMANENTLY lock these fields (read-only)
                        frm.set_df_property("item_group", "read_only", 1);
                        frm.set_df_property("brand", "read_only", 1);
                        frm.set_df_property("final_price_list", "read_only", 1);

                        // Show success message to user
                        frappe.show_alert({
                            message: __("Item details auto-filled and locked"),
                            indicator: "green"
                        });
                    }
                }
            });
        } else {
            // If Item Code cleared, clear dependent fields
            frm.set_value("item_group", "");
            frm.set_value("brand", "");
            frm.set_value("final_price_list", 0);
        }
    },

    refresh(frm) {
        // Always ensure these fields are read-only if Item Code exists
        // This prevents any manual editing even on form refresh
        if (frm.doc.item_code) {
            frm.set_df_property("item_group", "read_only", 1);
            frm.set_df_property("brand", "read_only", 1);
            frm.set_df_property("final_price_list", "read_only", 1);
        }

        // Calculate on form load to show current value
        calculate_final_rate_price(frm);
    },

    onload(frm) {
        // Apply read-only on form load (when opening existing record)
        if (frm.doc.item_code) {
            frm.set_df_property("item_group", "read_only", 1);
            frm.set_df_property("brand", "read_only", 1);
            frm.set_df_property("final_price_list", "read_only", 1);
        }
    },

    // Rebate List – Calculate Final Rate Price (ERPNext v15.8+)
    // Purpose:
    // - Dynamically calculate Final Rate Price based on Method and Discounts
    // - Support both "Gross" and "Net" calculation methods
    // - Recalculate whenever Method or Discount fields change
    // - Provide real-time calculation feedback
    //
    // Calculation Methods:
    // 1. Gross: Final Price - (Final Price × (Cash% + Invoice%))
    //    - Combines discounts into total percentage
    //    - Applies total discount to Final Price List
    //
    // 2. Net: Apply discounts sequentially
    //    - Step 1: Apply Cash Discount to Final Price List
    //    - Step 2: Apply Invoice Discount to result from Step 1
    //
    // Example (Final Price = 10,000, Cash = 5%, Invoice = 10%):
    // - Gross: 10,000 - (10,000 × 15%) = 8,500
    // - Net: (10,000 - 5%) = 9,500 → (9,500 - 10%) = 8,550
    //
    // Triggers:
    // - method: When user selects Gross or Net
    // - cash_discount: When Cash Discount % changes
    // - invoice_discount: When Invoice Discount % changes
    // - final_price_list: When Final Price List changes (from Item selection)

    // Trigger calculation on any relevant field change
    method(frm) {
        calculate_final_rate_price(frm);
    },

    cash_discount(frm) {
        calculate_final_rate_price(frm);
    },

    invoice_discount(frm) {
        calculate_final_rate_price(frm);
    },

    final_price_list(frm) {
        calculate_final_rate_price(frm);
    }
});

function calculate_final_rate_price(frm) {
    // Get values from form fields
    let final_price_list = frm.doc.final_price_list || 0;
    let cash_discount = frm.doc.cash_discount || 0;
    let invoice_discount = frm.doc.invoice_discount || 0;
    let method = frm.doc.method;

    // Validate inputs - need both Final Price List and Method
    if (!final_price_list || !method) {
        frm.set_value("final_rate_price", 0);
        return;
    }

    let final_rate_price = 0;

    if (method === "Gross") {
        // Gross Method: Combine discounts into total percentage
        // Formula: Final Price - (Final Price × (Cash% + Invoice%))
        let total_discount_percent = (cash_discount + invoice_discount) / 100;
        final_rate_price = final_price_list - (final_price_list * total_discount_percent);

    } else if (method === "Net") {
        // Net Method: Apply discounts sequentially
        // Step 1: Apply cash discount to Final Price List
        let price_after_cash = final_price_list - (final_price_list * (cash_discount / 100));

        // Step 2: Apply invoice discount to result from Step 1
        final_rate_price = price_after_cash - (price_after_cash * (invoice_discount / 100));
    }

    // Round to 2 decimal places for currency precision
    final_rate_price = Math.round(final_rate_price * 100) / 100;

    // Update Final Rate Price field
    frm.set_value("final_rate_price", final_rate_price);
}
