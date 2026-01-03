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
                            <p class="mb-0">${__("Selected {0} Platform Orders", [selected.length])}</p>
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
    },

    // Custom indicator colors based on delivery status
    get_indicator: function (doc) {
        const status_colors = {
            "Pending": "orange",
            "Ready to Ship": "blue",
            "Shipped": "purple",
            "Delivered": "green",
            "Canceled": "red",
            "Delivery Failed": "red",
            "Returned": "yellow",
        };

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
            return `<b>${value}</b>`;
        },
    },
};
