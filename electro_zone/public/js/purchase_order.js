// ====================================================================================
// PURCHASE ORDER - CLIENT SCRIPTS COLLECTION
// ====================================================================================
// All Purchase Order client scripts consolidated in one file
// ====================================================================================

// ====================================================================================
// SCRIPT 1: PO - Stock User Limited View
// ====================================================================================
// Purpose: Limit view and hide price fields for Stock Users

frappe.ui.form.on('Purchase Order', {
  refresh(frm) {
    // Only affect Stock User (don't touch System Manager)
    const isStockUserOnly =
      frappe.user.has_role('Stock User') && !frappe.user.has_role('System Manager');

    if (!isStockUserOnly) return;

    // -------------------- Add Purchase Receipt Button FIRST --------------------
    // Add button immediately for submitted documents
    if (frm.doc.docstatus === 1) {
      // Clear and re-add to ensure it appears
      frm.page.clear_inner_toolbar();

      frm.add_custom_button(__('Make Purchase Receipt'), () => {
        frappe.model.open_mapped_doc({
          method: "erpnext.buying.doctype.purchase_order.purchase_order.make_purchase_receipt",
          frm: frm
        });
      }).addClass('btn-primary');
    }

    // -------------------- Hide Tabs --------------------
    ['more_info', 'connections'].forEach(tab => {
      if (frm.fields_dict[tab]) {
        frm.set_df_property(tab, 'hidden', 1);
      }
    });

    // -------------------- Hide Status Indicators --------------------
    const statusFields = [
      'status',
      'per_billed',
      'per_received'
    ];
    statusFields.forEach(field => {
      frm.set_df_property(field, 'hidden', 1);
    });

    // -------------------- Hide Parent Money Fields --------------------
    const parentMoneyFields = [
      // Currency & Conversion
      'currency', 'conversion_rate', 'price_list_currency', 'plc_conversion_rate',
      // Totals
      'base_total', 'total', 'base_net_total', 'net_total',
      'base_grand_total', 'grand_total',
      // Rounding & In Words
      'rounding_adjustment', 'base_rounding_adjustment',
      'rounded_total', 'base_rounded_total',
      'in_words', 'base_in_words',
      'disable_rounded_total',
      // Taxes
      'taxes', 'taxes_and_charges', 'total_taxes_and_charges', 'base_total_taxes_and_charges',
      'tax_category',
      // Discounts
      'apply_discount_on', 'additional_discount_percentage',
      'discount_amount', 'additional_discount_amount',
      'base_discount_amount', 'base_additional_discount_amount',
      // Advances / Payments / Pricing
      'advance_paid', 'payment_terms_template', 'payment_schedule',
      'ignore_pricing_rule', 'pricing_rules'
    ];

    parentMoneyFields.forEach(field => {
      frm.set_df_property(field, 'hidden', 1);
    });

    // -------------------- Hide Items Grid Money Fields --------------------
    const childMoneyFields = [
      'rate', 'amount', 'base_rate', 'base_amount',
      'net_rate', 'net_amount', 'price_list_rate', 'valuation_rate',
      'discount_percentage', 'discount_amount',
      'item_tax_template', 'item_tax_rate',
      'gross_profit', 'margin_rate_or_amount', 'margin_type',
      'pricing_rules'
    ];

    if (frm.fields_dict['items']?.grid) {
      const grid = frm.fields_dict['items'].grid;

      childMoneyFields.forEach(fieldname => {
        grid.update_docfield_property(fieldname, 'hidden', 1);
        grid.update_docfield_property(fieldname, 'read_only', 1);
      });

      grid.refresh();
    }



    // -------------------- Hide Dashboard Indicators --------------------
    // Hide billing and receiving percentage from dashboard
    if (frm.dashboard?.stats_area) {
      setTimeout(() => {
        frm.dashboard.stats_area.find('.form-stats').hide();
      }, 200);
    }
  },

  onload(frm) {
    // Additional hiding for Stock User on form load
    const isStockUserOnly =
      frappe.user.has_role('Stock User') && !frappe.user.has_role('System Manager');

    if (isStockUserOnly) {
      // Hide status indicators in the form layout
      ['status', 'per_billed', 'per_received'].forEach(field => {
        frm.set_df_property(field, 'hidden', 1);
      });
    }
  }
});

// ====================================================================================
// SCRIPT 2: Purchase Order - Filter Items by Supplier with Quick Add
// ====================================================================================
// Purpose: Lock item grid when PO is approved, hide buttons, add "Get Items by Supplier"

frappe.ui.form.on("Purchase Order", {
  before_load: function (frm) {
    // Inject CSS to hide buttons globally (before form loads)
    if (!$("#hide-po-buttons").length) {
      $(
        '<style id="hide-po-buttons">\
        .inner-group-button[data-label="Status"] { display: none !important; }\
        .inner-group-button[data-label="Tools"] { display: none !important; }\
        button[data-label="Update%20Items"] { display: none !important; }\
        button[data-label="Get%20Items%20From"] { display: none !important; }\
        .btn-default.ellipsis[data-toggle="dropdown"]:contains("Get Items From") { display: none !important; }\
      </style>'
      ).appendTo("head");
    }
  },

  setup: function (frm) {
    // Clear menu to prevent button creation
    frm.page.clear_menu();
  },

  refresh: function (frm) {
    // Extra safety - remove buttons if they appear
    frm.remove_custom_button("Get Items From");
    $('.inner-group-button[data-label="Status"]').remove();
    $('.inner-group-button[data-label="Tools"]').remove();
    $('button[data-label="Update%20Items"]').remove();
    $('button[data-label="Get%20Items%20From"]').remove();
    $('.btn-default.ellipsis:contains("Get Items From")').parent().remove();

    // COMPLETE ITEM GRID LOCKDOWN - ONLY when workflow_state is "Approved"
    // Allow editing when: workflow_state is NOT "Approved" (Draft, Pending, etc.)
    // Block editing when: workflow_state === "Approved"
    if (frm.doc.workflow_state === "Approved") {
      // Make items table completely read-only
      frm.fields_dict.items.grid.cannot_add_rows = true;
      frm.fields_dict.items.grid.wrapper.find(".grid-add-row").remove();
      frm.fields_dict.items.grid.wrapper.find(".grid-remove-rows").remove();

      // Disable all item row controls (delete buttons)
      frm.doc.items.forEach((item, idx) => {
        frm.fields_dict.items.grid.grid_rows[idx].wrapper
          .find(".grid-delete-row")
          .remove();
      });

      // Make entire grid read-only (no field editing)
      frm.set_df_property("items", "read_only", 1);

      // Lock specific fields in items table (including item_code)
      frm.fields_dict.items.grid.docfields.forEach(function (df) {
        if (
          df.fieldname === "item_code" ||
          df.fieldname === "item_name" ||
          df.fieldname === "qty" ||
          df.fieldname === "rate" ||
          df.fieldname === "amount"
        ) {
          df.read_only = 1;
        }
      });

      // Disable all input fields in the grid (but KEEP download button functional)
      frm.fields_dict.items.grid.wrapper
        .find("input, select, textarea")
        .prop("disabled", true);

      // Disable grid action buttons (except download)
      frm.fields_dict.items.grid.wrapper
        .find("button")
        .not('.grid-download, .btn-open-row, [data-action="download"]')
        .prop("disabled", true);

      // Block pointer events on inputs only (no grayed out effect)
      frm.fields_dict.items.grid.wrapper
        .find("input, select, textarea")
        .css("pointer-events", "none");
    } else {
      // ALLOW editing when NOT Approved (Draft, Pending, etc.)
      // Explicitly enable adding/removing rows
      frm.fields_dict.items.grid.cannot_add_rows = false;
      frm.set_df_property("items", "read_only", 0);

      // Refresh grid to restore all buttons (Add Row, Add Multiple, etc.)
      frm.fields_dict.items.grid.refresh();

      // Enable all input fields
      frm.fields_dict.items.grid.wrapper
        .find("input, select, textarea, button")
        .prop("disabled", false)
        .css("pointer-events", "auto");

      // Ensure grid buttons are visible
      frm.fields_dict.items.grid.wrapper.find(".grid-add-row").show();
      frm.fields_dict.items.grid.wrapper.find(".grid-buttons").show();
    }

    // Apply filter when form loads
    if (frm.doc.supplier) {
      set_item_query_and_disable_creation(frm);

      // Add custom button ONLY when workflow allows editing
      // Show button when: workflow_state is NOT "Approved"
      // Hide button when: workflow_state === "Approved"
      if (frm.doc.workflow_state !== "Approved") {
        let btn = frm.add_custom_button(
          __("Get Items by Supplier"),
          function () {
            show_supplier_items_dialog(frm);
          }
        );

        // Apply base styling
        btn.css({
          "background-color": "#216DFF",
          color: "white",
          border: "none",
        });

        // Add hover effect
        btn.hover(
          function () {
            $(this).css("background-color", "#5A8FFF"); // Softer/lighter on hover
          },
          function () {
            $(this).css("background-color", "#216DFF"); // Original color
          }
        );

        // Add click/active effect
        btn.on("mousedown", function () {
          $(this).css("background-color", "#4A7FFF"); // Medium shade when clicking
        });

        btn.on("mouseup", function () {
          $(this).css("background-color", "#5A8FFF"); // Return to hover color
        });
      }
    }
  },

  supplier: function (frm) {
    // Apply filter when supplier changes
    if (frm.doc.supplier) {
      set_item_query_and_disable_creation(frm);

      // Clear existing items if supplier changes
      if (frm.doc.items && frm.doc.items.length > 0) {
        frappe.confirm(
          "Changing supplier will clear all items. Do you want to continue?",
          function () {
            // User clicked Yes
            frm.clear_table("items");
            frm.refresh_field("items");
          },
          function () {
            // User clicked No - revert supplier change
            frm.set_value("supplier", frm.doc.__previous_supplier);
          }
        );
      }

      // Store current supplier for comparison
      frm.doc.__previous_supplier = frm.doc.supplier;
    } else {
      // If supplier is cleared, remove filter
      frm.fields_dict["items"].grid.get_field("item_code").get_query = null;
    }
  },
});

frappe.ui.form.on("Purchase Order Item", {
  before_items_add: function (frm, cdt, cdn) {
    // Block adding items ONLY when workflow_state = "Approved"
    if (frm.doc.workflow_state === "Approved") {
      frappe.msgprint({
        title: __("Action Blocked"),
        message: __(
          "Cannot add items to an approved Purchase Order. Workflow State: Approved"
        ),
        indicator: "red",
      });
      frappe.validated = false;
      return false;
    }
  },

  before_items_remove: function (frm, cdt, cdn) {
    // Block removing items ONLY when workflow_state = "Approved"
    if (frm.doc.workflow_state === "Approved") {
      frappe.msgprint({
        title: __("Action Blocked"),
        message: __(
          "Cannot remove items from an approved Purchase Order. Workflow State: Approved"
        ),
        indicator: "red",
      });
      frappe.validated = false;
      return false;
    }
  },

  items_add: function (frm, cdt, cdn) {
    // Disable item creation when adding new row
    if (frm.doc.supplier) {
      set_item_query_and_disable_creation(frm);
    }
  },
});

function set_item_query_and_disable_creation(frm) {
  // Filter items to show only those with matching custom_primary_supplier
  // AND disable the "Create" option completely
  frm.set_query("item_code", "items", function () {
    return {
      filters: {
        custom_primary_supplier: frm.doc.supplier,
        is_purchase_item: 1,
        disabled: 0,
      },
    };
  });

  // Disable the "Create" option in item dropdown by setting only_select
  if (frm.fields_dict["items"] && frm.fields_dict["items"].grid) {
    let item_field = frm.fields_dict["items"].grid.get_field("item_code");
    if (item_field) {
      item_field.get_query = function () {
        return {
          filters: {
            custom_primary_supplier: frm.doc.supplier,
            is_purchase_item: 1,
            disabled: 0,
          },
        };
      };
      // This prevents "Create" option from showing
      item_field.only_select = true;
    }
  }
}

function filter_items_by_group(dialog, selected_group, all_items) {
  // Filter items based on selected group
  let filtered_items = all_items;
  if (selected_group !== "All Groups") {
    filtered_items = all_items.filter(
      (item) => item.item_group === selected_group
    );
  }

  // Re-render the table with filtered items
  render_items_table(dialog, filtered_items);
}

function render_items_table(dialog, items) {
  // Build HTML table with checkboxes - Enhanced UI/UX version
  let html = `
        <style>
        /* ========================================
           ENHANCED SUPPLIER ITEMS TABLE STYLING
           ======================================== */

        /* Main table container with card-like appearance */
        .supplier-items-container {
            background: #ffffff;
            border-radius: 8px;
            box-shadow: 0 1px 3px rgba(0, 0, 0, 0.05);
            overflow: hidden;
            margin-bottom: 16px;
        }

        /* Scrollable wrapper */
        .supplier-items-scroll {
            max-height: 520px;
            overflow-y: auto;
            overflow-x: auto;
        }

        /* Custom scrollbar styling */
        .supplier-items-scroll::-webkit-scrollbar {
            width: 8px;
            height: 8px;
        }

        .supplier-items-scroll::-webkit-scrollbar-track {
            background: #f1f1f1;
            border-radius: 4px;
        }

        .supplier-items-scroll::-webkit-scrollbar-thumb {
            background: #c1c1c1;
            border-radius: 4px;
        }

        .supplier-items-scroll::-webkit-scrollbar-thumb:hover {
            background: #a8a8a8;
        }

        /* Table base styling */
        .supplier-items-table {
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            font-size: 13px;
            margin: 0;
            table-layout: fixed; /* Fixed layout for better column control */
        }

        /* Header styling with gradient and shadow */
        .supplier-items-table thead {
            background: linear-gradient(to bottom, #f8f9fc 0%, #f1f3f8 100%);
            border-bottom: 2px solid #e2e8f0;
        }

        .supplier-items-table th {
            font-weight: 600;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.5px;
            color: #4a5568;
            padding: 14px 12px;
            text-align: left;
            position: sticky;
            top: 0;
            z-index: 10;
            background: linear-gradient(to bottom, #f8f9fc 0%, #f1f3f8 100%);
            border-bottom: 2px solid #e2e8f0;
            white-space: nowrap;
        }

        /* Column-specific text alignment */
        .supplier-items-table th.text-center,
        .supplier-items-table td.text-center {
            text-align: center;
        }

        .supplier-items-table th.text-right,
        .supplier-items-table td.text-right {
            text-align: right;
        }

        /* Checkbox column */
        .supplier-items-table th:first-child,
        .supplier-items-table td:first-child {
            width: 50px;
            text-align: center;
        }

        /* Body rows */
        .supplier-items-table tbody tr {
            border-bottom: 1px solid #e2e8f0;
            transition: all 0.2s ease;
        }

        .supplier-items-table tbody tr:last-child {
            border-bottom: none;
        }

        .supplier-items-table tbody tr:hover {
            background-color: #f7fafc;
            box-shadow: inset 0 0 0 1px #e2e8f0;
            cursor: pointer;
        }

        /* Body cells */
        .supplier-items-table td {
            padding: 12px;
            vertical-align: middle;
            color: #2d3748;
            line-height: 1.5;
            word-wrap: break-word;
        }

        /* Checkbox styling */
        .item-checkbox {
            width: 18px;
            height: 18px;
            cursor: pointer;
            accent-color: #216DFF;
        }

        #select-all-items {
            width: 18px;
            height: 18px;
            cursor: pointer;
            accent-color: #216DFF;
        }

        /* Item Code column - bold and prominent */
        .item-code-cell {
            font-weight: 600;
            color: #1a202c;
            font-size: 13px;
        }

        /* Model Number column - colored and styled */
        .model-number-cell {
            color: #e53e3e;
            font-weight: 600;
            font-size: 13px;
            letter-spacing: 0.3px;
        }

        /* Item Name column */
        .item-name-cell {
            color: #2d3748;
            font-size: 13px;
            overflow: hidden;
            text-overflow: ellipsis;
        }

        /* UOM column - badge style */
        .uom-badge {
            display: inline-block;
            padding: 4px 10px;
            background: #edf2f7;
            color: #4a5568;
            border-radius: 4px;
            font-size: 11px;
            font-weight: 600;
            text-transform: uppercase;
        }

        /* Rate column - prominent pricing */
        .rate-cell {
            font-weight: 700;
            color: #2d3748;
            font-size: 14px;
            white-space: nowrap;
        }

        /* Description column - wrapped text with proper styling */
        .description-cell {
            font-size: 11px;
            color: #718096;
            line-height: 1.6;
            max-width: 350px;
            word-wrap: break-word;
            white-space: normal;
            overflow: hidden;
            display: -webkit-box;
            -webkit-line-clamp: 3; /* Limit to 3 lines */
            -webkit-box-orient: vertical;
        }

        /* Empty state styling */
        .empty-state {
            text-align: center;
            padding: 60px 20px;
            color: #a0aec0;
        }

        .empty-state svg {
            width: 64px;
            height: 64px;
            margin-bottom: 16px;
            opacity: 0.5;
        }

        .empty-state-text {
            font-size: 14px;
            font-weight: 500;
            margin-bottom: 4px;
        }

        .empty-state-subtext {
            font-size: 12px;
            color: #cbd5e0;
        }

        /* Tip box at bottom */
        .items-tip-box {
            margin-top: 12px;
            padding: 12px 16px;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            border-radius: 6px;
            box-shadow: 0 2px 4px rgba(102, 126, 234, 0.2);
        }

        .items-tip-box-text {
            color: #ffffff;
            font-size: 12px;
            line-height: 1.5;
            margin: 0;
            display: flex;
            align-items: center;
            gap: 8px;
        }

        .items-tip-icon {
            font-size: 16px;
            opacity: 0.9;
        }

        /* Responsive design */
        @media (max-width: 1200px) {
            .supplier-items-table {
                font-size: 12px;
            }

            .supplier-items-table th,
            .supplier-items-table td {
                padding: 10px 8px;
            }
        }
        </style>

        <div class="supplier-items-container">
            <div class="supplier-items-scroll">
                <table class="supplier-items-table">
                    <thead>
                        <tr>
                            <th class="text-center">
                                <input type="checkbox" id="select-all-items">
                            </th>
                            <th style="width: 14%;">Item Code</th>
                            <th style="width: 14%;">Model Number</th>
                            <th style="width: 18%;">Item Name</th>
                            <th class="text-center" style="width: 8%;">UOM</th>
                            <th class="text-right" style="width: 12%;">Rate</th>
                            <th style="width: 34%;">Description</th>
                        </tr>
                    </thead>
                    <tbody>
    `;

  if (items.length === 0) {
    html += `
            <tr>
                <td colspan="7" class="empty-state">
                    <svg xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M20 13V6a2 2 0 00-2-2H6a2 2 0 00-2 2v7m16 0v5a2 2 0 01-2 2H6a2 2 0 01-2-2v-5m16 0h-2.586a1 1 0 00-.707.293l-2.414 2.414a1 1 0 01-.707.293h-3.172a1 1 0 01-.707-.293l-2.414-2.414A1 1 0 006.586 13H4" />
                    </svg>
                    <div class="empty-state-text">No items found</div>
                    <div class="empty-state-subtext">Try changing your filter selection</div>
                </td>
            </tr>
        `;
  } else {
    items.forEach(function (item, index) {
      html += `
                <tr data-item-index="${index}">
                    <td class="text-center" onclick="event.stopPropagation();">
                        <input type="checkbox" class="item-checkbox" data-item-code="${item.name}">
                    </td>
                    <td class="item-code-cell">${item.name}</td>
                    <td class="model-number-cell">${item.custom_item_model || "-"}</td>
                    <td class="item-name-cell" title="${item.item_name || ""}">${
        item.item_name || ""
      }</td>
                    <td class="text-center">
                        <span class="uom-badge">${item.stock_uom || "Nos"}</span>
                    </td>
                    <td class="rate-cell text-right">${frappe.format(
            item.custom_repeat_final_rate_price || 0,
            { fieldtype: "Currency" }
          )}</td>
                    <td class="description-cell" title="${item.description || "-"}">
                        ${item.description || "-"}
                    </td>
                </tr>
            `;
    });
  }

  html += `
                    </tbody>
                </table>
            </div>
        </div>

        <div class="items-tip-box">
            <p class="items-tip-box-text">
                <span class="items-tip-icon">💡</span>
                <span><strong>Tip:</strong> Click on any row to select/deselect items, or use the checkbox in the header to select all.</span>
            </p>
        </div>
    `;

  dialog.fields_dict.items_html.$wrapper.html(html);

  // Add select all functionality
  dialog.$wrapper.find("#select-all-items").on("change", function () {
    dialog.$wrapper
      .find(".item-checkbox")
      .prop("checked", $(this).prop("checked"));
  });

  // Add row click functionality (checkbox toggle)
  dialog.$wrapper
    .find(".supplier-items-table tbody tr")
    .on("click", function (e) {
      if (!$(e.target).is('input[type="checkbox"]')) {
        let checkbox = $(this).find(".item-checkbox");
        checkbox.prop("checked", !checkbox.prop("checked"));
      }
    });
}

function show_supplier_items_dialog(frm) {
  // Fetch items for the selected supplier
  frappe.call({
    method: "frappe.client.get_list",
    args: {
      doctype: "Item",
      filters: {
        custom_primary_supplier: frm.doc.supplier,
        is_purchase_item: 1,
        disabled: 0,
      },
      fields: [
        "name",
        "item_name",
        "item_group",
        "custom_item_model",
        "stock_uom",
        "custom_repeat_final_rate_price",
        "description",
      ],
      limit_page_length: 500,
    },
    callback: function (r) {
      if (r.message && r.message.length > 0) {
        // Get unique item groups
        let item_groups = ["All Groups"];
        let unique_groups = [
          ...new Set(r.message.map((item) => item.item_group)),
        ];
        item_groups = item_groups.concat(unique_groups.sort());

        // Create dialog with item list
        // TO CUSTOMIZE DIALOG SIZE: Add 'size' property below
        // Options: 'small', 'medium', 'large', 'extra-large'
        // Example: size: 'extra-large'
        let d = new frappe.ui.Dialog({
          title: __("Select Items for {0}", [frm.doc.supplier]),
          size: "extra-large", // Dialog width: small=400px, medium=600px, large=800px, extra-large=1140px
          fields: [
            {
              fieldname: "item_group_filter",
              fieldtype: "Select",
              label: __("Filter by Item Group"),
              options: item_groups.join("\n"),
              default: "All Groups",
              onchange: function () {
                filter_items_by_group(d, this.value, r.message);
              },
            },
            {
              fieldname: "items_html",
              fieldtype: "HTML",
            },
          ],
          primary_action_label: __("Add Selected Items"),
          primary_action: function () {
            let selected_items = [];
            d.$wrapper.find('input[type="checkbox"]:checked').each(function () {
              if ($(this).attr("id") !== "select-all-items") {
                selected_items.push($(this).data("item-code"));
              }
            });

            if (selected_items.length > 0) {
              // Step 1: Add all items to the grid first
              let added_rows = [];
              selected_items.forEach(function (item_code) {
                let row = frm.add_child("items");
                frappe.model.set_value(
                  row.doctype,
                  row.name,
                  "item_code",
                  item_code
                );
                // Set default quantity to 1
                frappe.model.set_value(row.doctype, row.name, "qty", 1);
                added_rows.push({ row: row, item_code: item_code });
              });

              // Refresh grid to show items
              frm.refresh_field("items");

              // Step 2: Wait 300ms, then fetch and populate all price fields
              setTimeout(function () {
                // Fetch all items in parallel (faster than sequential)
                let fetch_promises = added_rows.map(function (row_data) {
                  return new Promise(function (resolve, reject) {
                    frappe.call({
                      method: "frappe.client.get",
                      args: {
                        doctype: "Item",
                        name: row_data.item_code,
                      },
                      callback: function (r) {
                        if (r.message) {
                          resolve({ row: row_data.row, item: r.message });
                        } else {
                          reject("Item not found: " + row_data.item_code);
                        }
                      },
                      error: function (err) {
                        reject(err);
                      },
                    });
                  });
                });

                // Wait for all fetches to complete
                Promise.all(fetch_promises)
                  .then(function (results) {
                    // Step 3: Populate all fields for each row
                    results.forEach(function (result) {
                      let row = result.row;
                      let item = result.item;

                      // Populate Model Number
                      if (item.custom_item_model) {
                        frappe.model.set_value(
                          row.doctype,
                          row.name,
                          "custom_model_number",
                          item.custom_item_model
                        );
                      }

                      // Populate Rate (main price field)
                      if (item.custom_repeat_final_rate_price) {
                        frappe.model.set_value(
                          row.doctype,
                          row.name,
                          "rate",
                          item.custom_repeat_final_rate_price
                        );
                      }

                      // Populate Rate -Price (Company Currency)-
                      if (item.custom_repeat_final_rate_price) {
                        frappe.model.set_value(
                          row.doctype,
                          row.name,
                          "custom_price_company_currency",
                          item.custom_repeat_final_rate_price
                        );
                      }

                      // Populate Price Breakdown Fields
                      if (item.custom_current_final_price_list) {
                        frappe.model.set_value(
                          row.doctype,
                          row.name,
                          "custom_final_price_list",
                          item.custom_current_final_price_list
                        );
                      }

                      if (item.custom_current_final_promo) {
                        frappe.model.set_value(
                          row.doctype,
                          row.name,
                          "custom_final_promo",
                          item.custom_current_final_promo
                        );
                      }

                      if (item.custom_current_final_sellout_promo) {
                        frappe.model.set_value(
                          row.doctype,
                          row.name,
                          "custom_final_sellout_promo",
                          item.custom_current_final_sellout_promo
                        );
                      }

                      if (item.custom_repeat_invoice_discount) {
                        frappe.model.set_value(
                          row.doctype,
                          row.name,
                          "custom_repeat_invoice_discount",
                          item.custom_repeat_invoice_discount
                        );
                      }

                      if (item.custom_repeat_cash_discount) {
                        frappe.model.set_value(
                          row.doctype,
                          row.name,
                          "custom_repeat_cash_discount",
                          item.custom_repeat_cash_discount
                        );
                      }
                    });

                    // Step 4: Final refresh and show success
                    frm.refresh_field("items");
                    frappe.show_alert({
                      message: __(
                        "{0} items added with all price fields populated",
                        [selected_items.length]
                      ),
                      indicator: "green",
                    });
                  })
                  .catch(function (error) {
                    console.error("Error fetching item details:", error);
                    frappe.show_alert({
                      message: __(
                        "Items added but some price fields may not be populated. Check console for errors."
                      ),
                      indicator: "orange",
                    });
                  });
              }, 300); // 300ms delay

              // Close dialog immediately after items are added
              d.hide();
            } else {
              frappe.msgprint(__("Please select at least one item"));
            }
          },
        });

        // Render initial table with all items
        render_items_table(d, r.message);
        d.show();
      } else {
        frappe.msgprint(
          __("No items found for supplier {0}", [frm.doc.supplier])
        );
      }
    },
  });
}

// ====================================================================================
// SCRIPT 3: Purchase Order - Price Edit Status Color Indicator
// ====================================================================================
// Purpose: Add colored indicators to Price Edit Status field
// Green for "Automatic" (default), Blue for "Manually Edited"

frappe.ui.form.on("Purchase Order", {
  refresh(frm) {
    apply_status_indicator(frm);
  },

  onload(frm) {
    apply_status_indicator(frm);
  },

  custom_price_edit_status(frm) {
    // Trigger when status field changes
    apply_status_indicator(frm);
  },
});

function apply_status_indicator(frm) {
  if (!frm.doc.custom_price_edit_status) return;

  // Set indicator color based on status
  if (frm.doc.custom_price_edit_status === "Manually Edited") {
    // Blue indicator for manual edits
    frm.set_df_property(
      "custom_price_edit_status",
      "label_style",
      "primary"
    );
  } else if (frm.doc.custom_price_edit_status === "Automatic") {
    // Green indicator for automatic (default)
    frm.set_df_property(
      "custom_price_edit_status",
      "label_style",
      "success"
    );
  }
}

// ====================================================================================
// SCRIPT 4: Purchase Order - Real-Time Gift Item Calculation
// ====================================================================================
// Purpose: Calculate gift item adjustments in real-time with manual price editing

// Store original custom_price_company_currency values (for item_code change tracking only)
let original_custom_prices = {};

// Recursion guard to prevent infinite loops
let is_calculating_gifts = false;

frappe.ui.form.on("Purchase Order", {
  refresh(frm) {
    // Calculate custom_price_total on refresh
    if (frm.doc.docstatus === 0) {
      calculate_custom_price_total_only(frm);
    }

    // Update price edit status on refresh
    update_price_edit_status(frm);

    // Apply field locks on refresh - use delay to ensure grid is rendered
    setTimeout(() => {
      apply_field_locks_on_all_rows(frm);
    }, 300);
  },

  onload(frm) {
    // Update price edit status on load
    update_price_edit_status(frm);

    // Apply field locks when form first loads - use delay to ensure grid is rendered
    setTimeout(() => {
      apply_field_locks_on_all_rows(frm);
    }, 500);
  },
});

frappe.ui.form.on("Purchase Order Item", {
  // Trigger when item is added
  items_add(frm, cdt, cdn) {
    if (frm.doc.docstatus !== 0 || is_calculating_gifts) return;

    let row = locals[cdt][cdn];

    // Store original custom_price_company_currency when item is first added
    if (!original_custom_prices[cdn]) {
      original_custom_prices[cdn] = row.custom_price_company_currency || 0;
    }

    // Apply lock to this new row (default: locked)
    setTimeout(() => {
      set_field_read_only(frm, cdn, "custom_price_company_currency", true);
    }, 200);
  },

  // Trigger when grid row is rendered
  items_form_render(frm, cdt, cdn) {
    let row = locals[cdt][cdn];

    // Apply lock based on checkbox state when row is rendered
    let should_lock = !row.custom_allow_manual_price_edit;
    setTimeout(() => {
      set_field_read_only(
        frm,
        cdn,
        "custom_price_company_currency",
        should_lock
      );
    }, 50);
  },

  // Trigger when manual edit checkbox changes
  custom_allow_manual_price_edit(frm, cdt, cdn) {
    if (frm.doc.docstatus !== 0 || is_calculating_gifts) return;

    let row = locals[cdt][cdn];

    if (row.custom_allow_manual_price_edit) {
      // Checkbox CHECKED - Make field editable for THIS ROW ONLY
      set_field_read_only(frm, cdn, "custom_price_company_currency", false);

      // Update status to "Manually Edited"
      update_price_edit_status(frm);

      frappe.show_alert({
        message: __("Price field unlocked for manual editing"),
        indicator: "blue",
      });
    } else {
      // Checkbox UNCHECKED - Revert to original fetched value
      set_field_read_only(frm, cdn, "custom_price_company_currency", true);

      // FIX #4: Revert to original value stored when item_code was selected
      // This allows "Fetch From" to work naturally without API calls
      let original_price = original_custom_prices[cdn] || 0;

      // Temporarily disable gift calculation during revert
      let temp_is_calculating = is_calculating_gifts;
      is_calculating_gifts = true;

      // Revert to original value
      if (row.custom_price_company_currency !== original_price) {
        frappe.model.set_value(
          cdt,
          cdn,
          "custom_price_company_currency",
          original_price
        );
      }

      // Re-enable gift calculation after a short delay
      setTimeout(() => {
        is_calculating_gifts = temp_is_calculating;

        // Update status (may change back to "Automatic" if no other rows are edited)
        update_price_edit_status(frm);

        frappe.show_alert({
          message: __("Price reverted to original value"),
          indicator: "green",
        });

        // Recalculate gift items with reverted price
        calculate_gift_items(frm);
      }, 100);
    }
  },

  // Trigger when custom_price_company_currency changes manually
  custom_price_company_currency(frm, cdt, cdn) {
    if (frm.doc.docstatus !== 0 || is_calculating_gifts) return;

    let row = locals[cdt][cdn];

    // FIX #2 & #3: Remove direct rate sync - let calculate_gift_items handle it
    // Just recalculate gift items - they will use current custom_price_company_currency
    if (row.custom_allow_manual_price_edit) {
      frappe.show_alert({
        message: __(
          "Price updated - recalculating totals and gift distribution"
        ),
        indicator: "blue",
      });
    } else {
      // Update cache if manual edit is NOT allowed (auto-fetched value)
      original_custom_prices[cdn] = row.custom_price_company_currency || 0;
    }

    // Recalculate gift items (this will update Price Total, gift %, and rates)
    calculate_gift_items(frm);
  },

  // Trigger when gift checkbox changes
  custom_is_gift(frm, cdt, cdn) {
    if (frm.doc.docstatus !== 0 || is_calculating_gifts) return;
    calculate_gift_items(frm);
  },

  // Trigger when qty changes
  qty(frm, cdt, cdn) {
    if (frm.doc.docstatus !== 0 || is_calculating_gifts) return;
    calculate_gift_items(frm);
  },

  // Trigger when rate changes manually (when no gifts active)
  rate(frm, cdt, cdn) {
    if (frm.doc.docstatus !== 0 || is_calculating_gifts) return;

    // FIX #5: Simplified - only recalculate if no gifts are active
    if (!is_gift_calculation_active(frm)) {
      calculate_gift_items(frm);
    }
    // If gifts ARE active, don't recalculate (rate change is from calculate_gift_items)
  },

  // Trigger when item_code changes
  item_code(frm, cdt, cdn) {
    if (frm.doc.docstatus !== 0 || is_calculating_gifts) return;

    let row = locals[cdt][cdn];

    // Wait for item to fully load with all fetched fields
    setTimeout(() => {
      // Store new custom_price_company_currency as original for this item
      original_custom_prices[cdn] = row.custom_price_company_currency || 0;

      // Re-apply lock after item data loads (unless checkbox is checked)
      if (!row.custom_allow_manual_price_edit) {
        set_field_read_only(frm, cdn, "custom_price_company_currency", true);
      }

      // Recalculate with new item
      calculate_gift_items(frm);
    }, 500);
  },

  // Trigger when item is removed
  items_remove(frm, cdt, cdn) {
    if (frm.doc.docstatus !== 0 || is_calculating_gifts) return;

    // Clean up stored values
    delete original_custom_prices[cdn];
    calculate_gift_items(frm);
  },
});

function set_field_read_only(frm, cdn, fieldname, is_read_only) {
  // Set read-only property for a specific field in a specific row
  // This function uses both API updates and direct DOM manipulation
  let grid = frm.fields_dict.items.grid;
  if (!grid) return;

  let grid_row = grid.grid_rows_by_docname[cdn];
  if (!grid_row) return;

  // Update the field definition in multiple places
  if (grid_row.fields_dict && grid_row.fields_dict[fieldname]) {
    let row_field = grid_row.fields_dict[fieldname];
    if (row_field && row_field.df) {
      row_field.df.read_only = is_read_only ? 1 : 0;
    }
  }

  // Update in docfields array
  if (grid_row.docfields) {
    for (let i = 0; i < grid_row.docfields.length; i++) {
      if (grid_row.docfields[i].fieldname === fieldname) {
        grid_row.docfields[i].read_only = is_read_only ? 1 : 0;
        break;
      }
    }
  }

  // CRITICAL: Direct DOM manipulation to enable/disable the input field
  setTimeout(() => {
    // Find the input field in the DOM using the row index and field name
    let $input = $(grid_row.wrapper).find(
      `[data-fieldname="${fieldname}"] input`
    );

    if ($input.length > 0) {
      if (is_read_only) {
        // Lock the field
        $input.prop("readonly", true);
        $input.prop("disabled", true);
        $input.addClass("disabled-field");
        $input.css({
          "background-color": "#f5f5f5",
          cursor: "not-allowed",
          opacity: "0.7",
        });
      } else {
        // Unlock the field
        $input.prop("readonly", false);
        $input.prop("disabled", false);
        $input.removeClass("disabled-field");
        $input.css({
          "background-color": "#ffffff",
          cursor: "text",
          opacity: "1",
        });
        // Focus the field to show it's editable
        $input.focus();
      }
    }
  }, 50);
}

function apply_field_locks_on_all_rows(frm) {
  // Apply locks to all rows based on checkbox state
  if (!frm.doc.items || !frm.fields_dict.items) return;

  let grid = frm.fields_dict.items.grid;
  if (!grid || !grid.grid_rows) return;

  // Get the field definition from the grid
  let field_obj = grid.get_field("custom_price_company_currency");

  // Iterate through all grid rows
  grid.grid_rows.forEach((grid_row) => {
    if (grid_row && grid_row.doc) {
      let item = grid_row.doc;
      let should_lock = !item.custom_allow_manual_price_edit;

      // Update field in this specific row's fields_dict
      if (
        grid_row.fields_dict &&
        grid_row.fields_dict["custom_price_company_currency"]
      ) {
        grid_row.fields_dict["custom_price_company_currency"].df.read_only =
          should_lock ? 1 : 0;
      }
    }
  });

  // Refresh the entire grid to apply changes
  grid.refresh();
}

function calculate_custom_price_total_only(frm) {
  // Calculate ONLY custom_price_total without triggering gift calculation
  if (!frm.doc.items || frm.doc.items.length === 0) {
    frm.set_value("custom_price_total", 0);
    return;
  }

  let custom_price_total = 0;
  frm.doc.items.forEach((item) => {
    let qty = item.qty || 0;
    let base_price = item.custom_price_company_currency || 0;
    custom_price_total = custom_price_total + qty * base_price;
  });

  frm.set_value("custom_price_total", custom_price_total);
}

function update_price_edit_status(frm) {
  // Update Price Edit Status based on manual edit checkboxes
  // Check if ANY item has custom_allow_manual_price_edit checked
  if (!frm.doc.items || frm.doc.items.length === 0) {
    frm.set_value("custom_price_edit_status", "Automatic");
    return;
  }

  let has_manual_edit = frm.doc.items.some(
    (item) => item.custom_allow_manual_price_edit === 1
  );

  let new_status = has_manual_edit ? "Manually Edited" : "Automatic";

  // Only update if status has changed (prevent unnecessary triggers)
  if (frm.doc.custom_price_edit_status !== new_status) {
    frm.set_value("custom_price_edit_status", new_status);
  }
}

function is_gift_calculation_active(frm) {
  // Check if any items are marked as gifts
  if (!frm.doc.items) return false;
  return frm.doc.items.some((item) => item.custom_is_gift === 1);
}

function calculate_gift_items(frm) {
  // Prevent recursive calls and conflicts with other scripts
  if (is_calculating_gifts) return;

  // Use try-finally to ensure flag is always reset
  try {
    is_calculating_gifts = true;

    if (!frm.doc.items || frm.doc.items.length === 0) {
      frm.set_value("custom_price_total", 0);
      return;
    }

    // Calculate custom_price_total using custom_price_company_currency
    let custom_price_total = 0;
    frm.doc.items.forEach((item) => {
      let qty = item.qty || 0;
      let base_price = item.custom_price_company_currency || 0;
      let line_total = qty * base_price;
      custom_price_total = custom_price_total + line_total;
    });

    // Update custom_price_total field (baseline total)
    frm.set_value("custom_price_total", custom_price_total);

    // Calculate original total using custom_price_company_currency
    let original_total = 0;
    frm.doc.items.forEach((item) => {
      let qty = item.qty || 0;
      let base_price = item.custom_price_company_currency || 0;

      let line_total = qty * base_price;
      original_total = original_total + line_total;
    });

    // Calculate gift subtotal using custom_price_company_currency
    let gift_subtotal = 0;
    frm.doc.items.forEach((item) => {
      if (item.custom_is_gift === 1) {
        let qty = item.qty || 0;
        let base_price = item.custom_price_company_currency || 0;
        gift_subtotal = gift_subtotal + qty * base_price;
      }
    });

    // If no gifts, set rates to match custom_price_company_currency
    if (gift_subtotal === 0 || original_total === 0) {
      frm.doc.items.forEach((item) => {
        let base_price = item.custom_price_company_currency || 0;

        // Set rate to match custom_price_company_currency (no gift adjustment)
        if (Math.abs(item.rate - base_price) > 0.01) {
          frappe.model.set_value(item.doctype, item.name, "rate", base_price);
        }
      });
      frm.refresh_field("items");
      return;
    }

    // Calculate gift percentage and apply to all items
    let gift_percentage = gift_subtotal / original_total;

    frm.doc.items.forEach((item) => {
      let qty = item.qty || 0;
      if (qty === 0) return;

      // Use current custom_price_company_currency as base
      let base_price = item.custom_price_company_currency || 0;
      let original_line_amount = qty * base_price;

      // Calculate adjusted amount (reduce by gift percentage)
      let adjusted_amount = original_line_amount * (1 - gift_percentage);

      // Calculate new rate = custom_price_company_currency × (1 - gift_percentage)
      let new_rate = adjusted_amount / qty;

      // Update rate only if different (prevent unnecessary triggers)
      if (Math.abs(item.rate - new_rate) > 0.01) {
        frappe.model.set_value(item.doctype, item.name, "rate", new_rate);
      }
    });

    // Refresh the items table to show updated values
    frm.refresh_field("items");
  } finally {
    // Always reset the flag, even if error occurs
    is_calculating_gifts = false;
  }
}

// ====================================================================================
// SCRIPT 5: Purchase Order - Excel Download
// ====================================================================================
// Purpose: Download PO items as Excel file with custom formatting

frappe.ui.form.on('Purchase Order', {
    refresh: function(frm) {
        setTimeout(function() {
            override_download_button(frm);
        }, 500);
    }
});

function override_download_button(frm) {
    if (!frm.fields_dict.items || !frm.fields_dict.items.grid) return;

    let $download_btn = frm.fields_dict.items.grid.wrapper.find('.grid-download');

    if ($download_btn.length) {
        $download_btn.off('click');
        $download_btn.on('click', function(e) {
            e.preventDefault();
            e.stopPropagation();
            download_items_as_excel(frm);
        });
    }
}

function download_items_as_excel(frm) {
    let items = frm.doc.items || [];

    if (items.length === 0) {
        return;
    }

    if (typeof XLSX === 'undefined') {
        let script = document.createElement('script');
        script.src = 'https://cdn.sheetjs.com/xlsx-0.20.0/package/dist/xlsx.full.min.js';
        script.onload = function() {
            generate_and_download_excel(frm, items);
        };
        document.head.appendChild(script);
    } else {
        generate_and_download_excel(frm, items);
    }
}

function generate_and_download_excel(frm, items) {
    // Prepare data array
    let data = [];

    // Headers
    data.push([
        'Item Code',
        'Model Number',
        'Description',
        'Final Price List',
        'Final Promo',
        'Final Sellout Promo',
        'Invoice Discount',
        'Cash Discount',
        'Qty',
        'Rate'
    ]);

    // Add rows
    items.forEach(function(item) {
        // Format percentages as text with %
        let invoice_discount = item.custom_repeat_invoice_discount ? item.custom_repeat_invoice_discount.toFixed(2) + '%' : '0.00%';
        let cash_discount = item.custom_repeat_cash_discount ? item.custom_repeat_cash_discount.toFixed(2) + '%' : '0.00%';

        data.push([
            item.item_code || '',
            item.custom_model_number || '',
            item.description || '',
            item.custom_final_price_list || 0,
            item.custom_final_promo || 0,
            item.custom_final_sellout_promo || 0,
            invoice_discount,
            cash_discount,
            item.qty || '',
            item.rate || 0
        ]);
    });

    // Create worksheet
    let ws = XLSX.utils.aoa_to_sheet(data);

    // Set column widths
    ws['!cols'] = [
        {wch: 20},  // Item Code
        {wch: 20},  // Model Number
        {wch: 40},  // Description
        {wch: 18},  // Final Price List
        {wch: 15},  // Final Promo
        {wch: 20},  // Final Sellout Promo
        {wch: 18},  // Invoice Discount
        {wch: 15},  // Cash Discount
        {wch: 10},  // Qty
        {wch: 15}   // Rate
    ];

    // Apply formatting to cells
    let range = XLSX.utils.decode_range(ws['!ref']);

    for (let R = range.s.r + 1; R <= range.e.r; ++R) {
        // Column D (index 3): Final Price List - Number format with comma separator
        let cell_d = XLSX.utils.encode_cell({r: R, c: 3});
        if (!ws[cell_d]) ws[cell_d] = {t: 'n', v: 0};
        ws[cell_d].z = '#,##0.00';
        ws[cell_d].t = 'n';

        // Column E (index 4): Final Promo - Number format with comma separator
        let cell_e = XLSX.utils.encode_cell({r: R, c: 4});
        if (!ws[cell_e]) ws[cell_e] = {t: 'n', v: 0};
        ws[cell_e].z = '#,##0.00';
        ws[cell_e].t = 'n';

        // Column F (index 5): Final Sellout Promo - Number format with comma separator
        let cell_f = XLSX.utils.encode_cell({r: R, c: 5});
        if (!ws[cell_f]) ws[cell_f] = {t: 'n', v: 0};
        ws[cell_f].z = '#,##0.00';
        ws[cell_f].t = 'n';

        // Column G (index 6): Invoice Discount - Text format
        let cell_g = XLSX.utils.encode_cell({r: R, c: 6});
        if (ws[cell_g]) {
            ws[cell_g].t = 's'; // Force as string/text
        }

        // Column H (index 7): Cash Discount - Text format
        let cell_h = XLSX.utils.encode_cell({r: R, c: 7});
        if (ws[cell_h]) {
            ws[cell_h].t = 's'; // Force as string/text
        }

        // Column I (index 8): Qty - Text format
        let cell_i = XLSX.utils.encode_cell({r: R, c: 8});
        if (ws[cell_i]) {
            ws[cell_i].t = 's'; // Force as string/text
        }

        // Column J (index 9): Rate - Number format with comma separator
        let cell_j = XLSX.utils.encode_cell({r: R, c: 9});
        if (!ws[cell_j]) ws[cell_j] = {t: 'n', v: 0};
        ws[cell_j].z = '#,##0.00';
        ws[cell_j].t = 'n';
    }

    // Create workbook
    let wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, 'Items');

    // Extract number from document name (e.g., PO-2024-00001 -> 00001)
    let doc_name = frm.doc.name || '';
    let parts = doc_name.split('-');
    let number = parts.length > 0 ? parts[parts.length - 1] : '1';

    // Get current year
    let year = new Date().getFullYear();

    // Generate filename: PUR-ORD-YYYY-NUM
    let filename = 'PUR-ORD-' + year + '-' + number + '.xlsx';

    // Download
    XLSX.writeFile(wb, filename);

    frappe.show_alert({
        message: __('Excel file downloaded successfully'),
        indicator: 'green'
    }, 3);
}

// ====================================================================================
// SCRIPT 6: Purchase Order - Schedule Date (Auto)
// ====================================================================================
// Purpose: Automatically set schedule_date to 21 days from today

frappe.ui.form.on('Purchase Order Item', {
    item_code: function(frm, cdt, cdn) {
        let row = locals[cdt][cdn];
        if (!row.schedule_date) {
            // Set to 21 days from today
            frappe.model.set_value(cdt, cdn, 'schedule_date', frappe.datetime.add_days(frappe.datetime.nowdate(), 21));
        }
    }
});

frappe.ui.form.on('Purchase Order', {
    before_save: function(frm) {
        // Auto-fill schedule_date for all items before saving
        frm.doc.items.forEach(function(item) {
            if (!item.schedule_date) {
                item.schedule_date = frappe.datetime.add_days(frappe.datetime.nowdate(), 21);
            }
        });
    }
});

// ====================================================================================
// SCRIPT 7: Purchase Order - Hide Item Actions
// ====================================================================================
// Purpose: Hide "Create a new Item" and "Advanced Search" options from item dropdown

// Run this globally to hide options whenever they appear
setInterval(function() {
    $('ul[role="listbox"]:visible div[role="option"]').each(function() {
        const title = $(this).find('p').attr('title');
        const hasTextMuted = $(this).find('.text-muted').length > 0;

        if (title === 'Create a new Item' || title === 'Advanced Search' || title === '' || hasTextMuted) {
            $(this).hide();
        }
    });
}, 100);

frappe.ui.form.on('Purchase Order', {
    refresh: function(frm) {
        // Ensure it runs on form load
        setTimeout(function() {
            $('ul[role="listbox"] div[role="option"]').each(function() {
                const title = $(this).find('p').attr('title');
                const hasTextMuted = $(this).find('.text-muted').length > 0;

                if (title === 'Create a new Item' || title === 'Advanced Search' || title === '' || hasTextMuted) {
                    $(this).hide();
                }
            });
        }, 500);
    }
});
