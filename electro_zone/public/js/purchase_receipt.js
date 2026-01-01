// Purchase Receipt - Barcode Scan Handler (With Server API)
// Calls Server Script API to get PO ordered qty (bypasses Stock User permissions)
// IMPORTANT: Completely overrides ERPNext's default barcode behavior
// BLOCKS adding new rows if barcode not found in PR items table

frappe.ui.form.on("Purchase Receipt", {
  onload_post_render: function (frm) {
    // Block ERPNext's default barcode scan behavior completely
    // This prevents ERPNext from auto-adding new rows when scanning unknown barcodes
    if (frm.fields_dict.scan_barcode) {
      // Override the default barcode scanner handler
      frm.fields_dict.scan_barcode.df.onchange = null;
    }
  },

  scan_barcode: function (frm) {
    let scanned_barcode = (frm.doc.scan_barcode || "").trim();
    if (!scanned_barcode) return;

    // CRITICAL: Prevent ERPNext default behavior
    // Stop event propagation immediately
    frm.doc.scan_barcode = "";
    frm.refresh_field("scan_barcode");

    // Validation: Document must be Draft
    if (frm.doc.docstatus !== 0) {
      frappe.show_alert(
        {
          message: __("⚠️ Cannot scan on submitted document"),
          indicator: "yellow",
        },
        4
      );
      return;
    }

    // Validation: Must have items
    if (!frm.doc.items || frm.doc.items.length === 0) {
      frappe.show_alert(
        {
          message: __(
            "⚠️ No items in PR. Create PR from Purchase Order first."
          ),
          indicator: "yellow",
        },
        4
      );
      return;
    }

    // Step 1: Find item by barcode directly in PR items table
    let pr_item_row = null;
    let row_index = -1;

    frm.doc.items.forEach(function (row, index) {
      if (row.barcode === scanned_barcode) {
        pr_item_row = row;
        row_index = index;
      }
    });

    // CRITICAL VALIDATION: Block if barcode not found in PR items
    if (!pr_item_row) {
      frappe.show_alert(
        {
          message: __("❌ Barcode NOT in Purchase Receipt: {0} !", [
            scanned_barcode,
          ]),
          indicator: "red",
        },
        6
      );

      // Play error sound if available
      if (frappe.utils.play_sound) {
        frappe.utils.play_sound("error");
      }

      return; // EXIT - Do NOT proceed
    }

    // Step 2: Get ordered quantity via Server Script API
    let po_reference = pr_item_row.purchase_order || frm.doc.purchase_order;
    let item_code = pr_item_row.item_code;

    if (!po_reference) {
      frappe.show_alert(
        {
          message: __("❌ No Purchase Order reference found"),
          indicator: "red",
        },
        5
      );
      return;
    }

    // Call Server Script API (runs with elevated permissions)
    frappe.call({
      method: "electro_zone.electro_zone.handlers.purchase_order.get_po_ordered_qty",
      args: {
        po_reference: po_reference,
        item_code: item_code,
      },
      callback: function (r) {
        if (!r.message || !r.message.success) {
          frappe.show_alert(
            {
              message: __("❌ {0}", [
                r.message.error || "Item not found in Purchase Order",
              ]),
              indicator: "red",
            },
            5
          );
          return;
        }

        let ordered_qty = r.message.ordered_qty;
        let current_qty = pr_item_row.custom_received_quantity || 0;
        let new_qty = current_qty + 1;

        // Step 3: Validate against ordered quantity
        if (new_qty > ordered_qty) {
          frappe.show_alert(
            {
              message: __("❌ Max reached! {0} | Limit: {1}", [
                item_code,
                ordered_qty,
              ]),
              indicator: "red",
            },
            5
          );
          return;
        }

        // Step 4: Increment custom_received_quantity by +1
        frappe.model.set_value(
          pr_item_row.doctype,
          pr_item_row.name,
          "custom_received_quantity",
          new_qty
        );

        // Step 5: Show success message
        frappe.show_alert(
          {
            message: __("✅ {0} --> +1 (total: {1}/{2})", [
              item_code,
              new_qty,
              ordered_qty,
            ]),
            indicator: "green",
          },
          3
        );

        // Step 6: Focus on custom_received_quantity field and scroll to it
        setTimeout(() => {
          // Find the custom_received_quantity input field for this row
          let qty_field = $(
            `[data-name="${pr_item_row.name}"] [data-fieldname="custom_received_quantity"] input`
          );

          if (qty_field.length) {
            // Focus on the field (keeps focus on row)
            qty_field.focus();

            // Scroll row into view if needed
            qty_field[0].scrollIntoView({
              behavior: "smooth",
              block: "center",
            });
          }
        }, 100);

        // Refresh grid to show updated quantity
        frm.refresh_field("items");
      },
    });
  },

  refresh: function (frm) {
    // Determine user role for conditional hiding
    const isStockUserOnly =
      frappe.user.has_role("Stock User") &&
      !frappe.user.has_role("System Manager");

    // -------------------- Remove All Action Buttons (Stock User Only) --------------------
    if (isStockUserOnly && frm.doc.docstatus === 1) {
      frm.page.clear_inner_toolbar();
    }

    // -------------------- Hide Tabs (Stock User Only) --------------------
    if (isStockUserOnly) {
      ["more_info", "connections"].forEach((tab) => {
        if (frm.fields_dict[tab]) {
          frm.set_df_property(tab, "hidden", 1);
        }
      });
    }

    // -------------------- Hide Status Indicators (Stock User Only) --------------------
    if (isStockUserOnly) {
      const statusFields = ["status", "per_billed"];
      statusFields.forEach((field) => {
        frm.set_df_property(field, "hidden", 1);
      });
    }

    // -------------------- Hide Parent Money Fields (Stock User Only) --------------------
    if (isStockUserOnly) {
      const parentMoneyFields = [
        "currency", "conversion_rate", "price_list_currency", "plc_conversion_rate",
        "base_total", "total", "base_net_total", "net_total", "base_grand_total", "grand_total",
        "rounding_adjustment", "base_rounding_adjustment", "rounded_total", "base_rounded_total",
        "in_words", "base_in_words", "disable_rounded_total",
        "taxes", "taxes_and_charges", "taxes_and_charges_added", "taxes_and_charges_deducted",
        "total_taxes_and_charges", "base_total_taxes_and_charges", "tax_category",
        "apply_discount_on", "additional_discount_percentage", "discount_amount",
        "additional_discount_amount", "base_discount_amount", "base_additional_discount_amount",
        "payment_terms_template", "payment_schedule", "ignore_pricing_rule", "pricing_rules",
        "cost_center", "project", "buying_price_list", "is_subcontracted", "rejected_warehouse",
        "shipping_rule", "incoterm", "supplied_items"
      ];

      parentMoneyFields.forEach((field) => {
        frm.set_df_property(field, "hidden", 1);
      });
    }

    // -------------------- Hide Items Grid Money Fields (Stock User Only) --------------------
    if (isStockUserOnly && frm.fields_dict["items"]?.grid) {
      const grid = frm.fields_dict["items"].grid;

      const childMoneyFields = [
        "rate", "amount", "base_rate", "base_amount", "net_rate", "net_amount",
        "price_list_rate", "valuation_rate", "discount_percentage", "discount_amount",
        "item_tax_template", "item_tax_rate", "gross_profit", "margin_rate_or_amount",
        "margin_type", "pricing_rules", "rm_supp_cost", "landed_cost_voucher_amount"
      ];

      childMoneyFields.forEach((fieldname) => {
        grid.update_docfield_property(fieldname, "hidden", 1);
        grid.update_docfield_property(fieldname, "read_only", 1);
      });

      grid.refresh();
    }

    // -------------------- Block Add Row Button (ALL USERS) --------------------
    // This enforces the PO-only workflow for all users
    if (frm.fields_dict["items"]?.grid) {
      const grid = frm.fields_dict["items"].grid;

      // Disable adding rows
      grid.cannot_add_rows = true;

      // Hide the "Add Row" button from UI
      if (grid.grid_buttons) {
        grid.grid_buttons.find('.grid-add-row').hide();
      }

      // Hide row checkboxes (prevents deletion)
      grid.grid_rows.forEach(row => {
        if (row.wrapper) {
          row.wrapper.find('.grid-row-check').hide();
        }
      });

      grid.refresh();
    }

    // -------------------- Hide Dashboard Indicators (Stock User Only) --------------------
    if (isStockUserOnly && frm.dashboard?.stats_area) {
      setTimeout(() => {
        frm.dashboard.stats_area.find(".form-stats").hide();
      }, 200);
    }

    // Block adding new rows completely (prevents barcode auto-add)
    if (frm.fields_dict.items && frm.fields_dict.items.grid) {
      // Disable row addition at Grid API level
      frm.fields_dict.items.grid.cannot_add_rows = true;

      // Hide "Add Row" button visually
      frm.fields_dict.items.grid.wrapper.find(".grid-add-row").hide();

      // Hide row checkboxes (prevent deletion too)
      frm.fields_dict.items.grid.wrapper.find(".grid-row-check").hide();
    }

    // Auto-focus barcode field on form load
    if (frm.doc.docstatus === 0 && frm.doc.items && frm.doc.items.length > 0) {
      setTimeout(function () {
        if (frm.fields_dict.scan_barcode) {
          frm.fields_dict.scan_barcode.$input.focus();
        }
      }, 500);
    }
  },

  onload(frm) {
    const isStockUserOnly =
      frappe.user.has_role("Stock User") &&
      !frappe.user.has_role("System Manager");

    // -------------------- Hide Status Indicators on Load (Stock User Only) --------------------
    if (isStockUserOnly) {
      ["status", "per_billed"].forEach((field) => {
        frm.set_df_property(field, "hidden", 1);
      });
    }

    // -------------------- Block Add Row on Load (ALL USERS) --------------------
    if (frm.fields_dict["items"]?.grid) {
      const grid = frm.fields_dict["items"].grid;

      // Disable adding rows
      grid.cannot_add_rows = true;

      // Hide the "Add Row" button
      if (grid.grid_buttons) {
        grid.grid_buttons.find('.grid-add-row').hide();
      }
    }
  },
});
