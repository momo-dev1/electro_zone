// Copyright (c) 2025, didy1234567@gmail.com and contributors
// For license information, please see license.txt

// frappe.ui.form.on("Warehouse Transfer Request", {
// 	refresh(frm) {

// 	},
// });
// ============================================================
// Warehouse Transfer Request - Complete Client Script
// ============================================================
// This script handles ALL client-side functionality:
// - Workflow buttons (NO Save/Submit buttons)
// - Field locking (all fields + items table)
// - Warehouse validation and filtering
// - Item stock validation
// - Receipt confirmation dialog
// - Excel bulk upload/download for items
// ============================================================

// Load SheetJS library for Excel processing
if (!window.XLSX) {
  frappe.require(
    "https://cdn.sheetjs.com/xlsx-0.18.5/package/dist/xlsx.full.min.js",
    function () {
      //nothing
    }
  );
}

frappe.ui.form.on("Warehouse Transfer Request", {
  onload: function (frm) {
    setup_warehouse_filters(frm);
    // Store original quantities for increment calculation
    store_original_quantities(frm);
  },

  refresh: function (frm) {
    // Setup filters and visibility
    setup_warehouse_filters(frm);
    setup_item_filters(frm);
    update_field_visibility(frm);
    update_transfer_type_readonly(frm);

    // Store original quantities for increment calculation
    store_original_quantities(frm);

    // Control child table field editability
    control_quantity_field_editability(frm);

    // CRITICAL: Remove ALL standard buttons using multiple methods
    frm.page.clear_primary_action();
    frm.page.clear_secondary_action();
    frm.page.clear_actions_menu();
    frm.page.clear_menu();

    let status = frm.doc.approval_status;
    let user_roles = frappe.user_roles;

    // Add ONLY workflow buttons based on status
    if (status === "Draft") {
      frm
        .add_custom_button(__("Submit for Approval"), function () {
          if (frm.is_dirty()) {
            frm.save().then(() => submit_for_approval(frm));
          } else {
            submit_for_approval(frm);
          }
        })
        .addClass("btn-primary");
    } else if (
      status === "Pending Approval" &&
      user_roles.includes("External Transfer Manager")
    ) {
      // ✅ Approve button (green) inside "Approval" dropdown
      let approve_btn = frm.add_custom_button(
        __("Approve"),
        function () {
          frappe.confirm(
            __(
              "Are you sure you want to approve this transfer request?<br><br><strong>Transfer Details:</strong><br>From: {0}<br>To: {1}<br>Items: {2}",
              [
                frm.doc.source_warehouse,
                frm.doc.target_warehouse,
                frm.doc.items.length,
              ]
            ),
            function () {
              approve_transfer(frm);
            },
            function () {
              // User clicked "No" - do nothing
            }
          );
        },
        __("Approval")
      );

      approve_btn.css({
        "background-color": "#28a745",
        color: "white",
        width: "100%",
        "margin-bottom": "8px",
        "box-sizing": "border-box",
      });

      // Add hover effect (no transform)
      approve_btn.hover(
        function () {
          $(this).css({
            "background-color": "#218838",
          });
        },
        function () {
          $(this).css({
            "background-color": "#28a745",
            "box-shadow": "none",
          });
        }
      );

      // ✅ Reject button (red) inside "Approval" dropdown
      let reject_btn = frm.add_custom_button(
        __("Reject"),
        function () {
          frappe.confirm(
            __(
              "Are you sure you want to reject this transfer request?<br><br>You will be asked to provide a rejection reason in the next step."
            ),
            function () {
              // Show rejection reason dialog after confirmation
              frappe.prompt(
                [
                  {
                    fieldname: "rejection_reason",
                    fieldtype: "Small Text",
                    label: __("Rejection Reason"),
                    reqd: 1,
                    description: __(
                      "Please explain why this transfer is being rejected"
                    ),
                  },
                ],
                function (values) {
                  reject_transfer(frm, values.rejection_reason);
                },
                __("Reject Transfer"),
                __("Submit Rejection")
              );
            },
            function () {
              // User clicked "No" - do nothing
            }
          );
        },
        __("Approval")
      );

      reject_btn.css({
        "background-color": "#dc3545",
        color: "white",
        width: "100%",
        "box-sizing": "border-box",
      });

      // Add hover effect (no transform)
      reject_btn.hover(
        function () {
          $(this).css({
            "background-color": "#c82333",
          });
        },
        function () {
          $(this).css({
            "background-color": "#dc3545",
            "box-shadow": "none",
          });
        }
      );
    } else if (
      status === "Approved - Pending Shipment" ||
      status === "Partially Shipped"
    ) {
      if (can_ship(frm)) {
        frm
          .add_custom_button(__("Mark as Shipped"), function () {
            save_and_ship(frm);
          })
          .addClass("btn-primary");
      }
    } else if (
      status === "Shipped" ||
      status === "Partially Shipped" ||
      status === "Partially Completed"
    ) {
      if (can_receive(frm)) {
        // ✅ Standalone Confirm Receipt button (green) with hover effect
        let receipt_btn = frm.add_custom_button(
          __("Confirm Receipt"),
          function () {
            save_and_receive(frm);
          }
        );

        receipt_btn.css({
          "background-color": "#28a745",
          color: "white",
        });

        // Add hover effect (no transform)
        receipt_btn.hover(
          function () {
            $(this).css({
              "background-color": "#218838",
              "box-shadow": "0 4px 8px rgba(40, 167, 69, 0.4)",
            });
          },
          function () {
            $(this).css({
              "background-color": "#28a745",
              "box-shadow": "none",
            });
          }
        );
      }
    }

    // Add Excel upload/download buttons (visible in Draft status only)
    if (status === "Draft") {
      // Download Template button
      frm.add_custom_button(
        __("Download Template"),
        function () {
          download_excel_template(frm);
        },
        __("Excel")
      );

      // Upload Items from Excel button
      frm.add_custom_button(
        __("Upload Items from Excel"),
        function () {
          upload_items_from_excel(frm);
        },
        __("Excel")
      );
    }

    // Disable form for completed/rejected
    if (status === "Completed" || status === "Rejected") {
      frm.disable_form();
    }
  },

  transfer_type: function (frm) {
    // When transfer type changes, reset warehouses and update filters
    if (frm.doc.source_warehouse || frm.doc.target_warehouse) {
      frappe.confirm(
        __("Changing transfer type will clear warehouse selections. Continue?"),
        function () {
          frm.set_value("source_warehouse", "");
          frm.set_value("target_warehouse", "");
          frm.clear_table("items");
          frm.refresh_field("items");
          setup_warehouse_filters(frm);
        },
        function () {
          frm.reload_doc();
        }
      );
    } else {
      setup_warehouse_filters(frm);
    }
  },

  source_warehouse: function (frm) {
    if (
      frm.doc.source_warehouse &&
      frm.doc.target_warehouse &&
      frm.doc.source_warehouse === frm.doc.target_warehouse
    ) {
      frappe.msgprint({
        title: __("Invalid Selection"),
        message: __("Source and Target warehouses cannot be the same"),
        indicator: "red",
      });
      frm.set_value("source_warehouse", "");
      return;
    }

    setup_warehouse_filters(frm);
    setup_item_filters(frm);

    if (frm.doc.items && frm.doc.items.length > 0) {
      frappe.confirm(
        __("Changing source warehouse will clear all items. Continue?"),
        function () {
          frm.clear_table("items");
          frm.refresh_field("items");
        },
        function () {
          frm.reload_doc();
        }
      );
    }
  },

  target_warehouse: function (frm) {
    if (
      frm.doc.source_warehouse &&
      frm.doc.target_warehouse &&
      frm.doc.source_warehouse === frm.doc.target_warehouse
    ) {
      frappe.msgprint({
        title: __("Invalid Selection"),
        message: __("Source and Target warehouses cannot be the same"),
        indicator: "red",
      });
      frm.set_value("target_warehouse", "");
      return;
    }

    setup_warehouse_filters(frm);
  },

  approval_status: function (frm) {
    update_field_visibility(frm);
    control_quantity_field_editability(frm);
  },
});

// ============================================================
// CHILD TABLE EVENTS
// ============================================================

frappe.ui.form.on("Warehouse Transfer Request Item", {
  items_add: function (frm, cdt, cdn) {
    let item = locals[cdt][cdn];
    if (!item.received_qty) {
      frappe.model.set_value(cdt, cdn, "received_qty", 0);
    }
    if (!item.shipped_qty) {
      frappe.model.set_value(cdt, cdn, "shipped_qty", 0);
    }
    // Auto-set accepted_qty = requested_qty on item add (Draft status)
    if (frm.doc.approval_status === "Draft" && item.requested_qty) {
      frappe.model.set_value(cdt, cdn, "accepted_qty", item.requested_qty);
    }
    // Apply field editability controls
    control_quantity_field_editability(frm);
  },

  item_code: function (frm, cdt, cdn) {
    let item = locals[cdt][cdn];

    if (!frm.doc.source_warehouse) {
      frappe.model.set_value(cdt, cdn, "item_code", "");
      frappe.throw(__("Please select Source Warehouse first"));
      return;
    }

    if (!frm.doc.target_warehouse) {
      frappe.model.set_value(cdt, cdn, "item_code", "");
      frappe.throw(__("Please select Target Warehouse first"));
      return;
    }

    if (!item.received_qty && item.received_qty !== 0) {
      frappe.model.set_value(cdt, cdn, "received_qty", 0);
    }

    if (!item.shipped_qty && item.shipped_qty !== 0) {
      frappe.model.set_value(cdt, cdn, "shipped_qty", 0);
    }

    if (
      item.item_code &&
      frm.doc.source_warehouse &&
      frm.doc.target_warehouse
    ) {
      // Fetch source warehouse stock
      frappe.call({
        method: "frappe.client.get_value",
        args: {
          doctype: "Bin",
          filters: {
            item_code: item.item_code,
            warehouse: frm.doc.source_warehouse,
          },
          fieldname: "actual_qty",
        },
        callback: function (r) {
          let available_qty =
            r.message && r.message.actual_qty ? r.message.actual_qty : 0;
          frappe.model.set_value(cdt, cdn, "available_qty", available_qty);

          if (available_qty <= 0) {
            frappe.msgprint({
              title: __("No Stock Available"),
              message: __("Item {0} has no stock in {1}", [
                item.item_code,
                frm.doc.source_warehouse,
              ]),
              indicator: "red",
            });
            frappe.model.set_value(cdt, cdn, "item_code", "");
          }
        },
      });

      // Fetch target warehouse stock
      frappe.call({
        method: "frappe.client.get_value",
        args: {
          doctype: "Bin",
          filters: {
            item_code: item.item_code,
            warehouse: frm.doc.target_warehouse,
          },
          fieldname: "actual_qty",
        },
        callback: function (r) {
          let available_qty_target =
            r.message && r.message.actual_qty ? r.message.actual_qty : 0;
          frappe.model.set_value(
            cdt,
            cdn,
            "available_qty_target",
            available_qty_target
          );
        },
      });
    }
  },

  requested_qty: function (frm, cdt, cdn) {
    let item = locals[cdt][cdn];

    if (item.available_qty && item.requested_qty > item.available_qty) {
      frappe.show_alert(
        {
          message: __(
            "Requested ({0}) exceeds available ({1}). Adjusted to maximum.",
            [item.requested_qty, item.available_qty]
          ),
          indicator: "orange",
        },
        7
      );
      frappe.model.set_value(cdt, cdn, "requested_qty", item.available_qty);
    }

    let pending = item.requested_qty - (item.received_qty || 0);
    frappe.model.set_value(cdt, cdn, "pending_qty", pending);
  },

  received_qty: function (frm, cdt, cdn) {
    let item = locals[cdt][cdn];

    if (item.received_qty > item.requested_qty) {
      frappe.model.set_value(cdt, cdn, "received_qty", item.requested_qty);
      frappe.show_alert(
        {
          message: __("Received cannot exceed requested"),
          indicator: "red",
        },
        5
      );
    }

    let pending = item.requested_qty - item.received_qty;
    frappe.model.set_value(cdt, cdn, "pending_qty", pending);
  },

  before_items_remove: function (frm, cdt, cdn) {
    let status = frm.doc.approval_status;
    let locked = [
      "Pending Approval",
      "Approved - Pending Shipment",
      "Shipped",
      "Partially Completed",
      "Completed",
      "Rejected",
    ];

    if (locked.includes(status)) {
      frappe.throw(__("Cannot delete items after submitting for approval"));
      return false;
    }
  },
});

// ============================================================
// HELPER FUNCTIONS
// ============================================================

// Store original quantities for calculating increments
function store_original_quantities(frm) {
  if (!frm.doc.items) return;

  // Store original shipped_qty and received_qty for each item
  // This allows us to calculate increments when user edits inline
  frm.__original_quantities = {};

  frm.doc.items.forEach(function (item) {
    frm.__original_quantities[item.name] = {
      shipped_qty: item.shipped_qty || 0,
      received_qty: item.received_qty || 0,
    };
  });
}

// Control which quantity fields are editable based on status and role
function control_quantity_field_editability(frm) {
  if (!frm.fields_dict.items || !frm.fields_dict.items.grid) return;

  let status = frm.doc.approval_status;
  let user_roles = frappe.user_roles;

  // Determine which fields should be editable
  let editable_fields = {
    item_code: false,
    requested_qty: false,
    accepted_qty: false,
    shipped_qty: false,
    received_qty: false,
    requester_notes: false,
    manager_notes: false,
  };

  // Rule 1: accepted_qty editable for External Transfer Manager in Pending Approval
  if (
    status === "Pending Approval" &&
    user_roles.includes("External Transfer Manager")
  ) {
    editable_fields.accepted_qty = true;
    editable_fields.manager_notes = true;
  }

  // Rule 2: shipped_qty editable for Source Warehouse Manager after approval
  if (
    (status === "Approved - Pending Shipment" ||
      status === "Partially Shipped") &&
    (user_roles.includes("Zahran Warehouse Manager") ||
      user_roles.includes("Store Manager"))
  ) {
    editable_fields.shipped_qty = true;
  }

  // Rule 3: received_qty editable for Destination Warehouse Manager after shipment
  if (
    (status === "Shipped" ||
      status === "Partially Shipped" ||
      status === "Partially Completed") &&
    (user_roles.includes("Zahran Warehouse Manager") ||
      user_roles.includes("Store Manager"))
  ) {
    editable_fields.received_qty = true;
  }

  // Rule 4: requester_notes editable ONLY in Draft status (anyone)
  if (status === "Draft") {
    editable_fields.requester_notes = true;
  }

  // Rule 5: item_code editable ONLY in Draft status (anyone)
  if (status === "Draft") {
    editable_fields.item_code = true;
  }

  // Rule 6: requested_qty editable ONLY in Draft status (anyone)
  if (status === "Draft") {
    editable_fields.requested_qty = true;
  }

  // Get the grid meta
  let grid_meta = frappe.get_meta("Warehouse Transfer Request Item");

  if (grid_meta) {
    // Check current DocType field settings
    ["item_code", "requested_qty", "accepted_qty", "shipped_qty", "received_qty", "requester_notes", "manager_notes"].forEach(function (
      fieldname
    ) {
      let field = grid_meta.fields.find((f) => f.fieldname === fieldname);
      if (field) {
        // Try to set read_only based on our rules
        field.read_only = editable_fields[fieldname] ? 0 : 1;
      }
    });
  }

  // Try multiple methods to ensure fields are editable
  let grid = frm.fields_dict.items.grid;

  // Method 1: Update grid docfields
  if (grid.docfields) {
    grid.docfields.forEach(function (df) {
      if (
        ["item_code", "requested_qty", "accepted_qty", "shipped_qty", "received_qty", "requester_notes", "manager_notes"].includes(df.fieldname)
      ) {
        df.read_only = editable_fields[df.fieldname] ? 0 : 1;
      }
    });
  }

  // Method 2: Update visible grid rows (CRITICAL for making fields actually editable)
  if (frm.doc.items && grid.grid_rows) {
    frm.doc.items.forEach(function (item, idx) {
      let grid_row = grid.grid_rows[idx];
      if (grid_row) {
        // Update docfields in the row
        if (grid_row.docfields) {
          grid_row.docfields.forEach(function (df) {
            if (
              ["item_code", "requested_qty", "accepted_qty", "shipped_qty", "received_qty", "requester_notes", "manager_notes"].includes(
                df.fieldname
              )
            ) {
              df.read_only = editable_fields[df.fieldname] ? 0 : 1;
            }
          });
        }

        // CRITICAL: Update the actual field instances in the grid row
        ["item_code", "requested_qty", "accepted_qty", "shipped_qty", "received_qty", "requester_notes", "manager_notes"].forEach(function (
          fieldname
        ) {
          if (grid_row.fields_dict && grid_row.fields_dict[fieldname]) {
            grid_row.fields_dict[fieldname].df.read_only = editable_fields[
              fieldname
            ]
              ? 0
              : 1;

            // Force refresh of the specific field
            if (grid_row.fields_dict[fieldname].refresh) {
              grid_row.fields_dict[fieldname].refresh();
            }
          }
        });

        // Refresh the entire row
        grid_row.refresh();
      }
    });
  }

  // Refresh the grid to apply changes
  frm.refresh_field("items");

  // CRITICAL: Ensure grid is in editable mode
  if (grid && !grid.grid_rows_by_docname) {
    grid.grid_rows_by_docname = {};
  }

  // Make sure grid allows editing
  if (grid) {
    // Check if any field should be editable
    let has_editable_fields =
      editable_fields.item_code ||
      editable_fields.requested_qty ||
      editable_fields.accepted_qty ||
      editable_fields.shipped_qty ||
      editable_fields.received_qty ||
      editable_fields.requester_notes ||
      editable_fields.manager_notes;

    if (has_editable_fields) {
      // Ensure grid can be edited (not read-only at grid level)
      if (grid.df) {
        grid.df.read_only = 0;
      }

      // Ensure the grid wrapper is not disabled
      if (grid.wrapper) {
        grid.wrapper.find(".grid-body").removeClass("grid-readonly");
      }
    }
  }

  // CRITICAL: Force re-render of grid after a short delay
  setTimeout(function () {
    if (frm.fields_dict.items && frm.fields_dict.items.grid) {
      frm.fields_dict.items.grid.refresh();
    }
  }, 100);
}

function update_transfer_type_readonly(frm) {
  // Transfer Type is editable in Draft, locked after submission
  let is_draft = frm.doc.approval_status === "Draft";
  frm.set_df_property("transfer_type", "read_only", is_draft ? 0 : 1);
}

function setup_warehouse_filters(frm) {
  // Define warehouse groups
  const GROUP_A = ["Zahran Main - EZ", "Damage - EZ", "Damage For Sale - EZ"];
  const GROUP_B = [
    "Store Warehouse - EZ",
    "Store Display - EZ",
    "Store Damage - EZ",
  ];
  const EXTERNAL_WAREHOUSES = ["Zahran Main - EZ", "Store Warehouse - EZ"];

  // Source Warehouse Filter
  frm.set_query("source_warehouse", function () {
    let allowed_warehouses = [];

    if (!frm.doc.transfer_type) {
      // If no transfer type selected, show all leaf warehouses except Hold
      return {
        filters: {
          is_group: 0,
          disabled: 0,
          name: ["not in", ["Hold (Reserved / Pending Shipment) - EZ"]],
        },
      };
    } else if (frm.doc.transfer_type === "Internal Transfer") {
      // Internal: Show Group A + Group B warehouses (exclude Hold)
      allowed_warehouses = [...GROUP_A, ...GROUP_B];
    } else if (frm.doc.transfer_type === "External Transfer") {
      // External: Show only Zahran Main and Store Warehouse
      allowed_warehouses = [...EXTERNAL_WAREHOUSES];
    }

    // Exclude target warehouse if selected
    if (frm.doc.target_warehouse) {
      allowed_warehouses = allowed_warehouses.filter(
        (w) => w !== frm.doc.target_warehouse
      );
    }

    return {
      filters: {
        is_group: 0,
        disabled: 0,
        name: ["in", allowed_warehouses],
      },
    };
  });

  // Target Warehouse Filter
  frm.set_query("target_warehouse", function () {
    let allowed_warehouses = [];

    if (!frm.doc.transfer_type) {
      // If no transfer type selected, show all leaf warehouses except Hold
      return {
        filters: {
          is_group: 0,
          disabled: 0,
          name: ["not in", ["Hold (Reserved / Pending Shipment) - EZ"]],
        },
      };
    } else if (frm.doc.transfer_type === "Internal Transfer") {
      if (!frm.doc.source_warehouse) {
        // No source selected, show all Group A + Group B warehouses
        allowed_warehouses = [...GROUP_A, ...GROUP_B];
      } else {
        // Source selected, filter by group to show ONLY other warehouses from same group
        if (GROUP_A.includes(frm.doc.source_warehouse)) {
          // Source is Group A, show only OTHER Group A warehouses (exclude source)
          allowed_warehouses = GROUP_A.filter(
            (w) => w !== frm.doc.source_warehouse
          );
        } else if (GROUP_B.includes(frm.doc.source_warehouse)) {
          // Source is Group B, show only OTHER Group B warehouses (exclude source)
          allowed_warehouses = GROUP_B.filter(
            (w) => w !== frm.doc.source_warehouse
          );
        }
      }
    } else if (frm.doc.transfer_type === "External Transfer") {
      if (!frm.doc.source_warehouse) {
        // No source selected, show both external warehouses
        allowed_warehouses = [...EXTERNAL_WAREHOUSES];
      } else {
        // Source selected, show only the OTHER external warehouse
        allowed_warehouses = EXTERNAL_WAREHOUSES.filter(
          (w) => w !== frm.doc.source_warehouse
        );
      }
    }

    return {
      filters: {
        is_group: 0,
        disabled: 0,
        name: ["in", allowed_warehouses],
      },
    };
  });
}

function setup_item_filters(frm) {
  frm.set_query("item_code", "items", function () {
    if (!frm.doc.source_warehouse) {
      return { filters: { name: ["in", []] } };
    }
    return {
      query: "erpnext.controllers.queries.item_query",
      filters: { is_stock_item: 1 },
    };
  });
}

function update_field_visibility(frm) {
  let status = frm.doc.approval_status;

  // Toggle field visibility
  let show_approval = [
    "Approved - Pending Shipment",
    "Shipped",
    "Partially Completed",
    "Completed",
  ].includes(status);
  frm.toggle_display("approved_by", show_approval);
  frm.toggle_display("approval_date", show_approval);
  frm.toggle_display("rejection_reason", status === "Rejected");
  frm.toggle_display(
    "stock_entries",
    status === "Completed" || status === "Partially Completed"
  );

  // Lock ALL fields after submit for approval
  let locked_statuses = [
    "Pending Approval",
    "Approved - Pending Shipment",
    "Shipped",
    "Partially Completed",
    "Completed",
    "Rejected",
  ];
  let is_locked = locked_statuses.includes(status);

  // Lock main form fields
  frm.set_df_property("source_warehouse", "read_only", is_locked ? 1 : 0);
  frm.set_df_property("target_warehouse", "read_only", is_locked ? 1 : 0);
  frm.set_df_property("transfer_type", "read_only", is_locked ? 1 : 0); // Editable in Draft, locked after submission
  frm.set_df_property("remarks", "read_only", is_locked ? 1 : 0);
  frm.set_df_property("requested_date", "read_only", is_locked ? 1 : 0);

  // Lock items table completely
  if (frm.fields_dict["items"] && frm.fields_dict["items"].grid) {
    let grid = frm.fields_dict["items"].grid;

    if (is_locked) {
      // Prevent adding/removing rows
      grid.cannot_add_rows = true;
      grid.wrapper.find(".grid-add-row").hide();
      grid.wrapper.find(".grid-remove-rows").hide();

      // ⚠️ IMPORTANT: Do NOT lock the entire table with read_only
      // This would prevent inline editing of quantity fields
      // Instead, let control_quantity_field_editability() handle field-level locking
      // frm.set_df_property("items", "read_only", 1); // ❌ REMOVED

      // Note: item_code, requested_qty, and requester_notes are now handled by control_quantity_field_editability()
      // No need to lock them here separately
    } else {
      // Allow adding/removing rows
      grid.cannot_add_rows = false;
      grid.wrapper.find(".grid-add-row").show();
      grid.wrapper.find(".grid-remove-rows").show();

      // ⚠️ IMPORTANT: Do NOT set entire table to read_only: 0
      // Let control_quantity_field_editability() handle field-level permissions
      // frm.set_df_property("items", "read_only", 0); // ❌ REMOVED
    }

    grid.refresh();
  }

  // Status indicators
  let indicators = {
    "Pending Approval": ["Awaiting Approval", "orange"],
    "Approved - Pending Shipment": ["Approved - Ready to Ship", "green"],
    Shipped: ["Items Shipped - Awaiting Receipt", "blue"],
    "Partially Completed": ["Partially Received", "light-blue"],
    Completed: ["Transfer Completed", "darkgreen"],
    Rejected: ["Request Rejected", "red"],
  };

  if (indicators[status]) {
    frm.dashboard.add_indicator(
      __(indicators[status][0]),
      indicators[status][1]
    );
  }
}

function can_ship(frm) {
  let user_roles = frappe.user_roles;
  let source = frm.doc.source_warehouse || "";

  if (
    source.includes("Zahran") &&
    user_roles.includes("Zahran Warehouse Manager")
  ) {
    return true;
  }
  if (
    source.includes("Store") &&
    user_roles.includes("Store Warehouse Manager")
  ) {
    return true;
  }
  return false;
}

function can_receive(frm) {
  let user_roles = frappe.user_roles;
  let target = frm.doc.target_warehouse || "";

  if (
    target.includes("Zahran") &&
    user_roles.includes("Zahran Warehouse Manager")
  ) {
    return true;
  }
  if (
    target.includes("Store") &&
    user_roles.includes("Store Warehouse Manager")
  ) {
    return true;
  }
  return false;
}

// ============================================================
// WORKFLOW API CALLS
// ============================================================

function submit_for_approval(frm) {
  frappe.call({
    method: "electro_zone.electro_zone.doctype.warehouse_transfer_request.warehouse_transfer_request.submit_for_approval",
    args: { transfer_name: frm.doc.name },
    freeze: true,
    freeze_message: __("Submitting for approval..."),
    callback: function (r) {
      handle_api_response(r, frm);
    },
  });
}

function approve_transfer(frm) {
  // Read accepted quantities directly from items table (inline editing)
  save_and_approve(frm);
}

function reject_transfer(frm, reason) {
  frappe.call({
    method: "electro_zone.electro_zone.doctype.warehouse_transfer_request.warehouse_transfer_request.reject_transfer",
    args: {
      transfer_name: frm.doc.name,
      rejection_reason: reason,
    },
    freeze: true,
    freeze_message: __("Rejecting transfer..."),
    callback: function (r) {
      handle_api_response(r, frm);
    },
  });
}

function handle_api_response(r, frm) {
  if (r.message && r.message.success) {
    frappe.show_alert(
      {
        message: r.message.message,
        indicator: "green",
      },
      5
    );
    frm.reload_doc();
  } else {
    frappe.msgprint({
      title: __("Error"),
      message: r.message ? r.message.message : __("Unknown error"),
      indicator: "red",
    });
  }
}

// ============================================================
// INLINE EDITING FUNCTIONS (Replace Dialogs)
// ============================================================

// ============================================================
// APPROVAL: Save & Approve (External Transfer Manager)
// ============================================================

function save_and_approve(frm) {
  if (!frm.doc.items || frm.doc.items.length === 0) {
    frappe.msgprint({
      title: __("No Items"),
      message: __("No items found in this transfer request"),
      indicator: "orange",
    });
    return;
  }

  let accepted_items = [];
  let validation_errors = [];

  // Read accepted_qty directly from items table
  frm.doc.items.forEach(function (item) {
    let requested = item.requested_qty || 0;
    let accepted = item.accepted_qty || 0;

    // Validation 1: accepted_qty cannot be negative
    if (accepted < 0) {
      validation_errors.push(
        __("{0}: Accepted quantity cannot be negative", [item.item_code])
      );
      return;
    }

    // Validation 2: accepted_qty cannot exceed requested_qty
    if (accepted > requested) {
      validation_errors.push(
        __(
          "{0}: Accepted quantity ({1}) cannot exceed requested quantity ({2})",
          [item.item_code, accepted, requested]
        )
      );
      return;
    }

    // Add to list (0 qty means excluded)
    accepted_items.push({
      item_code: item.item_code,
      qty: accepted,
    });
  });

  // Show all validation errors at once
  if (validation_errors.length > 0) {
    frappe.msgprint({
      title: __("Validation Errors"),
      message: validation_errors.join("<br>"),
      indicator: "red",
    });
    return;
  }

  // Check if at least one item has accepted_qty > 0
  let has_accepted = accepted_items.some((item) => item.qty > 0);
  if (!has_accepted) {
    frappe.msgprint({
      title: __("No Items Accepted"),
      message: __(
        "Cannot approve: all items have been excluded (accepted_qty = 0). Please accept at least one item."
      ),
      indicator: "orange",
    });
    return;
  }

  // Save form first if dirty, then call API
  if (frm.is_dirty()) {
    frm.save().then(() => {
      call_approve_api(frm, accepted_items);
    });
  } else {
    call_approve_api(frm, accepted_items);
  }
}

function call_approve_api(frm, accepted_items) {
  frappe.call({
    method: "electro_zone.electro_zone.doctype.warehouse_transfer_request.warehouse_transfer_request.approve_transfer",
    args: {
      transfer_name: frm.doc.name,
      accepted_items: JSON.stringify(accepted_items),
    },
    freeze: true,
    freeze_message: __("Approving transfer..."),
    callback: function (r) {
      if (r.message && r.message.success) {
        frappe.show_alert(
          {
            message: __("Transfer approved successfully"),
            indicator: "green",
          },
          5
        );
        frm.reload_doc();
      } else {
        frappe.msgprint({
          title: __("Approval Failed"),
          message: r.message.message || __("Unknown error"),
          indicator: "red",
        });
      }
    },
  });
}

// ============================================================
// SHIPMENT: Save & Mark as Shipped (Source Warehouse Manager)
// ============================================================

function save_and_ship(frm) {
  if (!frm.doc.items || frm.doc.items.length === 0) {
    frappe.msgprint({
      title: __("No Items"),
      message: __("No items found in this transfer request"),
      indicator: "orange",
    });
    return;
  }

  // CRITICAL: Use the stored original quantities from form load
  // DO NOT save the form - the API will update quantities after validation
  let original_quantities_snapshot = {};

  if (frm.__original_quantities) {
    // Use the existing stored values
    original_quantities_snapshot = JSON.parse(
      JSON.stringify(frm.__original_quantities)
    );
  } else {
    // Fallback: Store current values as baseline
    frm.doc.items.forEach(function (item) {
      original_quantities_snapshot[item.name] = {
        shipped_qty: item.shipped_qty || 0,
        received_qty: item.received_qty || 0,
      };
    });
  }

  // DO NOT save form - process shipment directly with current inline values
  // The API will validate and update the database correctly
  process_shipment(frm, original_quantities_snapshot);
}

function process_shipment(frm, original_quantities_snapshot) {
  let shipped_items = [];
  let validation_errors = [];
  let has_unshipped = false;

  // Read shipped_qty directly from items table (now saved values)
  frm.doc.items.forEach(function (item) {
    let accepted = item.accepted_qty || 0;
    let new_shipped = item.shipped_qty || 0; // Current value after save
    let original_shipped =
      original_quantities_snapshot[item.name]?.shipped_qty || 0; // Use snapshot!
    let shipping_now = new_shipped - original_shipped; // Incremental qty being shipped

    // Skip items with accepted_qty = 0 (excluded)
    if (accepted === 0) {
      return;
    }

    let pending = accepted - original_shipped;

    // Track if there are items pending shipment
    if (pending > 0) {
      has_unshipped = true;
    }

    // Only validate/process items where shipped_qty increased
    if (shipping_now > 0) {
      // Validation 1: new shipped cannot exceed accepted
      if (new_shipped > accepted) {
        validation_errors.push(
          __(
            "{0}: Shipped quantity ({1}) cannot exceed accepted quantity ({2})",
            [item.item_code, new_shipped, accepted]
          )
        );
        return;
      }

      // Validation 2: shipping_now cannot exceed available stock
      if (shipping_now > (item.available_qty || 0)) {
        validation_errors.push(
          __("{0}: Shipping quantity ({1}) exceeds available stock ({2})", [
            item.item_code,
            shipping_now,
            item.available_qty || 0,
          ])
        );
        return;
      }

      // Add to shipping list (only the incremental quantity)
      shipped_items.push({
        item_code: item.item_code,
        qty: shipping_now,
      });
    }
  });

  // Show all validation errors at once
  if (validation_errors.length > 0) {
    frappe.msgprint({
      title: __("Validation Errors"),
      message: validation_errors.join("<br>"),
      indicator: "red",
    });
    return;
  }

  // Check if user selected any items to ship
  if (shipped_items.length === 0) {
    if (!has_unshipped) {
      frappe.msgprint({
        title: __("All Items Shipped"),
        message: __("All items have been fully shipped"),
        indicator: "blue",
      });
    } else {
      frappe.msgprint({
        title: __("No Items Selected"),
        message: __(
          "Please increase (Shipped Qty) for at least one item to ship"
        ),
        indicator: "orange",
      });
    }
    return;
  }

  // Call API with shipped items
  call_ship_api(frm, shipped_items);
}

function call_ship_api(frm, shipped_items) {
  frappe.call({
    method: "electro_zone.electro_zone.doctype.warehouse_transfer_request.warehouse_transfer_request.mark_as_shipped",
    args: {
      transfer_name: frm.doc.name,
      shipped_items: JSON.stringify(shipped_items),
    },
    freeze: true,
    freeze_message: __("Processing shipment..."),
    callback: function (r) {
      if (r.message && r.message.success) {
        frappe.show_alert(
          {
            message: __("Shipment confirmed successfully"),
            indicator: "green",
          },
          5
        );
        frm.reload_doc();
      } else {
        frappe.msgprint({
          title: __("Shipment Failed"),
          message: r.message.message || __("Unknown error"),
          indicator: "red",
        });
      }
    },
  });
}

// ============================================================
// RECEIPT: Save & Confirm Receipt (Destination Warehouse Manager)
// ============================================================

function save_and_receive(frm) {
  if (!frm.doc.items || frm.doc.items.length === 0) {
    frappe.msgprint({
      title: __("No Items"),
      message: __("No items found in this transfer request"),
      indicator: "orange",
    });
    return;
  }

  // CRITICAL: Use the stored original quantities from form load
  // DO NOT save the form - the API will update quantities after validation
  let original_quantities_snapshot = {};

  if (frm.__original_quantities) {
    // Use the existing stored values
    original_quantities_snapshot = JSON.parse(
      JSON.stringify(frm.__original_quantities)
    );
  } else {
    // Fallback: Store current values as baseline
    frm.doc.items.forEach(function (item) {
      original_quantities_snapshot[item.name] = {
        shipped_qty: item.shipped_qty || 0,
        received_qty: item.received_qty || 0,
      };
    });
  }

  // DO NOT save form - process receipt directly with current inline values
  // The API will validate and update the database correctly
  process_receipt(frm, original_quantities_snapshot);
}

function process_receipt(frm, original_quantities_snapshot) {
  let received_items = [];
  let validation_errors = [];
  let has_pending = false;

  // Read received_qty directly from items table (now saved values)
  frm.doc.items.forEach(function (item) {
    let shipped = item.shipped_qty || 0;
    let new_received = item.received_qty || 0; // Current value after save
    let original_received =
      original_quantities_snapshot[item.name]?.received_qty || 0; // Use snapshot!
    let receiving_now = new_received - original_received; // Incremental qty being received

    // Skip items with no shipment
    if (shipped === 0) {
      return;
    }

    let pending = shipped - original_received;

    // Track if there are items pending receipt
    if (pending > 0) {
      has_pending = true;
    }

    // Only validate/process items where received_qty increased
    if (receiving_now > 0) {
      // Validation 1: new received cannot exceed shipped
      if (new_received > shipped) {
        validation_errors.push(
          __(
            "{0}: Received quantity ({1}) cannot exceed shipped quantity ({2})",
            [item.item_code, new_received, shipped]
          )
        );
        return;
      }

      // Add to receiving list (only the incremental quantity)
      received_items.push({
        item_code: item.item_code,
        qty: receiving_now,
      });
    }
  });

  // Show all validation errors at once
  if (validation_errors.length > 0) {
    frappe.msgprint({
      title: __("Validation Errors"),
      message: validation_errors.join("<br>"),
      indicator: "red",
    });
    return;
  }

  // Check if user selected any items to receive
  if (received_items.length === 0) {
    if (!has_pending) {
      frappe.msgprint({
        title: __("All Items Received"),
        message: __("All items have been fully received"),
        indicator: "blue",
      });
    } else {
      frappe.msgprint({
        title: __("No Items Selected"),
        message: __(
          "Please increase (Received Qty) for at least one item to receive"
        ),
        indicator: "orange",
      });
    }
    return;
  }

  // Call API with received items
  call_receive_api(frm, received_items);
}

function call_receive_api(frm, received_items) {
  frappe.call({
    method: "electro_zone.electro_zone.doctype.warehouse_transfer_request.warehouse_transfer_request.confirm_receipt",
    args: {
      transfer_name: frm.doc.name,
      received_items: JSON.stringify(received_items),
    },
    freeze: true,
    freeze_message: __("Processing receipt..."),
    callback: function (r) {
      if (r.message && r.message.success) {
        frappe.show_alert(
          {
            message: __("Receipt confirmed successfully"),
            indicator: "green",
          },
          5
        );
        frm.reload_doc();
      } else {
        frappe.msgprint({
          title: __("Receipt Failed"),
          message: r.message.message || __("Unknown error"),
          indicator: "red",
        });
      }
    },
  });
}

// ============================================================================
// EXCEL BULK UPLOAD - COMPLETE CODE
// ============================================================================
// This section contains all Excel upload/download functionality:
// - download_excel_template(): Creates and downloads Excel template
// - upload_items_from_excel(): Reads Excel file and validates items
// - populate_items_table(): Populates items child table after validation
// - show_validation_errors(): Displays detailed error report
//
// Usage: Buttons are added in refresh event handler (see above)
// ============================================================================

/**
 * Generate and download Excel template for bulk item upload
 * Template includes descriptive headers with examples
 */
function download_excel_template(frm) {
  // Check if XLSX library is loaded
  if (!window.XLSX) {
    frappe.msgprint({
      title: __("Library Not Loaded"),
      message: __(
        "Excel library is still loading. Please try again in a moment."
      ),
      indicator: "orange",
    });
    return;
  }

  // Create workbook
  const wb = XLSX.utils.book_new();

  // Define template data with headers only (no sample data)
  // Headers include examples to guide users
  const template_data = [
    [
      "Item Code (mandatory) Example: EZ-103282-SA",
      "Requested Qty (mandatory) Example: 50",
      "Requester Notes (optional) Example: Need urgently",
    ], // Headers
  ];

  // Create worksheet from data
  const ws = XLSX.utils.aoa_to_sheet(template_data);

  // Set column widths (wider to accommodate descriptive headers)
  ws["!cols"] = [
    { wch: 45 }, // item_code column width (wider for header text)
    { wch: 45 }, // requested_qty column width (wider for header text)
    { wch: 55 }, // requester_notes column width
  ];

  // Add worksheet to workbook
  XLSX.utils.book_append_sheet(wb, ws, "Items");

  // Generate filename with timestamp
  const filename = `warehouse_transfer_items_template_${frappe.datetime
    .now_datetime()
    .replace(/[\s:]/g, "_")}.xlsx`;

  // Download file
  XLSX.writeFile(wb, filename);

  frappe.show_alert(
    {
      message: __("Template downloaded successfully"),
      indicator: "green",
    },
    3
  );
}

/**
 * Read Excel file and populate items table with validation
 */
function upload_items_from_excel(frm) {
  // Check if XLSX library is loaded
  if (!window.XLSX) {
    frappe.msgprint({
      title: __("Library Not Loaded"),
      message: __(
        "Excel library is still loading. Please try again in a moment."
      ),
      indicator: "orange",
    });
    return;
  }

  // Check if source_warehouse is set (needed for stock validation)
  if (!frm.doc.source_warehouse) {
    frappe.msgprint({
      title: __("Source Warehouse Required"),
      message: __("Please select a source warehouse before uploading items."),
      indicator: "orange",
    });
    return;
  }

  // Create file input element
  const file_input = document.createElement("input");
  file_input.type = "file";
  file_input.accept = ".xlsx, .xls";

  // Handle file selection
  file_input.onchange = function (e) {
    const file = e.target.files[0];
    if (!file) return;

    // Show loading indicator
    frappe.show_alert(
      {
        message: __("Reading Excel file..."),
        indicator: "blue",
      },
      5
    );

    // Read file
    const reader = new FileReader();
    reader.onload = function (e) {
      try {
        // Parse Excel file
        const data = new Uint8Array(e.target.result);
        const workbook = XLSX.read(data, { type: "array" });

        // Get first worksheet
        const first_sheet_name = workbook.SheetNames[0];
        const worksheet = workbook.Sheets[first_sheet_name];

        // Convert to JSON (first row as headers)
        const json_data = XLSX.utils.sheet_to_json(worksheet, {
          raw: false, // Get formatted values
          defval: null, // Default value for empty cells
        });

        // Validate Excel format
        if (json_data.length === 0) {
          frappe.msgprint({
            title: __("Invalid File"),
            message: __("Excel file is empty or has no data rows."),
            indicator: "red",
          });
          return;
        }

        // Get column headers from first row keys
        const first_row = json_data[0];
        const headers = Object.keys(first_row);

        // Find item_code and requested_qty columns (case-insensitive, partial match)
        // This allows flexible header names like "Item Code (mandatory) Example: EZ-103282-SA"
        const item_code_header = headers.find(
          (h) =>
            h.toLowerCase().includes("item") && h.toLowerCase().includes("code")
        );
        const requested_qty_header = headers.find(
          (h) =>
            h.toLowerCase().includes("requested") &&
            h.toLowerCase().includes("qty")
        );
        // Find requester_notes column (optional)
        const requester_notes_header = headers.find(
          (h) =>
            h.toLowerCase().includes("requester") &&
            h.toLowerCase().includes("notes")
        );

        // Check required columns exist
        if (!item_code_header || !requested_qty_header) {
          frappe.msgprint({
            title: __("Invalid Format"),
            message: __(
              'Excel file must have columns: "Item Code" and "Requested Qty"'
            ),
            indicator: "red",
          });
          return;
        }

        // Prepare items for validation using dynamically detected headers
        const items_to_validate = json_data
          .map((row) => ({
            item_code: (row[item_code_header] || "").toString().trim(),
            requested_qty: parseInt(row[requested_qty_header]) || 0,
            requester_notes: requester_notes_header
              ? (row[requester_notes_header] || "").toString().trim().substring(0, 55)
              : "",
          }))
          .filter((item) => item.item_code); // Remove empty rows

        if (items_to_validate.length === 0) {
          frappe.msgprint({
            title: __("No Items Found"),
            message: __("Excel file contains no valid item data."),
            indicator: "orange",
          });
          return;
        }

        // Call server API to validate items
        frappe.show_alert(
          {
            message: __("Validating {0} items...", [items_to_validate.length]),
            indicator: "blue",
          },
          5
        );

        frappe.call({
          method: "electro_zone.electro_zone.doctype.warehouse_transfer_request.warehouse_transfer_request.validate_items_for_upload",
          args: {
            items: JSON.stringify(items_to_validate),
            source_warehouse: frm.doc.source_warehouse,
            target_warehouse: frm.doc.target_warehouse,
          },
          freeze: true,
          freeze_message: __("Validating items..."),
          callback: function (r) {
            if (r.message && r.message.success) {
              // Validation passed - populate items table
              populate_items_table(frm, r.message.validated_items);
            } else if (r.message && r.message.errors) {
              // Validation failed - show errors
              show_validation_errors(r.message.errors);
            } else {
              frappe.msgprint({
                title: __("Validation Error"),
                message: __(
                  "An error occurred during validation. Please try again."
                ),
                indicator: "red",
              });
            }
          },
          error: function (r) {
            frappe.msgprint({
              title: __("API Error"),
              message: __(
                "Failed to validate items. Please check your connection and try again."
              ),
              indicator: "red",
            });
          },
        });
      } catch (error) {
        console.error("Excel parsing error:", error);
        frappe.msgprint({
          title: __("File Error"),
          message: __(
            "Failed to read Excel file. Please ensure it is a valid .xlsx file."
          ),
          indicator: "red",
        });
      }
    };

    reader.readAsArrayBuffer(file);
  };

  // Trigger file picker
  file_input.click();
}

/**
 * Populate items child table with validated items
 */
function populate_items_table(frm, validated_items) {
  // Clear existing items (REPLACE mode)
  frm.clear_table("items");

  // Add validated items
  validated_items.forEach((item) => {
    const row = frm.add_child("items");
    row.item_code = item.item_code;
    row.item_name = item.item_name;
    row.requested_qty = item.requested_qty;
    row.uom = item.uom;
    row.available_qty = item.available_qty;
    row.available_qty_target = item.available_qty_target;
    row.requester_notes = item.requester_notes || "";

    // Initialize workflow fields (same as manual item addition)
    row.shipped_qty = 0;
    row.received_qty = 0;

    // Auto-set accepted_qty = requested_qty (Draft status only)
    if (frm.doc.approval_status === "Draft") {
      row.accepted_qty = item.requested_qty;
    } else {
      row.accepted_qty = 0;
    }
  });

  // Refresh items table
  frm.refresh_field("items");

  // Apply field editability controls after upload
  control_quantity_field_editability(frm);

  // Show success message
  frappe.show_alert(
    {
      message: __("Successfully added {0} items from Excel", [
        validated_items.length,
      ]),
      indicator: "green",
    },
    5
  );

  frappe.msgprint({
    title: __("Upload Complete"),
    message: __(
      "{0} items have been added to the table. Please review and save the form.",
      [validated_items.length]
    ),
    indicator: "green",
  });
}

/**
 * Display validation errors in a formatted dialog
 */
function show_validation_errors(errors) {
  let error_html = '<div style="max-height: 400px; overflow-y: auto;">';
  error_html += "<p><strong>The following errors were found:</strong></p>";
  error_html += '<ul style="color: red; line-height: 1.8;">';

  errors.forEach((error) => {
    error_html = error_html + "<li>" + error + "</li>";
  });

  error_html = error_html + "</ul>";
  error_html =
    error_html +
    "<p><em>Please correct these errors in your Excel file and try again.</em></p>";
  error_html = error_html + "</div>";

  frappe.msgprint({
    title: __("Validation Failed"),
    message: error_html,
    indicator: "red",
    primary_action: {
      label: __("Download Template"),
      action: function () {
        download_excel_template(cur_frm);
      },
    },
  });
}

// ============================================================================
// END OF EXCEL BULK UPLOAD CODE
// ============================================================================

// ============================================================================
// BARCODE SCANNING: Shipment Process (Source Warehouse Manager)
// ============================================================================
// This section handles barcode scanning for the shipment process when
// marking items as shipped. Only active in "Approved - Pending Shipment" status.
// Implementation: Phase 11.10 - Barcode Shipment
// Note: The 'barcode' field in child table uses "Fetch From" - no client script needed
// ============================================================================

frappe.ui.form.on("Warehouse Transfer Request", {
  custom_scan_barcode: function(frm) {
    let scanned_barcode = (frm.doc.custom_scan_barcode || '').trim();

    if (!scanned_barcode) {
      return;
    }

    // Clear the barcode field immediately
    frm.set_value('custom_scan_barcode', '');

    let status = frm.doc.approval_status;

    // Determine which operation to perform based on status
    let operation_mode = null;

    if (status === "Draft") {
      operation_mode = "add_item";
    } else if (status === "Approved - Pending Shipment" || status === "Partially Shipped") {
      operation_mode = "ship_item";
    } else if (status === "Shipped" || status === "Partially Completed") {
      operation_mode = "receive_item";
    } else {
      frappe.show_alert({
        message: __('⚠️ Barcode scanning not available in "{0}" status', [status]),
        indicator: 'yellow'
      }, 4);
      return;
    }

    // For add_item mode, check if warehouses are set
    if (operation_mode === "add_item") {
      if (!frm.doc.source_warehouse || !frm.doc.target_warehouse) {
        frappe.show_alert({
          message: __('⚠️ Please select Source and Target warehouses first'),
          indicator: 'yellow'
        }, 4);
        return;
      }
    }

    // Search for item by barcode using server API (bypasses permissions)
    frappe.call({
      method: 'electro_zone.electro_zone.doctype.warehouse_transfer_request.warehouse_transfer_request.get_item_by_barcode',
      args: {
        barcode: scanned_barcode
      },
      callback: function(r) {
        if (!r.message || !r.message.success) {
          frappe.show_alert({
            message: __('❌ {0}', [r.message ? r.message.error : 'Barcode not found']),
            indicator: 'red'
          }, 5);
          return;
        }

        let item_code = r.message.item_code;

        // Handle based on operation mode
        if (operation_mode === "add_item") {
          handle_add_item_by_barcode(frm, item_code, scanned_barcode);
        } else if (operation_mode === "ship_item") {
          handle_ship_item_by_barcode(frm, item_code, scanned_barcode);
        } else if (operation_mode === "receive_item") {
          handle_receive_item_by_barcode(frm, item_code, scanned_barcode);
        }
      },
      error: function(err) {
        frappe.show_alert({
          message: __('❌ Error searching barcode: {0}', [err.message || 'Unknown error']),
          indicator: 'red'
        }, 5);
      }
    });
  }
});

// ============================================================================
// BARCODE HANDLER: Add Item (Draft status)
// ============================================================================
function handle_add_item_by_barcode(frm, item_code, barcode) {
  // Check if item already exists in table
  let existing_item = null;
  frm.doc.items.forEach(function(row) {
    if (row.item_code === item_code) {
      existing_item = row;
    }
  });

  if (existing_item) {
    // Item exists: increment requested_qty by 1
    let new_qty = (existing_item.requested_qty || 0) + 1;

    // Check against available stock
    if (existing_item.available_qty && new_qty > existing_item.available_qty) {
      frappe.show_alert({
        message: __('❌ Max stock reached! {0} | Available: {1}', [item_code, existing_item.available_qty]),
        indicator: 'red'
      }, 5);
      return;
    }

    frappe.model.set_value(existing_item.doctype, existing_item.name, 'requested_qty', new_qty);

    // Auto-update accepted_qty in Draft status
    if (frm.doc.approval_status === "Draft") {
      frappe.model.set_value(existing_item.doctype, existing_item.name, 'accepted_qty', new_qty);
    }

    frappe.show_alert({
      message: __('✅ {0} --> +1 (total: {1})', [item_code, new_qty]),
      indicator: 'green'
    }, 3);

    // Scroll to the row
    setTimeout(() => {
      let qty_field = $(`[data-name="${existing_item.name}"] [data-fieldname="requested_qty"] input`);
      if (qty_field.length) {
        qty_field[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
      }
    }, 100);

    frm.refresh_field('items');
  } else {
    // Item doesn't exist: add new row
    // First fetch item details
    frappe.call({
      method: 'frappe.client.get',
      args: {
        doctype: 'Item',
        name: item_code
      },
      callback: function(r) {
        if (!r.message) {
          frappe.show_alert({
            message: __('❌ Item {0} not found', [item_code]),
            indicator: 'red'
          }, 5);
          return;
        }

        let item = r.message;

        // Check stock in source warehouse
        frappe.call({
          method: 'frappe.client.get_value',
          args: {
            doctype: 'Bin',
            filters: {
              item_code: item_code,
              warehouse: frm.doc.source_warehouse
            },
            fieldname: 'actual_qty'
          },
          callback: function(bin_r) {
            let available_qty = bin_r.message && bin_r.message.actual_qty ? bin_r.message.actual_qty : 0;

            if (available_qty <= 0) {
              frappe.show_alert({
                message: __('❌ No stock available for {0} in {1}', [item_code, frm.doc.source_warehouse]),
                indicator: 'red'
              }, 5);
              return;
            }

            // Fetch target warehouse stock
            frappe.call({
              method: 'frappe.client.get_value',
              args: {
                doctype: 'Bin',
                filters: {
                  item_code: item_code,
                  warehouse: frm.doc.target_warehouse
                },
                fieldname: 'actual_qty'
              },
              callback: function(target_bin_r) {
                let available_qty_target = target_bin_r.message && target_bin_r.message.actual_qty ? target_bin_r.message.actual_qty : 0;

                // Add new row
                let new_row = frm.add_child('items');
                new_row.item_code = item_code;
                new_row.item_name = item.item_name;
                new_row.requested_qty = 1;
                new_row.uom = item.stock_uom;
                new_row.available_qty = available_qty;
                new_row.available_qty_target = available_qty_target;
                new_row.shipped_qty = 0;
                new_row.received_qty = 0;

                // Auto-set accepted_qty in Draft
                if (frm.doc.approval_status === "Draft") {
                  new_row.accepted_qty = 1;
                }

                frm.refresh_field('items');

                frappe.show_alert({
                  message: __('✅ Added {0} (qty: 1)', [item_code]),
                  indicator: 'green'
                }, 3);

                // Scroll to the new row
                setTimeout(() => {
                  let qty_field = $(`[data-name="${new_row.name}"] [data-fieldname="requested_qty"] input`);
                  if (qty_field.length) {
                    qty_field[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
                  }
                }, 100);
              }
            });
          }
        });
      }
    });
  }
}

// ============================================================================
// BARCODE HANDLER: Ship Item (Approved - Pending Shipment / Partially Shipped)
// ============================================================================
function handle_ship_item_by_barcode(frm, item_code, barcode) {
  // Validation: Must have items
  if (!frm.doc.items || frm.doc.items.length === 0) {
    frappe.show_alert({
      message: __('⚠️ No items in transfer request'),
      indicator: 'yellow'
    }, 4);
    return;
  }

  // Find the corresponding row in transfer items table
  let item_row = null;

  frm.doc.items.forEach(function(row) {
    if (row.item_code === item_code) {
      item_row = row;
    }
  });

  if (!item_row) {
    frappe.show_alert({
      message: __('❌ Item {0} not in this transfer request', [item_code]),
      indicator: 'red'
    }, 5);
    return;
  }

  // Check if item has accepted_qty > 0 (not excluded)
  if ((item_row.accepted_qty || 0) === 0) {
    frappe.show_alert({
      message: __('❌ Item {0} was excluded (accepted_qty = 0)', [item_code]),
      indicator: 'red'
    }, 5);
    return;
  }

  let current_shipped = item_row.shipped_qty || 0;
  let accepted_qty = item_row.accepted_qty || 0;
  let new_shipped = current_shipped + 1;

  // Validate against accepted_qty
  if (new_shipped > accepted_qty) {
    frappe.show_alert({
      message: __('❌ Max reached! {0} | Limit: {1}', [item_code, accepted_qty]),
      indicator: 'red'
    }, 5);
    return;
  }

  // Increment shipped_qty by +1
  frappe.model.set_value(item_row.doctype, item_row.name, 'shipped_qty', new_shipped);

  // Show success message
  frappe.show_alert({
    message: __('✅ Shipped {0} --> +1 (total: {1}/{2})', [item_code, new_shipped, accepted_qty]),
    indicator: 'green'
  }, 3);

  // Focus on shipped_qty field and scroll to it
  setTimeout(() => {
    let qty_field = $(`[data-name="${item_row.name}"] [data-fieldname="shipped_qty"] input`);
    if (qty_field.length) {
      qty_field.focus();
      qty_field[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, 100);

  // Refresh grid to show updated quantity
  frm.refresh_field('items');
}

// ============================================================================
// BARCODE HANDLER: Receive Item (Shipped / Partially Completed)
// ============================================================================
function handle_receive_item_by_barcode(frm, item_code, barcode) {
  // Validation: Must have items
  if (!frm.doc.items || frm.doc.items.length === 0) {
    frappe.show_alert({
      message: __('⚠️ No items in transfer request'),
      indicator: 'yellow'
    }, 4);
    return;
  }

  // Find the corresponding row in transfer items table
  let item_row = null;

  frm.doc.items.forEach(function(row) {
    if (row.item_code === item_code) {
      item_row = row;
    }
  });

  if (!item_row) {
    frappe.show_alert({
      message: __('❌ Item {0} not in this transfer request', [item_code]),
      indicator: 'red'
    }, 5);
    return;
  }

  // Check if item has been shipped
  let shipped_qty = item_row.shipped_qty || 0;
  if (shipped_qty === 0) {
    frappe.show_alert({
      message: __('❌ Item {0} has not been shipped yet', [item_code]),
      indicator: 'red'
    }, 5);
    return;
  }

  let current_received = item_row.received_qty || 0;
  let new_received = current_received + 1;

  // Validate against shipped_qty
  if (new_received > shipped_qty) {
    frappe.show_alert({
      message: __('❌ Max reached! {0} | Shipped: {1}', [item_code, shipped_qty]),
      indicator: 'red'
    }, 5);
    return;
  }

  // Increment received_qty by +1
  frappe.model.set_value(item_row.doctype, item_row.name, 'received_qty', new_received);

  // Show success message
  frappe.show_alert({
    message: __('✅ Received {0} --> +1 (total: {1}/{2})', [item_code, new_received, shipped_qty]),
    indicator: 'green'
  }, 3);

  // Focus on received_qty field and scroll to it
  setTimeout(() => {
    let qty_field = $(`[data-name="${item_row.name}"] [data-fieldname="received_qty"] input`);
    if (qty_field.length) {
      qty_field.focus();
      qty_field[0].scrollIntoView({ behavior: 'smooth', block: 'center' });
    }
  }, 100);

  // Refresh grid to show updated quantity
  frm.refresh_field('items');
}

// ============================================================================
// END OF BARCODE SCANNING CODE
// ============================================================================
