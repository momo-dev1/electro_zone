// Copyright (c) 2025, didy1234567@gmail.com and contributors
// For license information, please see license.txt

// Retail Price - Auto Fetch Item Details
frappe.ui.form.on('Retail Price', {
    refresh: function(frm) {
        // Set default tracking fields on new document
        if (frm.is_new() && !frm.doc.submitted_by) {
            frm.set_value('submitted_by', frappe.session.user);
            frm.set_value('submission_date', frappe.datetime.now_datetime());
        }
    },

    item_code: function(frm) {
        if (frm.doc.item_code) {
            // Fetch item details from Item master
            frappe.db.get_value('Item', frm.doc.item_code,
                ['custom_item_model', 'item_name', 'description', 'item_group', 'brand']
            ).then(r => {
                if (r.message) {
                    // Set item details
                    frm.set_value('item_model', r.message.custom_item_model || '');
                    frm.set_value('item_name', r.message.item_name || '');
                    frm.set_value('description', r.message.description || '');
                    frm.set_value('item_group', r.message.item_group || '');
                    frm.set_value('brand', r.message.brand || '');

                    // Set tracking fields if not already set
                    if (!frm.doc.submitted_by) {
                        frm.set_value('submitted_by', frappe.session.user);
                    }
                    if (!frm.doc.submission_date) {
                        frm.set_value('submission_date', frappe.datetime.now_datetime());
                    }
                }
            });
        }
    }
});
