// Copyright (c) 2025, didy1234567@gmail.com and contributors
// For license information, please see license.txt

// Item Price List History - Complete Form Control
frappe.ui.form.on("Item Price List History", {
  refresh(frm) {
    // Get current user roles
    let user_roles = frappe.user_roles;
    let is_stock_user = user_roles.includes("Stock User");
    let is_accountant =
      user_roles.includes("Accounts User") ||
      user_roles.includes("Accounts Manager");
    let is_accountant_manager = user_roles.includes("Accounts Manager");

    // Apply field visibility rules (updated for manager correction mode)
    apply_field_visibility(frm);

    // Lock date field if either role has submitted
    if (frm.doc.stock_submitted || frm.doc.accountant_submitted) {
      frm.set_df_property("date", "read_only", 1);
    }

    // Hide standard buttons for Stock User
    if (is_stock_user && !is_accountant) {
      // Hide Save button completely
      frm.page.clear_primary_action();
      frm.page.clear_secondary_action();
    }

    // Only show buttons for Draft documents
    if (frm.doc.docstatus === 0) {
      // Stock Submit button - Only for Stock Users, only if not yet submitted
      // Appears directly on form (PRIMARY BUTTON - not in dropdown)
      if (
        is_stock_user &&
        !frm.doc.stock_submitted &&
        !frm.doc.needs_manager_correction
      ) {
        frm.page.set_primary_action(__("Stock Submit"), () => {
          stock_submit(frm);
        });
      }

      // Accountant workflow buttons - PRIORITY ORDER MATTERS!
      // Priority 1: Accountant Submit (if not yet submitted)
      // Priority 2: Revalidate & Submit (if in manager correction mode - Manager only)
      // Priority 3: Final Submit (if matched)
      // Priority 4: Refuse Submit (if not matched - Manager only)

      if (is_accountant) {
        // Priority 1: Accountant Submit button - Show if accountant has NOT submitted yet
        // NOTE: No longer requires Stock User to submit first - independent submissions
        if (!frm.doc.accountant_submitted) {
          frm.page.set_primary_action(__("Accountant Submit"), () => {
            accountant_submit(frm);
          });
        }
        // Priority 2: Revalidate & Submit - Show if in manager correction mode (Manager only)
        else if (
          is_accountant_manager &&
          frm.doc.needs_manager_correction &&
          frm.doc.stock_submitted &&
          frm.doc.accountant_submitted
        ) {
          frm.page.set_primary_action(__("Revalidate & Submit"), () => {
            manager_revalidate(frm);
          });
        }
        // Priority 3: Final Submit button - Show ONLY if BOTH submitted AND matched AND NOT in correction mode
        else if (
          frm.doc.accountant_submitted &&
          frm.doc.stock_submitted &&
          frm.doc.match_status === "Matched" &&
          !frm.doc.needs_manager_correction
        ) {
          frm.page.set_primary_action(__("Final Submit"), () => {
            frappe.confirm(
              __(
                "Are you sure you want to perform Final Submit?<br>This will update the Item Price if this is the latest date."
              ),
              () => {
                // Save first if dirty
                let submit_action = () => {
                  // Override the standard confirm_dialog to prevent double confirmation
                  let original_confirm = frappe.ui.form.Form.prototype.savesubmit;

                  frappe.ui.form.Form.prototype.savesubmit = function() {
                    // Directly submit without confirmation
                    this.save('Submit', null, this);
                  };

                  // Call savesubmit
                  frm.savesubmit();

                  // Restore original function after a delay
                  setTimeout(() => {
                    frappe.ui.form.Form.prototype.savesubmit = original_confirm;
                  }, 500);
                };

                // Save first if needed
                if (frm.is_dirty()) {
                  frm.save().then(() => {
                    submit_action();
                  });
                } else {
                  submit_action();
                }
              }
            );
          });
        }
      }

      // Refuse Submit button - Only for Accounts Manager, only if not matched, NOT in correction mode
      // NEW v6.1: Standalone button (not in Actions dropdown)
      if (
        is_accountant_manager &&
        frm.doc.stock_submitted &&
        frm.doc.accountant_submitted &&
        frm.doc.match_status === "Not Matched" &&
        !frm.doc.needs_manager_correction
      ) {
        frm
          .add_custom_button(__("Refuse Submit"), function () {
            refuse_submit(frm);
          })
          .addClass("btn-danger");
      }
    }

    // Disable all actions for Stock User after submission
    if (is_stock_user && !is_accountant && frm.doc.stock_submitted) {
      frm.disable_form();
      frappe.show_alert({
        message: __(
          "Your submission is complete. Waiting for Accountant review."
        ),
        indicator: "blue",
      });
    }

    // Disable form for Accountant (non-manager) if in manager correction mode
    if (
      is_accountant &&
      !is_accountant_manager &&
      frm.doc.needs_manager_correction
    ) {
      frm.disable_form();
      frappe.show_alert({
        message: __(
          "Document is in Account Manager correction mode. Only Account Manager can edit."
        ),
        indicator: "orange",
      });
    }

    // Set indicator colors for match_status
    if (frm.doc.match_status === "Matched") {
      frm.set_df_property("match_status", "label_style", "success");
    } else if (frm.doc.match_status === "Not Matched") {
      frm.set_df_property("match_status", "label_style", "danger");
    }

    // Set indicator color for comparison_status
    if (frm.doc.comparison_status === "Pending Account Manager Correction") {
      frm.set_df_property("comparison_status", "label_style", "warning");
    }
  },

  onload(frm) {
    // Initialize status on new documents
    if (frm.is_new()) {
      frm.set_value("comparison_status", "Draft");
    }
  },

  item_code(frm) {
    // Auto-fetch Item Group and Brand when Item Code is selected
    if (frm.doc.item_code) {
      frappe.call({
        method: "frappe.client.get",
        args: {
          doctype: "Item",
          name: frm.doc.item_code,
        },
        callback: function (r) {
          if (r.message) {
            // Populate both Stock User and Accountant fields with same data
            frm.set_value("stock_item_group", r.message.item_group);
            frm.set_value("stock_brand", r.message.brand);
            frm.set_value("account_item_group", r.message.item_group);
            frm.set_value("account_brand", r.message.brand);

            // PERMANENTLY lock ONLY Item Group and Brand fields (4 fields)
            // Price fields remain controlled by apply_field_visibility
            frm.set_df_property("stock_item_group", "read_only", 1);
            frm.set_df_property("stock_brand", "read_only", 1);
            frm.set_df_property("account_item_group", "read_only", 1);
            frm.set_df_property("account_brand", "read_only", 1);

            frappe.show_alert({
              message: __("Item Group and Brand auto-filled and locked"),
              indicator: "green",
            });
          }
        },
      });
    } else {
      // If item_code is cleared, unlock ONLY the Item Group and Brand fields
      frm.set_df_property("stock_item_group", "read_only", 0);
      frm.set_df_property("stock_brand", "read_only", 0);
      frm.set_df_property("account_item_group", "read_only", 0);
      frm.set_df_property("account_brand", "read_only", 0);
    }
  },
});

function stock_submit(frm) {
  // Validate required fields before API call
  let required_fields = [
    { field: "item_code", label: "Item Code" },
    { field: "date", label: "Date" },
    { field: "stock_price_list", label: "Price List" },
  ];

  let missing = [];
  required_fields.forEach((f) => {
    if (!frm.doc[f.field]) {
      missing.push(f.label);
    }
  });

  if (missing.length > 0) {
    frappe.msgprint({
      title: __("Required Fields Missing"),
      message: __("Please fill: ") + missing.join(", "),
      indicator: "red",
    });
    return;
  }

  // Save the document first, then call API
  let submit_action = () => {
    frappe.call({
      method: "electro_zone.electro_zone.doctype.item_price_list_history.item_price_list_history.stock_submit_price_history",
      args: {
        name: frm.doc.name,
      },
      callback: function (r) {
        if (r.message && r.message.success) {
          // Check if auto-submitted
          if (r.message.auto_submitted) {
            frappe.show_alert({
              message: __(
                "✅ Stock data submitted and ALL FIELDS MATCHED! Document automatically submitted permanently."
              ),
              indicator: "green",
            });
          } else {
            frappe.show_alert({
              message: __(r.message.message),
              indicator:
                r.message.match_status === "Not Matched" ? "orange" : "green",
            });
          }
          frm.reload_doc();
        }
      },
    });
  };

  // If new document or has unsaved changes, save first
  if (frm.is_new() || frm.is_dirty()) {
    frm.save().then(() => {
      submit_action();
    });
  } else {
    submit_action();
  }
}

function accountant_submit(frm) {
  // Validate required fields before API call
  let required_fields = [{ field: "account_price_list", label: "Price List" }];

  let missing = [];
  required_fields.forEach((f) => {
    if (!frm.doc[f.field]) {
      missing.push(f.label);
    }
  });

  if (missing.length > 0) {
    frappe.msgprint({
      title: __("Required Fields Missing"),
      message: __("Please fill: ") + missing.join(", "),
      indicator: "red",
    });
    return;
  }

  // Save the document first, then call API
  let submit_action = () => {
    frappe.call({
      method: "electro_zone.electro_zone.doctype.item_price_list_history.item_price_list_history.accountant_submit_price_history",
      args: {
        name: frm.doc.name,
      },
      callback: function (r) {
        if (r.message && r.message.success) {
          // Check if auto-submitted
          if (r.message.auto_submitted) {
            frappe.show_alert({
              message: __(
                "✅ Accountant data submitted and ALL FIELDS MATCHED! Document automatically submitted permanently."
              ),
              indicator: "green",
            });
          } else {
            let msg = r.message.message;
            let indicator =
              r.message.match_status === "Not Matched" ? "orange" : "green";

            frappe.show_alert({
              message: __(msg),
              indicator: indicator,
            });
          }
          frm.reload_doc();
        }
      },
    });
  };

  // If new document or has unsaved changes, save first
  if (frm.is_new() || frm.is_dirty()) {
    frm.save().then(() => {
      submit_action();
    });
  } else {
    submit_action();
  }
}

function refuse_submit(frm) {
  frappe.confirm(
    __(
      "Are you sure you want to refuse this submission? <br><br>" +
        "✅ Stock User and Accountant fields are already locked<br>" +
        "🧩 Only you (Account Manager) can edit both to fix mismatches<br>" +
        "🔄 After fixing, click <b>'Revalidate & Submit'</b> to check again"
    ),
    () => {
      // Call API
      frappe.call({
        method: "electro_zone.electro_zone.doctype.item_price_list_history.item_price_list_history.refuse_submit_price_history",
        args: {
          name: frm.doc.name,
        },
        callback: function (r) {
          if (r.message && r.message.success) {
            frappe.show_alert({
              message: r.message.message,
              indicator: "orange",
            });
            frm.reload_doc();
          }
        },
      });
    }
  );
}

function manager_revalidate(frm) {
  // Save the document first if dirty, then call API
  let revalidate_action = () => {
    frappe.call({
      method: "electro_zone.electro_zone.doctype.item_price_list_history.item_price_list_history.manager_revalidate_price_history",
      args: {
        name: frm.doc.name,
      },
      callback: function (r) {
        if (r.message && r.message.success) {
          let indicator = r.message.all_match ? "green" : "orange";

          frappe.show_alert({
            message: __(r.message.message),
            indicator: indicator,
          });
          frm.reload_doc();
        }
      },
    });
  };

  // If document has unsaved changes, save first
  if (frm.is_dirty()) {
    frm.save().then(() => {
      revalidate_action();
    });
  } else {
    revalidate_action();
  }
}

function apply_field_visibility(frm) {
  let user_roles = frappe.user_roles;
  let is_stock_user = user_roles.includes("Stock User");
  let is_accountant =
    user_roles.includes("Accounts User") ||
    user_roles.includes("Accounts Manager");
  let is_accountant_manager = user_roles.includes("Accounts Manager");

  let is_final_submitted = frm.doc.docstatus === 1;
  let in_manager_correction = frm.doc.needs_manager_correction === 1;

  // Permanently locked fields (controlled by item_code trigger, NEVER editable)
  let permanently_locked_fields = [
    "stock_item_group",
    "stock_brand",
    "account_item_group",
    "account_brand",
  ];

  // Workflow-controlled price fields (editable based on submission status)
  let stock_price_fields = [
    "stock_price_list",
    "stock_promo",
    "stock_sellout_promo",
    "stock_rrp",
    "stock_final_price_list",
  ];

  let account_price_fields = [
    "account_price_list",
    "account_promo",
    "account_sellout_promo",
    "account_rrp",
    "account_final_price_list",
  ];

  // Combined lists for visibility control
  let stock_fields = [
    ...permanently_locked_fields.slice(0, 2),
    ...stock_price_fields,
  ];
  let account_fields = [
    ...permanently_locked_fields.slice(2, 4),
    ...account_price_fields,
  ];

  // If final submitted, show everything to everyone (but permanently locked fields stay read-only)
  if (is_final_submitted) {
    stock_fields.forEach((field) => {
      frm.set_df_property(field, "hidden", 0);
    });
    account_fields.forEach((field) => {
      frm.set_df_property(field, "hidden", 0);
    });
    return;
  }

  // Manager Correction Mode - Only PRICE fields editable
  if (in_manager_correction) {
    // Show all fields
    stock_fields.forEach((field) => {
      frm.set_df_property(field, "hidden", 0);
    });
    account_fields.forEach((field) => {
      frm.set_df_property(field, "hidden", 0);
    });

    // Make ONLY price fields editable for Account Manager
    stock_price_fields.forEach((field) => {
      frm.set_df_property(field, "read_only", is_accountant_manager ? 0 : 1);
    });
    account_price_fields.forEach((field) => {
      frm.set_df_property(field, "read_only", is_accountant_manager ? 0 : 1);
    });

    // Keep permanently locked fields read-only (even for Manager)
    permanently_locked_fields.forEach((field) => {
      frm.set_df_property(field, "read_only", 1);
    });

    // Show comparison section in correction mode
    frm.set_df_property("comparison_section", "hidden", 0);
    return;
  }

  // Normal Workflow (NOT in manager correction mode)

  // Stock User visibility rules
  if (is_stock_user && !is_accountant) {
    // Hide account fields (blind entry)
    account_fields.forEach((field) => {
      frm.set_df_property(field, "hidden", 1);
    });

    // Make ONLY price fields read-only if submitted
    stock_price_fields.forEach((field) => {
      frm.set_df_property(field, "read_only", frm.doc.stock_submitted ? 1 : 0);
    });
  }

  // Accountant visibility rules
  if (is_accountant && !is_stock_user) {
    // Hide stock fields (blind entry)
    stock_fields.forEach((field) => {
      frm.set_df_property(field, "hidden", 1);
    });

    // Make ONLY price fields read-only if submitted
    account_price_fields.forEach((field) => {
      frm.set_df_property(
        field,
        "read_only",
        frm.doc.accountant_submitted ? 1 : 0
      );
    });
  }

  // If user has both roles, show everything but respect read-only
  if (is_stock_user && is_accountant) {
    stock_fields.forEach((field) => {
      frm.set_df_property(field, "hidden", 0);
    });
    account_fields.forEach((field) => {
      frm.set_df_property(field, "hidden", 0);
    });

    // Make ONLY price fields read-only based on submission
    stock_price_fields.forEach((field) => {
      frm.set_df_property(field, "read_only", frm.doc.stock_submitted ? 1 : 0);
    });
    account_price_fields.forEach((field) => {
      frm.set_df_property(
        field,
        "read_only",
        frm.doc.accountant_submitted ? 1 : 0
      );
    });
  }

  // Hide comparison section until both submitted
  let show_comparison = frm.doc.stock_submitted && frm.doc.accountant_submitted;
  frm.set_df_property("comparison_section", "hidden", !show_comparison);
}
