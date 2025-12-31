# Copyright (c) 2025, didy1234567@gmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class RebateList(Document):
    def validate(self):
        """Validate required fields and recalculate Final Rate Price"""
        # Validate mandatory discount fields
        if not self.cash_discount and self.cash_discount != 0:
            frappe.throw("Cash Discount (%) is mandatory. Please enter a value (can be 0).")

        if not self.invoice_discount and self.invoice_discount != 0:
            frappe.throw("Invoice Discount (%) is mandatory. Please enter a value (can be 0).")

        if not self.method:
            frappe.throw("Method is mandatory. Please select either 'Gross' or 'Net'.")

        # Validate Final Price List exists
        if not self.final_price_list or self.final_price_list <= 0:
            frappe.throw("Final Price List must be greater than 0. Please select a valid Item Code.")

        # Recalculate Final Rate Price server-side (backup for client calculation)
        final_price_list = self.final_price_list or 0
        cash_discount = self.cash_discount or 0
        invoice_discount = self.invoice_discount or 0

        if self.method == "Gross":
            # Gross Method: Final Price List - (Final Price List * (Cash% + Invoice%))
            total_discount_percent = (cash_discount + invoice_discount) / 100
            self.final_rate_price = final_price_list - (final_price_list * total_discount_percent)

        elif self.method == "Net":
            # Net Method: Apply discounts sequentially
            # Step 1: Apply cash discount
            price_after_cash = final_price_list - (final_price_list * (cash_discount / 100))
            # Step 2: Apply invoice discount to result
            self.final_rate_price = price_after_cash - (price_after_cash * (invoice_discount / 100))

        # Round to 2 decimal places
        self.final_rate_price = round(self.final_rate_price, 2)

        # Validate Final Rate Price
        if self.final_rate_price < 0:
            frappe.throw("Final Rate Price cannot be negative. Please check your discount percentages.")

    def on_submit(self):
        """Update Item Price and Item Repeat tab after submit"""
        self.create_or_update_item_price()
        self.update_item_repeat_tab()

    def create_or_update_item_price(self):
        """Create or update Item Price for Standard Buying price list"""
        # Get all submitted records for this item, ordered by latest first
        all_records = frappe.db.get_all(
            "Rebate List",
            filters={"item_code": self.item_code, "docstatus": 1},  # Only submitted records
            fields=["name", "date", "creation"],
            order_by="date desc, creation desc",
        )

        if not all_records:
            frappe.log_error("No submitted records found after submit", f"Item: {self.item_code}")
        else:
            latest_record = all_records[0]

            # Only update Item Price if this is the latest record
            if latest_record.get("name") == self.name:
                # This IS the latest record - create/update Item Price

                # Check if Item Price already exists for this item + price list
                existing_price = frappe.db.exists(
                    "Item Price", {"item_code": self.item_code, "price_list": "Standard Buying"}
                )

                if existing_price:
                    # Update existing Item Price
                    frappe.db.set_value(
                        "Item Price",
                        existing_price,
                        {"price_list_rate": self.final_rate_price, "valid_from": self.date},
                    )

                    self.add_comment(
                        "Info",
                        f"Item Price UPDATED: Rate = {self.final_rate_price} (Price List: Standard Buying)",
                    )

                    frappe.msgprint(
                        f"✅ Item Price updated!<br>"
                        f"<b>Item:</b> {self.item_code}<br>"
                        f"<b>Price List:</b> Standard Buying<br>"
                        f"<b>Rate:</b> {self.final_rate_price}",
                        indicator="green",
                        alert=True,
                    )
                else:
                    # Create new Item Price record
                    item_price = frappe.new_doc("Item Price")
                    item_price.item_code = self.item_code
                    item_price.price_list = "Standard Buying"
                    item_price.price_list_rate = self.final_rate_price

                    # Get currency from Global Defaults
                    currency = frappe.db.get_single_value("Global Defaults", "default_currency") or "EGP"
                    item_price.currency = currency

                    item_price.valid_from = self.date
                    item_price.flags.ignore_permissions = True
                    item_price.insert()

                    self.add_comment(
                        "Info",
                        f"Item Price CREATED: Rate = {self.final_rate_price} (Price List: Standard Buying)",
                    )

                    frappe.msgprint(
                        f"✅ Item Price created!<br>"
                        f"<b>Item:</b> {self.item_code}<br>"
                        f"<b>Price List:</b> Standard Buying<br>"
                        f"<b>Rate:</b> {self.final_rate_price}<br>"
                        f"<b>Currency:</b> {currency}",
                        indicator="green",
                        alert=True,
                    )
            else:
                # NOT the latest record - skip Item Price update
                self.add_comment("Info", "Item Price NOT updated - A newer Rebate List record exists")

    def update_item_repeat_tab(self):
        """Update Item custom fields (Repeat tab) with latest rebate data"""
        # Get all submitted records for this item, ordered by latest first
        all_records = frappe.db.get_all(
            "Rebate List",
            filters={"item_code": self.item_code, "docstatus": 1},  # Only submitted records
            fields=["name", "date", "creation"],
            order_by="date desc, creation desc",  # Latest date first, then latest creation
        )

        if not all_records:
            # Should not happen, but log just in case
            frappe.log_error("No submitted records found after submit", f"Item: {self.item_code}")
        else:
            # Get the latest record
            latest_record = all_records[0]

            # Check if current document is the latest
            if latest_record.get("name") != self.name:
                # This is NOT the latest record - skip update
                self.add_comment(
                    "Info",
                    f"Repeat data NOT updated - A newer record exists with date: {latest_record.get('date')}",
                )
                frappe.msgprint(
                    f"This record is submitted but NOT the latest.<br>"
                    f"Item's Repeat tab will NOT be updated.<br>"
                    f"Latest record date: {latest_record.get('date')}",
                    indicator="orange",
                    alert=True,
                )
            else:
                # This IS the latest record - update Item custom fields
                frappe.db.set_value(
                    "Item",
                    self.item_code,
                    {
                        "custom_repeat_final_rate_price": self.final_rate_price,
                        "custom_repeat_cash_discount": self.cash_discount,
                        "custom_repeat_invoice_discount": self.invoice_discount,
                        "custom_repeat_quarter_discount": self.quarter_discount or 0,
                        "custom_repeat_yearly_dis": self.yearly_discount or 0,
                        "custom_repeat_method": self.method,
                        "custom_repeat_last_updated": frappe.utils.now(),
                    },
                )

                # Calculate and update valuation_rate based on Repeat discounts
                # Formula: valuation_rate = final_rate_price - quarter_discount - yearly_discount
                # (Both discounts applied separately on original price)
                quarter_discount_pct = self.quarter_discount or 0
                yearly_discount_pct = self.yearly_discount or 0

                # Calculate discount amounts
                quarter_discount_amount = (self.final_rate_price * quarter_discount_pct) / 100
                yearly_discount_amount = (self.final_rate_price * yearly_discount_pct) / 100

                # Calculate valuation rate
                calculated_valuation_rate = (
                    self.final_rate_price - quarter_discount_amount - yearly_discount_amount
                )

                # Update Item valuation_rate
                frappe.db.set_value(
                    "Item", self.item_code, "valuation_rate", calculated_valuation_rate, update_modified=False
                )

                # Add audit trail comment with valuation rate calculation details
                self.add_comment(
                    "Info",
                    f"Item Repeat tab updated: Final Rate Price = {self.final_rate_price}, Method = {self.method}<br>"
                    f"Valuation Rate calculated: {calculated_valuation_rate} "
                    f"(Final: {self.final_rate_price} - Q.Disc: {quarter_discount_amount} - Y.Disc: {yearly_discount_amount})",
                )

                # Show success message to user
                frappe.msgprint(
                    f"✅ Item Repeat tab updated successfully!<br><br>"
                    f"<b>Item:</b> {self.item_code}<br>"
                    f"<b>Final Rate Price:</b> {self.final_rate_price}<br>"
                    f"<b>Cash Discount:</b> {self.cash_discount}%<br>"
                    f"<b>Invoice Discount:</b> {self.invoice_discount}%<br>"
                    f"<b>Method:</b> {self.method}",
                    indicator="green",
                    alert=True,
                )


@frappe.whitelist()
def recalculate_rebate_for_item():
    """
    API method to recalculate rebate for an item based on current final price list.
    Updates the latest submitted Rebate List record, Item Repeat tab, and Item Price.

    Parameters:
        item_code (str): Item Code to recalculate rebate for (passed via frappe.form_dict)

    Returns:
        dict: Response with success status, message, and updated count
    """
    item_code = frappe.form_dict.get("item_code")

    if not item_code:
        frappe.response["message"] = {
            "success": False,
            "message": "Item Code is required",
            "updated_count": 0,
        }
        return

    # Verify item exists
    item_exists = frappe.db.exists("Item", item_code)

    if not item_exists:
        frappe.response["message"] = {
            "success": False,
            "message": f"Item {item_code} not found",
            "updated_count": 0,
        }
        return

    # Get Item's current final_price_list_calculated value
    item_doc = frappe.get_doc("Item", item_code)
    new_final_price_list = item_doc.custom_current_final_price_list_calculated or 0

    if new_final_price_list <= 0:
        frappe.response["message"] = {
            "success": False,
            "message": "Item has no calculated final price list. Cannot update Rebate List records.",
            "updated_count": 0,
        }
        return

    # Get ONLY the latest submitted Rebate List record for this item
    rebate_records = frappe.db.get_all(
        "Rebate List",
        filters={"item_code": item_code, "docstatus": 1},  # Only submitted records
        fields=[
            "name",
            "date",
            "creation",
            "cash_discount",
            "invoice_discount",
            "method",
            "quarter_discount",
            "yearly_discount",
        ],
        order_by="date desc, creation desc",
        limit=1,  # ONLY get the latest record
    )

    if not rebate_records:
        frappe.response["message"] = {
            "success": True,
            "message": "No submitted Rebate List records found for this item.",
            "updated_count": 0,
        }
        return

    # Get the latest (and only) record
    latest_record = rebate_records[0]

    # Calculate new final_rate_price based on method
    cash_discount = latest_record.get("cash_discount") or 0
    invoice_discount = latest_record.get("invoice_discount") or 0
    method = latest_record.get("method")

    if method == "Gross":
        # Gross Method: Final Price List - (Final Price List × (Cash% + Invoice%))
        total_discount_percent = (cash_discount + invoice_discount) / 100
        new_final_rate_price = new_final_price_list - (new_final_price_list * total_discount_percent)
    elif method == "Net":
        # Net Method: Apply discounts sequentially
        price_after_cash = new_final_price_list - (new_final_price_list * (cash_discount / 100))
        new_final_rate_price = price_after_cash - (price_after_cash * (invoice_discount / 100))
    else:
        # Unknown method - cannot update
        frappe.response["message"] = {
            "success": False,
            "message": f"Latest Rebate List record has unknown method: {method}",
            "updated_count": 0,
        }
        return

    # Round to 2 decimal places
    new_final_rate_price = round(new_final_rate_price, 2)

    # Update ONLY the latest record (bypass permissions for submitted docs)
    frappe.db.set_value(
        "Rebate List",
        latest_record.get("name"),
        {"final_price_list": new_final_price_list, "final_rate_price": new_final_rate_price},
        update_modified=False,
    )

    # Update Item's Repeat tab with latest Rebate List values
    frappe.db.set_value(
        "Item",
        item_code,
        {
            "custom_repeat_final_rate_price": new_final_rate_price,
            "custom_repeat_cash_discount": cash_discount,
            "custom_repeat_invoice_discount": invoice_discount,
            "custom_repeat_quarter_discount": latest_record.get("quarter_discount") or 0,
            "custom_repeat_yearly_dis": latest_record.get("yearly_discount") or 0,
            "custom_repeat_method": method,
            "custom_repeat_last_updated": frappe.utils.now(),
        },
    )

    # Update Standard Buying Item Price to sync with latest Rebate List
    # Check if Item Price already exists for this item + price list
    existing_price = frappe.db.exists("Item Price", {"item_code": item_code, "price_list": "Standard Buying"})

    if existing_price:
        # Update existing Item Price
        frappe.db.set_value(
            "Item Price",
            existing_price,
            {"price_list_rate": new_final_rate_price, "valid_from": frappe.utils.today()},
        )
    else:
        # Create new Item Price record
        item_price = frappe.new_doc("Item Price")
        item_price.item_code = item_code
        item_price.price_list = "Standard Buying"
        item_price.price_list_rate = new_final_rate_price

        # Get currency from Global Defaults
        currency = frappe.db.get_single_value("Global Defaults", "default_currency") or "EGP"
        item_price.currency = currency

        item_price.valid_from = frappe.utils.today()
        item_price.flags.ignore_permissions = True
        item_price.insert()

    # Get the full doc to add comment
    latest_record_doc = frappe.get_doc("Rebate List", latest_record.get("name"))
    latest_record_doc.add_comment(
        "Info",
        f"Auto-recalculated: Final Price List updated to {new_final_price_list}, Final Rate Price recalculated to {new_final_rate_price}. Standard Buying Item Price updated.",
    )

    # Return success response
    frappe.response["message"] = {
        "success": True,
        "message": f"Successfully updated latest Rebate List record with new Final Price List: {new_final_price_list} (Final Rate: {new_final_rate_price})",
        "updated_count": 1,
        "new_final_price_list": new_final_price_list,
    }
