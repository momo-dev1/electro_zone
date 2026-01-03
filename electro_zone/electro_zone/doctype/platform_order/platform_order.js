// Copyright (c) 2026, Electro Zone and contributors
// For license information, please see license.txt

frappe.ui.form.on("Platform Order", {
    refresh: function (frm) {
        // Import from Excel button - show when Pending and not submitted
        if (frm.doc.delivery_status === "Pending" && frm.doc.docstatus === 0) {
            frm.add_custom_button(__("Import from Excel"), function () {
                show_import_dialog(frm);
            }).addClass("btn-primary");
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
                    __("This will issue items from Hold Warehouse. Continue?"),
                    function () {
                        frappe.call({
                            method: "electro_zone.electro_zone.doctype.platform_order.platform_order.mark_shipped",
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

function show_import_dialog(frm) {
    let d = new frappe.ui.Dialog({
        title: __("Import Platform Orders from Excel"),
        fields: [
            {
                fieldname: "excel_file",
                fieldtype: "Attach",
                label: __("Excel File"),
                reqd: 1,
                description: __(
                    "Upload Excel file with columns: Platform, Platform Date, Order Number, Asin/Sku, Quantity, Unit Price, Total Price"
                ),
                onchange: function() {
                    // Process file when attached
                    let file_url = d.get_value("excel_file");
                    if (file_url) {
                        process_excel_file(file_url, d);
                    }
                }
            },
            {
                fieldname: "help_section",
                fieldtype: "Section Break",
                label: __("Excel Format"),
            },
            {
                fieldname: "help_html",
                fieldtype: "HTML",
                options: `
                    <div class="alert alert-info">
                        <h6>Expected Excel Format:</h6>
                        <table class="table table-bordered table-sm">
                            <thead>
                                <tr>
                                    <th>Platform</th>
                                    <th>Platform Date</th>
                                    <th>Order Number</th>
                                    <th>Asin/Sku</th>
                                    <th>Quantity</th>
                                    <th>Unit Price</th>
                                    <th>Total Price</th>
                                </tr>
                            </thead>
                            <tbody>
                                <tr>
                                    <td>Amazon</td>
                                    <td>2024-01-15</td>
                                    <td>AMZ-001</td>
                                    <td>B08XYZ123</td>
                                    <td>2</td>
                                    <td>150.00</td>
                                    <td>300.00</td>
                                </tr>
                            </tbody>
                        </table>
                        <p class="mb-0"><small>Items will be matched using the Asin/Sku field against Item.platform_asin_sku</small></p>
                    </div>
                `,
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

            frappe.call({
                method: "electro_zone.electro_zone.doctype.platform_order.platform_order.import_platform_orders_from_excel",
                args: {
                    data: JSON.stringify(d.excel_data),
                    platform_order_name: frm.doc.name,
                },
                freeze: true,
                freeze_message: __("Importing data..."),
                callback: function (r) {
                    if (r.message && r.message.success) {
                        // Show success message
                        frappe.msgprint({
                            title: __("Import Successful"),
                            message: r.message.message,
                            indicator: "green",
                        });

                        // Show detailed results
                        if (r.message.results.stock_warnings.length > 0) {
                            show_stock_warnings(r.message.results.stock_warnings);
                        }

                        if (r.message.results.unmatched.length > 0) {
                            show_unmatched_items(r.message.results.unmatched);
                        }

                        frm.reload_doc();
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
        },
    });

    d.show();

    // Load SheetJS from CDN if not already loaded
    load_sheetjs_library();
}

function load_sheetjs_library() {
    if (typeof XLSX !== 'undefined') {
        return; // Already loaded
    }

    // Load SheetJS from CDN
    const script = document.createElement('script');
    script.src = 'https://cdn.sheetjs.com/xlsx-0.20.1/package/dist/xlsx.full.min.js';
    script.async = true;
    document.head.appendChild(script);
}

function process_excel_file(file_url, dialog) {
    // Fetch the file
    fetch(file_url)
        .then(response => response.arrayBuffer())
        .then(data => {
            // Check if XLSX is loaded
            if (typeof XLSX === 'undefined') {
                frappe.msgprint(__("Excel library is still loading. Please try again in a moment."));
                return;
            }

            // Parse the Excel file
            const workbook = XLSX.read(data, { type: 'array' });
            const first_sheet = workbook.Sheets[workbook.SheetNames[0]];

            // Convert to JSON
            const json_data = XLSX.utils.sheet_to_json(first_sheet);

            // Store in dialog
            dialog.excel_data = json_data;

            // Show preview
            show_preview_table(json_data, dialog);
        })
        .catch(error => {
            frappe.msgprint({
                title: __("File Processing Error"),
                message: __("Could not process the Excel file: {0}", [error.message]),
                indicator: "red",
            });
        });
}

function show_preview_table(data, dialog) {
    if (!data || data.length === 0) {
        dialog.fields_dict.preview_html.$wrapper.html('<p class="text-muted">No data found in Excel file</p>');
        return;
    }

    let html = `
        <div class="alert alert-success">
            <p class="mb-0"><strong>${data.length}</strong> rows found in Excel file</p>
        </div>
        <div style="max-height: 300px; overflow-y: auto;">
        <table class="table table-bordered table-sm">
            <thead>
                <tr>
                    <th>Row</th>
                    <th>Platform</th>
                    <th>Platform Date</th>
                    <th>Order Number</th>
                    <th>Asin/Sku</th>
                    <th>Quantity</th>
                    <th>Unit Price</th>
                    <th>Total Price</th>
                </tr>
            </thead>
            <tbody>
    `;

    data.slice(0, 10).forEach((row, idx) => {
        html += `
            <tr>
                <td>${idx + 1}</td>
                <td>${row['Platform'] || ''}</td>
                <td>${row['Platform Date'] || ''}</td>
                <td>${row['Order Number'] || ''}</td>
                <td>${row['Asin/Sku'] || ''}</td>
                <td>${row['Quantity'] || ''}</td>
                <td>${row['Unit Price'] || ''}</td>
                <td>${row['Total Price'] || ''}</td>
            </tr>
        `;
    });

    html += `</tbody></table></div>`;

    if (data.length > 10) {
        html += `<p class="text-muted mt-2"><small>Showing first 10 of ${data.length} rows</small></p>`;
    }

    dialog.fields_dict.preview_html.$wrapper.html(html);
}

function show_stock_warnings(warnings) {
    let html = `
        <table class="table table-bordered">
            <thead>
                <tr>
                    <th>Row</th>
                    <th>Item Code</th>
                    <th>Required</th>
                    <th>Available</th>
                    <th>Short</th>
                </tr>
            </thead>
            <tbody>
    `;

    warnings.forEach((w) => {
        html += `
            <tr class="text-warning">
                <td>${w.row}</td>
                <td>${w.item_code}</td>
                <td>${w.required}</td>
                <td>${w.available}</td>
                <td>${w.short}</td>
            </tr>
        `;
    });

    html += `</tbody></table>`;

    frappe.msgprint({
        title: __("Stock Warnings ({0} items)", [warnings.length]),
        message: html,
        indicator: "yellow",
        wide: true,
    });
}

function show_unmatched_items(unmatched) {
    let html = `
        <table class="table table-bordered">
            <thead>
                <tr>
                    <th>Row</th>
                    <th>Asin/SKU</th>
                    <th>Quantity</th>
                </tr>
            </thead>
            <tbody>
    `;

    unmatched.forEach((u) => {
        html += `
            <tr class="text-danger">
                <td>${u.row}</td>
                <td>${u.asin_sku}</td>
                <td>${u.quantity}</td>
            </tr>
        `;
    });

    html += `</tbody></table>`;

    frappe.msgprint({
        title: __("Items Not Found ({0} items)", [unmatched.length]),
        message: html + `<p class="mt-3">These items were not imported because no matching Item with platform_asin_sku was found.</p>`,
        indicator: "red",
        wide: true,
    });
}

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

    if (frm.doc.delivery_status) {
        frm.page.set_indicator(
            __(frm.doc.delivery_status),
            status_colors[frm.doc.delivery_status] || "gray"
        );
    }
}
