// Copyright (c) 2026, Electro Zone and contributors
// For license information, please see license.txt

frappe.ui.form.on("Platform Order", {
    refresh: function (frm) {
        // Show Sales Invoice link if available
        if (frm.doc.sales_invoice) {
            frm.add_custom_button(__("View Sales Invoice"), function () {
                frappe.set_route("Form", "Sales Invoice", frm.doc.sales_invoice);
            }, __("View"));
        }

        // Mark as Ready to Ship button - show when Pending and not submitted
        if (frm.doc.delivery_status === "Pending" && frm.doc.docstatus === 0 && frm.doc.items && frm.doc.items.length > 0) {
            frm.add_custom_button(__("Mark as Ready to Ship"), function () {
                frappe.confirm(
                    __("This will move items from Main Warehouse to Hold Warehouse. Continue?"),
                    function () {
                        frappe.call({
                            method: "electro_zone.electro_zone.doctype.platform_order.platform_order.mark_ready_to_ship",
                            args: {
                                platform_order_name: frm.doc.name,
                            },
                            freeze: true,
                            freeze_message: __("Creating Stock Entry..."),
                            callback: function (r) {
                                if (r.message && r.message.success) {
                                    frm.reload_doc();
                                }
                            },
                        });
                    }
                );
            }).addClass("btn-primary");
        }

        // Mark as Shipped button - show when Ready to Ship and submitted
        if (frm.doc.delivery_status === "Ready to Ship" && frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Mark as Shipped"), function () {
                frappe.confirm(
                    __("This will create Sales Invoice and deduct stock from Hold Warehouse. Continue?"),
                    function () {
                        frappe.call({
                            method: "electro_zone.electro_zone.doctype.platform_order.platform_order.mark_shipped",
                            args: {
                                platform_order_name: frm.doc.name,
                            },
                            freeze: true,
                            freeze_message: __("Creating Sales Invoice..."),
                            callback: function (r) {
                                if (r.message && r.message.success) {
                                    frm.reload_doc();
                                }
                            },
                        });
                    }
                );
            }).addClass("btn-primary");
        }

        // Manual status change for final statuses - show when Shipped and submitted
        if (frm.doc.delivery_status === "Shipped" && frm.doc.docstatus === 1) {
            frm.add_custom_button(__("Update Status"), function () {
                let d = new frappe.ui.Dialog({
                    title: __("Update Delivery Status"),
                    fields: [
                        {
                            fieldname: "new_status",
                            fieldtype: "Select",
                            label: __("New Status"),
                            options: "Delivered\nCanceled\nDelivery Failed\nReturned",
                            reqd: 1,
                        },
                    ],
                    primary_action_label: __("Update"),
                    primary_action: function (values) {
                        frm.set_value("delivery_status", values.new_status);
                        frm.save();
                        d.hide();
                    },
                });
                d.show();
            });
        }

        // Show Match Items button if there are unmatched items (is_matched = 0)
        let has_unmatched = frm.doc.items && frm.doc.items.some(item => !item.is_matched);
        if (has_unmatched && frm.doc.docstatus === 0) {
            frm.add_custom_button(__("Match Items"), function () {
                show_match_items_dialog(frm);
            }).addClass("btn-warning");
        }

        // Add indicator colors based on status
        set_status_indicator(frm);
    },
});

// Platform Order Item child table events
frappe.ui.form.on("Platform Order Item", {
    item_code: function (frm, cdt, cdn) {
        update_stock_availability(frm, cdt, cdn);
    },

    quantity: function (frm, cdt, cdn) {
        calculate_total_price(frm, cdt, cdn);
        update_stock_availability(frm, cdt, cdn);
    },

    unit_price: function (frm, cdt, cdn) {
        calculate_total_price(frm, cdt, cdn);
    },
});

function update_stock_availability(frm, cdt, cdn) {
    let item = locals[cdt][cdn];

    if (item.item_code) {
        frappe.call({
            method: "frappe.client.get_value",
            args: {
                doctype: "Bin",
                filters: {
                    item_code: item.item_code,
                    warehouse: ["like", "%Main%"],
                },
                fieldname: "actual_qty",
            },
            callback: function (r) {
                if (r.message) {
                    frappe.model.set_value(cdt, cdn, "stock_available", r.message.actual_qty || 0);

                    // Warning if insufficient stock
                    if (item.quantity > (r.message.actual_qty || 0)) {
                        frappe.show_alert(
                            {
                                message: __("Item {0}: Required {1}, Available {2}", [
                                    item.item_code,
                                    item.quantity,
                                    r.message.actual_qty || 0,
                                ]),
                                indicator: "yellow",
                            },
                            5
                        );
                    }
                }
            },
        });
    }
}

function calculate_total_price(frm, cdt, cdn) {
    let item = locals[cdt][cdn];
    let total = (item.quantity || 0) * (item.unit_price || 0);
    frappe.model.set_value(cdt, cdn, "total_price", total);
}

function set_status_indicator(frm) {
    const status_colors = {
        "Pending": "orange",
        "Ready to Ship": "blue",
        "Shipped": "purple",
        "Delivered": "green",
        "Canceled": "red",
        "Delivery Failed": "red",
        "Returned": "yellow",
    };

    // Primary indicator: Delivery Status
    if (frm.doc.delivery_status) {
        frm.page.set_indicator(
            __(frm.doc.delivery_status),
            status_colors[frm.doc.delivery_status] || "gray"
        );
    }

    // Secondary indicators for match and stock status
    if (frm.doc.match_status && frm.doc.match_status !== "Fully Matched") {
        frm.dashboard.add_indicator(
            __("Match Status: {0}", [frm.doc.match_status]),
            frm.doc.match_status === "Unmatched" ? "red" : "orange"
        );
    }

    if (frm.doc.stock_status && frm.doc.stock_status !== "Stock Available") {
        frm.dashboard.add_indicator(
            __("Stock Status: {0}", [frm.doc.stock_status]),
            frm.doc.stock_status === "No Stock" ? "red" : "orange"
        );
    }
}

function show_match_items_dialog(frm) {
    // Filter unmatched items (is_matched = 0)
    let unmatched_items = frm.doc.items.filter(item => !item.is_matched);

    let d = new frappe.ui.Dialog({
        title: __("Match Unmatched Items"),
        fields: [
            {
                fieldname: "info",
                fieldtype: "HTML",
                options: `<p class="text-muted">${__("Select an unmatched item and match it to an Item Code")}</p>`
            },
            {
                fieldname: "unmatched_item",
                fieldtype: "Select",
                label: __("Unmatched Item (Platform SKU)"),
                options: unmatched_items.map(item => `${item.name}:${item.platform_sku}`).join("\n"),
                reqd: 1
            },
            {
                fieldname: "item_code",
                fieldtype: "Link",
                label: __("Match to Item Code"),
                options: "Item",
                reqd: 1
            }
        ],
        primary_action_label: __("Match"),
        primary_action: function(values) {
            let unmatched_row_name = values.unmatched_item.split(":")[0];
            frappe.call({
                method: "electro_zone.electro_zone.doctype.platform_order.platform_order.match_unmatched_item",
                args: {
                    platform_order: frm.doc.name,
                    unmatched_item_row_name: unmatched_row_name,
                    item_code: values.item_code
                },
                freeze: true,
                freeze_message: __("Matching item..."),
                callback: function(r) {
                    if (r.message && r.message.success) {
                        frm.reload_doc();
                        frappe.show_alert({
                            message: __("Item matched successfully"),
                            indicator: "green"
                        });
                        d.hide();
                    }
                }
            });
        }
    });
    d.show();
}
