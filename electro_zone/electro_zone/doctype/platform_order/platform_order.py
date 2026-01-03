# Copyright (c) 2026, Electro Zone and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime


class PlatformOrder(Document):
    def validate(self):
        """Before Save validation"""
        # Auto-fill Rep Name with current user
        if not self.rep_name:
            self.rep_name = frappe.session.user

        # Calculate totals
        self.calculate_totals()

        # Validate at least one item
        if not self.items:
            frappe.throw(_("Please add at least one item"))

    def calculate_totals(self):
        """Calculate total quantity and total amount"""
        total_qty = 0
        total_amount = 0

        for item in self.items:
            # Calculate item total price
            item.total_price = item.quantity * item.unit_price
            total_qty += item.quantity
            total_amount += item.total_price

        self.total_quantity = total_qty
        self.total_amount = total_amount

    def before_submit(self):
        """Before Submit validation"""
        if self.delivery_status == "Pending":
            frappe.throw(
                _("Cannot submit Platform Order with status Pending. Please mark as Ready to Ship first.")
            )


@frappe.whitelist()
def mark_ready_to_ship(platform_order_name):
    """
    Mark Platform Order as Ready to Ship
    Creates Stock Entry from Main Warehouse to Hold Warehouse

    Args:
        platform_order_name: Name of the Platform Order document

    Returns:
        dict: Success status and stock entry name
    """
    doc = frappe.get_doc("Platform Order", platform_order_name)

    # Check if already in Ready to Ship or beyond
    if doc.delivery_status != "Pending":
        frappe.throw(_("Can only mark Pending orders as Ready to Ship"))

    # Validate stock availability
    stock_errors = []
    main_warehouse = get_main_warehouse()

    for item in doc.items:
        available_qty = frappe.db.get_value(
            "Bin", {"item_code": item.item_code, "warehouse": main_warehouse}, "actual_qty"
        ) or 0

        if available_qty < item.quantity:
            stock_errors.append(
                _("Item {0}: Required {1}, Available {2}").format(item.item_code, item.quantity, available_qty)
            )

    if stock_errors:
        frappe.throw(_("Insufficient Stock:<br>") + "<br>".join(stock_errors))

    # Create Stock Entry: Main Warehouse → Hold Warehouse
    hold_warehouse = get_hold_warehouse()
    stock_entry = frappe.new_doc("Stock Entry")
    stock_entry.stock_entry_type = "Material Transfer"
    stock_entry.from_warehouse = main_warehouse
    stock_entry.to_warehouse = hold_warehouse

    # Add custom field link if exists
    if hasattr(stock_entry, "platform_order"):
        stock_entry.platform_order = doc.name

    for item in doc.items:
        stock_entry.append(
            "items",
            {
                "item_code": item.item_code,
                "qty": item.quantity,
                "s_warehouse": main_warehouse,
                "t_warehouse": hold_warehouse,
                "basic_rate": item.unit_price,
            },
        )

    stock_entry.insert()
    stock_entry.submit()

    # Update Platform Order
    doc.delivery_status = "Ready to Ship"
    doc.ready_to_ship_date = now_datetime()
    doc.stock_entry_ready = stock_entry.name
    doc.flags.ignore_permissions = True
    doc.save()

    frappe.msgprint(
        _("Stock Entry {0} created. Status updated to Ready to Ship").format(stock_entry.name), indicator="green"
    )

    return {"success": True, "stock_entry": stock_entry.name}


@frappe.whitelist()
def mark_shipped(platform_order_name):
    """
    Mark Platform Order as Shipped
    Creates Stock Entry from Hold Warehouse (Material Issue)

    Args:
        platform_order_name: Name of the Platform Order document

    Returns:
        dict: Success status and stock entry name
    """
    doc = frappe.get_doc("Platform Order", platform_order_name)

    # Check if in Ready to Ship status
    if doc.delivery_status != "Ready to Ship":
        frappe.throw(_("Can only mark Ready to Ship orders as Shipped"))

    # Validate stock in Hold Warehouse
    stock_errors = []
    hold_warehouse = get_hold_warehouse()

    for item in doc.items:
        available_qty = frappe.db.get_value(
            "Bin", {"item_code": item.item_code, "warehouse": hold_warehouse}, "actual_qty"
        ) or 0

        if available_qty < item.quantity:
            stock_errors.append(
                _("Item {0}: Required {1}, Available in Hold {2}").format(
                    item.item_code, item.quantity, available_qty
                )
            )

    if stock_errors:
        frappe.throw(_("Insufficient Stock in Hold Warehouse:<br>") + "<br>".join(stock_errors))

    # Create Stock Entry: Hold Warehouse → Material Issue
    stock_entry = frappe.new_doc("Stock Entry")
    stock_entry.stock_entry_type = "Material Issue"
    stock_entry.from_warehouse = hold_warehouse

    # Add custom field link if exists
    if hasattr(stock_entry, "platform_order"):
        stock_entry.platform_order = doc.name

    for item in doc.items:
        stock_entry.append(
            "items",
            {"item_code": item.item_code, "qty": item.quantity, "s_warehouse": hold_warehouse, "basic_rate": item.unit_price},
        )

    stock_entry.insert()
    stock_entry.submit()

    # Update Platform Order
    doc.delivery_status = "Shipped"
    doc.shipped_date = now_datetime()
    doc.stock_entry_shipped = stock_entry.name
    doc.flags.ignore_permissions = True
    doc.save()

    frappe.msgprint(_("Stock Entry {0} created. Status updated to Shipped").format(stock_entry.name), indicator="green")

    return {"success": True, "stock_entry": stock_entry.name}


@frappe.whitelist()
def bulk_update_status(platform_orders, new_status):
    """
    Bulk update delivery status for multiple Platform Orders

    Args:
        platform_orders: JSON list of Platform Order names
        new_status: New delivery status

    Returns:
        dict: Results with updated and failed counts
    """
    import json

    if isinstance(platform_orders, str):
        platform_orders = json.loads(platform_orders)

    updated = []
    failed = []

    allowed_statuses = ["Pending", "Ready to Ship", "Shipped", "Delivered", "Canceled", "Delivery Failed", "Returned"]

    if new_status not in allowed_statuses:
        frappe.throw(_("Invalid status: {0}").format(new_status))

    for po_name in platform_orders:
        try:
            doc = frappe.get_doc("Platform Order", po_name)

            # Validation: Can't use bulk update for Ready to Ship or Shipped
            # (those require stock entries)
            if new_status in ["Ready to Ship", "Shipped"]:
                failed.append({"name": po_name, "error": "Use individual buttons for Ready to Ship/Shipped status"})
                continue

            # Update status
            doc.delivery_status = new_status
            doc.flags.ignore_permissions = True
            doc.save()
            updated.append(po_name)

        except Exception as e:
            failed.append({"name": po_name, "error": str(e)})

    return {"updated": len(updated), "failed": len(failed), "details": {"updated": updated, "failed": failed}}


def get_main_warehouse():
    """Get the main warehouse name"""
    # Try to find Zahran Main warehouse
    warehouse = frappe.db.get_value("Warehouse", {"warehouse_name": ["like", "%Main%"]}, "name")
    if warehouse:
        return warehouse

    # Fallback to first warehouse
    warehouse = frappe.db.get_value("Warehouse", {}, "name")
    if warehouse:
        return warehouse

    frappe.throw(_("No warehouse found. Please create a warehouse first."))


def get_hold_warehouse():
    """Get the hold warehouse name"""
    # Try to find Hold warehouse
    warehouse = frappe.db.get_value("Warehouse", {"warehouse_name": ["like", "%Hold%"]}, "name")
    if warehouse:
        return warehouse

    # Try to create Hold warehouse if it doesn't exist
    try:
        company = frappe.defaults.get_defaults().get("company")
        if not company:
            company = frappe.db.get_value("Company", {}, "name")

        hold_wh = frappe.get_doc(
            {"doctype": "Warehouse", "warehouse_name": "Hold", "company": company, "is_group": 0}
        )
        hold_wh.insert(ignore_permissions=True)
        return hold_wh.name
    except Exception:
        frappe.throw(_("Hold warehouse not found. Please create a 'Hold' warehouse first."))


@frappe.whitelist()
def import_platform_orders_from_excel(data, platform_order_name):
    """
    Import platform order items from parsed Excel data

    Args:
        data: JSON string containing parsed Excel rows
        platform_order_name: Name of the Platform Order document

    Returns:
        dict: Import results with matched/unmatched items
    """
    try:
        import json

        # Parse the data if it's a string
        if isinstance(data, str):
            data = json.loads(data)

        # Get Platform Order document
        doc = frappe.get_doc("Platform Order", platform_order_name)

        results = {"matched": [], "unmatched": [], "stock_warnings": []}

        # Get main warehouse
        main_warehouse = get_main_warehouse()

        for row_idx, row in enumerate(data, start=2):
            # Extract data from row object
            platform = str(row.get("Platform", "")).strip()
            platform_date = row.get("Platform Date", "")
            order_number = str(row.get("Order Number", "")).strip()
            asin_sku = str(row.get("Asin/Sku", "")).strip()
            quantity = float(row.get("Quantity", 0))
            unit_price = float(row.get("Unit Price", 0))
            total_price = float(row.get("Total Price", 0))

            # Skip if no Asin/SKU
            if not asin_sku:
                continue

            # Find Item by platform_asin_sku
            item = frappe.db.get_value(
                "Item", {"platform_asin_sku": asin_sku}, ["name", "item_model", "description"], as_dict=True
            )

            if item:
                # Get stock availability
                stock_qty = (
                    frappe.db.get_value("Bin", {"item_code": item.name, "warehouse": main_warehouse}, "actual_qty")
                    or 0
                )

                item_data = {
                    "item_code": item.name,
                    "item_model": item.item_model,
                    "description": item.description,
                    "asin_sku": asin_sku,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "total_price": total_price,
                    "stock_available": stock_qty,
                }

                # Add to child table
                doc.append("items", item_data)

                results["matched"].append(
                    {"row": row_idx, "asin_sku": asin_sku, "item_code": item.name, "quantity": quantity, "stock": stock_qty}
                )

                # Stock warning
                if stock_qty < quantity:
                    results["stock_warnings"].append(
                        {
                            "row": row_idx,
                            "item_code": item.name,
                            "required": quantity,
                            "available": stock_qty,
                            "short": quantity - stock_qty,
                        }
                    )
            else:
                results["unmatched"].append({"row": row_idx, "asin_sku": asin_sku, "quantity": quantity})

            # Update header fields from first data row
            if not doc.platform and platform:
                doc.platform = platform
            if not doc.platform_date and platform_date:
                doc.platform_date = platform_date
            if not doc.order_number and order_number:
                doc.order_number = order_number

        # Save document
        doc.save()

        message = _("Imported {0} items").format(len(results["matched"]))
        if results["unmatched"]:
            message += _(", {0} not found").format(len(results["unmatched"]))
        if results["stock_warnings"]:
            message += _(", {0} with low stock").format(len(results["stock_warnings"]))

        return {"success": True, "results": results, "message": message}

    except Exception as e:
        frappe.log_error(f"Excel Import Error: {str(e)}", "Platform Order Excel Import")
        return {"success": False, "message": str(e)}
