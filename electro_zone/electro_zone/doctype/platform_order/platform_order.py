# Copyright (c) 2026, Electro Zone and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime, getdate
from datetime import datetime, timedelta


class PlatformOrder(Document):
    def validate(self):
        """Before Save validation"""
        # Auto-fill Rep Name with current user
        if not self.rep_name:
            self.rep_name = frappe.session.user

        # Auto-create/link customer based on platform
        if self.platform:
            self.customer = get_or_create_platform_customer(self.platform)

        # Calculate totals
        self.calculate_totals()

        # Update match status
        self.update_match_status()

        # Update stock status
        self.update_stock_status()

        # Validate at least one item (matched or unmatched)
        if not self.items and not self.unmatched_items:
            frappe.throw(_("Please add at least one item (matched or unmatched)"))

    def calculate_totals(self):
        """Calculate total quantity and total amount from both matched and unmatched items"""
        total_qty = 0
        total_amount = 0

        # Matched items
        for item in self.items:
            # Calculate item total price
            item.total_price = item.quantity * item.unit_price
            total_qty += item.quantity
            total_amount += item.total_price

        # Unmatched items
        for item in self.unmatched_items:
            # Calculate item total price
            item.total_price = item.quantity * item.unit_price
            total_qty += item.quantity
            total_amount += item.total_price

        self.total_quantity = total_qty
        self.total_amount = total_amount

    def update_match_status(self):
        """Update match_status based on matched and unmatched items"""
        matched_count = len(self.items) if self.items else 0
        unmatched_count = len(self.unmatched_items) if self.unmatched_items else 0

        if unmatched_count > 0:
            # Has unmatched items now
            self.has_unmatched_items = 1
            self.was_unmatched = 1  # Mark that this order was once unmatched

            if matched_count > 0:
                self.match_status = "Partially Matched"
            else:
                self.match_status = "Unmatched"
        else:
            # No unmatched items now
            self.has_unmatched_items = 0

            if matched_count > 0:
                # Check if it was previously unmatched (audit trail)
                if self.was_unmatched:
                    self.match_status = "Matched After Edit"
                else:
                    self.match_status = "Fully Matched"
            else:
                self.match_status = None

    def update_stock_status(self):
        """Update stock_status based on stock availability for matched items"""
        if not self.items:
            self.stock_status = None
            return

        main_warehouse = get_main_warehouse()
        insufficient_items = []
        no_stock_items = []

        for item in self.items:
            available_qty = (
                frappe.db.get_value("Bin", {"item_code": item.item_code, "warehouse": main_warehouse}, "actual_qty")
                or 0
            )

            # Update stock_available on item
            item.stock_available = available_qty

            if available_qty <= 0:
                no_stock_items.append(item.item_code)
            elif available_qty < item.quantity:
                insufficient_items.append(item.item_code)

        if no_stock_items:
            self.stock_status = "No Stock"
        elif insufficient_items:
            self.stock_status = "Insufficient Stock"
        else:
            self.stock_status = "Stock Available"

    def before_submit(self):
        """Before Submit validation"""
        # Block submission if delivery status is Pending
        if self.delivery_status == "Pending":
            frappe.throw(
                _("Cannot submit Platform Order with status Pending. Please mark as Ready to Ship first.")
            )

        # Block submission if there are unmatched items
        if self.has_unmatched_items:
            frappe.throw(
                _("Cannot submit Platform Order with unmatched items. Please match all items or remove unmatched items before submitting.")
            )

        # Block submission if stock status is insufficient or no stock
        if self.stock_status in ["Insufficient Stock", "No Stock"]:
            frappe.throw(
                _("Cannot submit Platform Order with {0}. Please ensure sufficient stock is available.").format(
                    self.stock_status
                )
            )


@frappe.whitelist()
def mark_ready_to_ship(platform_order_name):
    """
    Mark Platform Order as Ready to Ship
    Creates Stock Entry from Zahran Main Warehouse to Hold Warehouse

    Args:
        platform_order_name: Name of the Platform Order document

    Returns:
        dict: Success status and stock entry name
    """
    doc = frappe.get_doc("Platform Order", platform_order_name)

    # Check if already in Ready to Ship or beyond
    if doc.delivery_status != "Pending":
        frappe.throw(_("Can only mark Pending orders as Ready to Ship"))

    # Validate stock availability in Main Warehouse
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
        frappe.throw(_("Insufficient Stock in Main Warehouse:<br>") + "<br>".join(stock_errors))

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
    Creates Stock Entry from Hold Warehouse (Material Issue) and auto-creates Delivery Note

    Args:
        platform_order_name: Name of the Platform Order document

    Returns:
        dict: Success status, stock entry name, and delivery note name
    """
    doc = frappe.get_doc("Platform Order", platform_order_name)

    # Check if in Ready to Ship status
    if doc.delivery_status != "Ready to Ship":
        frappe.throw(_("Can only mark Ready to Ship orders as Shipped"))

    # Validate customer is set
    if not doc.customer:
        frappe.throw(_("Customer is required to create Delivery Note. Please set Customer first."))

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

    # Create Delivery Note
    delivery_note = frappe.new_doc("Delivery Note")
    delivery_note.customer = doc.customer
    delivery_note.posting_date = frappe.utils.nowdate()
    delivery_note.posting_time = frappe.utils.nowtime()
    delivery_note.set_posting_time = 1

    # Add custom field link if exists
    if hasattr(delivery_note, "platform_order"):
        delivery_note.platform_order = doc.name

    # Get company from settings or first available
    company = frappe.defaults.get_defaults().get("company")
    if not company:
        company = frappe.db.get_value("Company", {}, "name")
    delivery_note.company = company

    # Add items to Delivery Note
    for item in doc.items:
        # Get item details
        item_doc = frappe.get_doc("Item", item.item_code)

        delivery_note.append(
            "items",
            {
                "item_code": item.item_code,
                "item_name": item_doc.item_name,
                "description": item_doc.description,
                "qty": item.quantity,
                "uom": item_doc.stock_uom,
                "stock_uom": item_doc.stock_uom,
                "conversion_factor": 1.0,
                "warehouse": hold_warehouse,
                "rate": item.unit_price,
                "amount": item.total_price,
            },
        )

    delivery_note.insert()
    delivery_note.submit()

    # Update Platform Order
    doc.delivery_status = "Shipped"
    doc.shipped_date = now_datetime()
    doc.stock_entry_shipped = stock_entry.name
    doc.delivery_note = delivery_note.name
    doc.flags.ignore_permissions = True
    doc.save()

    frappe.msgprint(
        _("Stock Entry {0} and Delivery Note {1} created. Status updated to Shipped").format(
            stock_entry.name, delivery_note.name
        ),
        indicator="green",
    )

    return {"success": True, "stock_entry": stock_entry.name, "delivery_note": delivery_note.name}


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


def get_or_create_platform_customer(platform):
    """
    Link to existing customer for the platform
    Each platform (Amazon, Noon, Jumia, Other) must have a customer with the same name

    Args:
        platform: Platform name (Amazon, Noon, Jumia, Other)

    Returns:
        str: Customer name

    Raises:
        ValidationError: If customer doesn't exist for the platform
    """
    if not platform:
        return None

    # Check if customer exists with platform name
    customer_name = platform
    if frappe.db.exists("Customer", customer_name):
        return customer_name

    # Customer doesn't exist - throw error
    frappe.throw(
        _("Customer '{0}' does not exist. Please create a customer with name '{0}' first.").format(platform),
        title=_("Customer Not Found")
    )


@frappe.whitelist()
def match_unmatched_item(platform_order, unmatched_item_row_name, item_code):
    """
    Match an unmatched item to an Item Code

    Args:
        platform_order: Name of Platform Order
        unmatched_item_row_name: Row name in unmatched_items table
        item_code: Item Code to match to

    Returns:
        dict: Success status
    """
    doc = frappe.get_doc("Platform Order", platform_order)

    # Find unmatched item
    unmatched_item = None
    for item in doc.unmatched_items:
        if item.name == unmatched_item_row_name:
            unmatched_item = item
            break

    if not unmatched_item:
        frappe.throw(_("Unmatched item not found"))

    # Get item details
    item = frappe.get_doc("Item", item_code)
    main_warehouse = get_main_warehouse()
    stock_qty = (
        frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": main_warehouse}, "actual_qty") or 0
    )

    # Add to matched items
    doc.append(
        "items",
        {
            "item_code": item_code,
            "custom_item_model": item.custom_item_model if hasattr(item, "custom_item_model") else None,
            "description": item.description,
            "asin_sku": unmatched_item.asin_sku,
            "quantity": unmatched_item.quantity,
            "unit_price": unmatched_item.unit_price,
            "total_price": unmatched_item.total_price,
            "stock_available": stock_qty,
        },
    )

    # Remove from unmatched items
    doc.remove(unmatched_item)

    # Save (this will trigger validation and update statuses)
    doc.save()

    return {"success": True, "message": _("Item matched successfully")}


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
            platform_date_raw = row.get("Platform Date", "")
            order_number = str(row.get("Order Number", "")).strip()
            asin_sku = str(row.get("Asin/Sku", "")).strip()
            quantity = float(row.get("Quantity", 0))
            unit_price = float(row.get("Unit Price", 0))
            total_price = float(row.get("Total Price", 0))

            # Convert Excel date to proper format
            platform_date = convert_excel_date(platform_date_raw)

            # Skip if no Asin/SKU
            if not asin_sku:
                continue

            # Find Item from Marketplace Listing by Platform + ASIN/SKU
            item = get_item_from_marketplace_listing(platform, asin_sku) if platform else None

            if item:
                # Validate that matched item has this exact platform+ASIN combination
                if platform:
                    has_listing = validate_item_has_marketplace_listing(item.name, platform, asin_sku)
                    if not has_listing:
                        # Treat as unmatched if validation fails
                        doc.append(
                            "unmatched_items",
                            {
                                "asin_sku": asin_sku,
                                "quantity": quantity,
                                "unit_price": unit_price,
                                "total_price": total_price,
                                "platform": platform,
                                "row_number": row_idx,
                                "notes": f"Item {item.name} found but doesn't have listing for {platform}+{asin_sku}",
                            },
                        )
                        results["unmatched"].append({"row": row_idx, "asin_sku": asin_sku, "quantity": quantity})
                        continue

                # Get stock availability
                stock_qty = (
                    frappe.db.get_value("Bin", {"item_code": item.name, "warehouse": main_warehouse}, "actual_qty")
                    or 0
                )

                item_data = {
                    "item_code": item.name,
                    "custom_item_model": item.custom_item_model,
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
                # Add to unmatched items child table
                doc.append(
                    "unmatched_items",
                    {
                        "asin_sku": asin_sku,
                        "quantity": quantity,
                        "unit_price": unit_price,
                        "total_price": total_price,
                        "platform": platform,
                        "row_number": row_idx,
                    },
                )
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


def get_latest_marketplace_listing_asin(item_code, platform):
    """
    Get ASIN from latest Marketplace Listing for item+platform combination

    Args:
        item_code: Item code to lookup
        platform: Platform name (Amazon, Noon, etc.)

    Returns:
        str: ASIN/SKU from latest listing, or None if not found
    """
    sql = """
        SELECT mpld.asin
        FROM `tabMarketplace Listing` mpl
        INNER JOIN `tabMarketplace Listing Detail` mpld
            ON mpld.parent = mpl.name
        WHERE mpl.item_code = %(item_code)s
            AND mpld.platform = %(platform)s
            AND mpl.docstatus = 1
        ORDER BY mpl.effective_date DESC, mpl.creation DESC
        LIMIT 1
    """
    result = frappe.db.sql(sql, {"item_code": item_code, "platform": platform}, as_dict=True)
    return result[0].asin if result else None


def validate_item_has_marketplace_listing(item_code, platform, asin_sku):
    """
    Check if item has ANY marketplace listing with the exact platform+ASIN combination

    Args:
        item_code: Item code to check
        platform: Platform name (Amazon, Noon, etc.)
        asin_sku: ASIN/SKU to validate

    Returns:
        bool: True if matching listing exists, False otherwise
    """
    sql = """
        SELECT COUNT(*) as count
        FROM `tabMarketplace Listing` mpl
        INNER JOIN `tabMarketplace Listing Detail` mpld
            ON mpld.parent = mpl.name
        WHERE mpl.item_code = %(item_code)s
            AND mpld.platform = %(platform)s
            AND mpld.asin = %(asin_sku)s
            AND mpl.docstatus = 1
    """
    result = frappe.db.sql(sql, {"item_code": item_code, "platform": platform, "asin_sku": asin_sku}, as_dict=True)

    return result[0].count > 0 if result else False


def get_item_from_marketplace_listing(platform, asin_sku):
    """
    Get Item Code from latest Marketplace Listing for platform+ASIN combination

    Args:
        platform: Platform name (Amazon, Noon, etc.)
        asin_sku: ASIN/SKU to lookup

    Returns:
        dict: Item data (name, custom_item_model, description) or None if not found
    """
    sql = """
        SELECT mpl.item_code as name,
               i.custom_item_model,
               i.description
        FROM `tabMarketplace Listing` mpl
        INNER JOIN `tabMarketplace Listing Detail` mpld
            ON mpld.parent = mpl.name
        INNER JOIN `tabItem` i
            ON i.name = mpl.item_code
        WHERE mpld.platform = %(platform)s
            AND mpld.asin = %(asin_sku)s
            AND mpl.docstatus = 1
        ORDER BY mpl.effective_date DESC, mpl.creation DESC
        LIMIT 1
    """
    result = frappe.db.sql(sql, {"platform": platform, "asin_sku": asin_sku}, as_dict=True)
    return result[0] if result else None


def convert_excel_date(excel_date):
    """
    Convert Excel date to Python date string
    Handles: Excel serial numbers, datetime objects, and date strings (dd/mm/yyyy, yyyy-mm-dd)

    Args:
        excel_date: Can be Excel serial number (int/float), datetime object, or date string

    Returns:
        str: Date in YYYY-MM-DD format, or None if conversion fails
    """
    if not excel_date:
        return None

    # If already a string, try to parse it
    if isinstance(excel_date, str):
        excel_date = excel_date.strip()
        if not excel_date:
            return None

        # Try dd/mm/yyyy format first (common Excel export format)
        try:
            parsed_date = datetime.strptime(excel_date, "%d/%m/%Y")
            return parsed_date.strftime("%Y-%m-%d")
        except Exception:
            pass

        # Try yyyy-mm-dd format
        try:
            parsed_date = datetime.strptime(excel_date, "%Y-%m-%d")
            return parsed_date.strftime("%Y-%m-%d")
        except Exception:
            pass

        # Try frappe's getdate utility
        try:
            parsed_date = getdate(excel_date)
            return parsed_date.strftime("%Y-%m-%d")
        except Exception:
            pass

        return None

    # If it's a datetime object
    if isinstance(excel_date, datetime):
        return excel_date.strftime("%Y-%m-%d")

    # If it's an Excel serial number (int or float)
    if isinstance(excel_date, (int, float)):
        try:
            # Excel dates start from 1900-01-01 (serial 1)
            # But Excel incorrectly treats 1900 as a leap year, so serial 60 = 1900-02-29
            # We need to account for this bug
            if excel_date < 1:
                return None

            # Excel epoch is 1899-12-30 (not 1900-01-01 due to the leap year bug)
            excel_epoch = datetime(1899, 12, 30)
            converted_date = excel_epoch + timedelta(days=excel_date)
            return converted_date.strftime("%Y-%m-%d")
        except Exception:
            return None

    return None


@frappe.whitelist()
def bulk_import_platform_orders_from_excel(data):
    """
    Bulk import multiple Platform Orders from Excel data
    Each unique order number creates a separate Platform Order document

    Args:
        data: JSON string containing parsed Excel rows

    Returns:
        dict: Import results with created/failed/warnings
    """
    try:
        import json
        from collections import defaultdict

        # Parse data
        if isinstance(data, str):
            data = json.loads(data)

        # Results tracking
        results = {"created": [], "failed": [], "warnings": [], "summary": {}}

        # Phase 1: Group rows by order number
        orders_data = defaultdict(
            lambda: {"platform": None, "platform_date": None, "order_number": None, "items": []}
        )

        # Get main warehouse for stock checks
        main_warehouse = get_main_warehouse()

        for row_idx, row in enumerate(data, start=2):
            # Extract header data
            platform = str(row.get("Platform", "")).strip()
            platform_date_raw = row.get("Platform Date", "")
            order_number = str(row.get("Order Number", "")).strip()

            # Convert Excel date to proper format
            platform_date = convert_excel_date(platform_date_raw)

            # Skip rows without order number
            if not order_number:
                results["warnings"].append(
                    {"row": row_idx, "type": "missing_order_number", "message": "Row skipped: Missing Order Number"}
                )
                continue

            # Create order key
            order_key = order_number

            # Set header fields (from first occurrence)
            if not orders_data[order_key]["order_number"]:
                orders_data[order_key]["platform"] = platform
                orders_data[order_key]["platform_date"] = platform_date
                orders_data[order_key]["order_number"] = order_number

            # Extract item data
            asin_sku = str(row.get("Asin/Sku", "")).strip()
            quantity = float(row.get("Quantity", 0))
            unit_price = float(row.get("Unit Price", 0))
            total_price = float(row.get("Total Price", 0))

            # Skip if no ASIN/SKU
            if not asin_sku:
                results["warnings"].append(
                    {"row": row_idx, "order": order_number, "type": "missing_asin", "message": "Row skipped: Missing Asin/Sku"}
                )
                continue

            # Find Item from Marketplace Listing by Platform + ASIN/SKU
            item = get_item_from_marketplace_listing(platform, asin_sku) if platform else None

            if not item:
                orders_data[order_key]["items"].append(
                    {
                        "row": row_idx,
                        "matched": False,
                        "asin_sku": asin_sku,
                        "quantity": quantity,
                        "unit_price": unit_price,
                        "total_price": total_price,
                    }
                )
                continue

            # Get stock availability
            stock_qty = (
                frappe.db.get_value("Bin", {"item_code": item.name, "warehouse": main_warehouse}, "actual_qty") or 0
            )

            # Validate that matched item has this exact platform+ASIN combination
            if platform:
                has_listing = validate_item_has_marketplace_listing(item.name, platform, asin_sku)
                if not has_listing:
                    # Get latest ASIN for helpful error message
                    latest_asin = get_latest_marketplace_listing_asin(item.name, platform)
                    results["warnings"].append(
                        {
                            "row": row_idx,
                            "order_number": order_number,
                            "type": "asin_not_found",
                            "item_code": item.name,
                            "excel_asin": asin_sku,
                            "latest_marketplace_asin": latest_asin,
                            "platform": platform,
                            "message": f"Item {item.name} does not have marketplace listing for {platform} with ASIN {asin_sku}. Latest ASIN: {latest_asin or 'None'}",
                        }
                    )

            # Add item data
            orders_data[order_key]["items"].append(
                {
                    "row": row_idx,
                    "matched": True,
                    "item_code": item.name,
                    "custom_item_model": item.custom_item_model,
                    "description": item.description,
                    "asin_sku": asin_sku,
                    "quantity": quantity,
                    "unit_price": unit_price,
                    "total_price": total_price,
                    "stock_available": stock_qty,
                }
            )

        # Phase 2: Create Platform Order documents
        for order_key, order_data in orders_data.items():
            try:
                # Validate required fields
                if not order_data["platform"]:
                    results["failed"].append({"order_number": order_data["order_number"], "error": "Missing Platform"})
                    continue

                if not order_data["platform_date"]:
                    results["failed"].append(
                        {"order_number": order_data["order_number"], "error": "Missing Platform Date"}
                    )
                    continue

                # Check if customer exists for this platform
                if not frappe.db.exists("Customer", order_data["platform"]):
                    results["failed"].append(
                        {
                            "order_number": order_data["order_number"],
                            "error": f"Customer '{order_data['platform']}' does not exist. Please create it first."
                        }
                    )
                    continue

                # Check if order number already exists
                if frappe.db.exists("Platform Order", {"order_number": order_data["order_number"]}):
                    results["failed"].append(
                        {"order_number": order_data["order_number"], "error": "Order Number already exists"}
                    )
                    continue

                # Filter matched items
                matched_items = [item for item in order_data["items"] if item.get("matched")]
                unmatched_items = [item for item in order_data["items"] if not item.get("matched")]

                # Create Platform Order document (allow creation even with only unmatched items)
                doc = frappe.new_doc("Platform Order")
                doc.platform = order_data["platform"]
                doc.platform_date = order_data["platform_date"]
                doc.order_number = order_data["order_number"]
                doc.delivery_status = "Pending"

                # Add matched items
                for item in matched_items:
                    doc.append(
                        "items",
                        {
                            "item_code": item["item_code"],
                            "custom_item_model": item["custom_item_model"],
                            "description": item["description"],
                            "asin_sku": item["asin_sku"],
                            "quantity": item["quantity"],
                            "unit_price": item["unit_price"],
                            "total_price": item["total_price"],
                            "stock_available": item["stock_available"],
                        },
                    )

                    # Stock warning
                    if item["stock_available"] < item["quantity"]:
                        results["warnings"].append(
                            {
                                "order_number": order_data["order_number"],
                                "item_code": item["item_code"],
                                "type": "low_stock",
                                "required": item["quantity"],
                                "available": item["stock_available"],
                                "short": item["quantity"] - item["stock_available"],
                            }
                        )

                # Add unmatched items
                for item in unmatched_items:
                    doc.append(
                        "unmatched_items",
                        {
                            "asin_sku": item["asin_sku"],
                            "quantity": item["quantity"],
                            "unit_price": item["unit_price"],
                            "total_price": item["total_price"],
                            "platform": order_data["platform"],
                            "row_number": item["row"],
                        },
                    )

                # Insert document (validation will set match_status and stock_status)
                doc.insert()

                # Track success
                results["created"].append(
                    {
                        "name": doc.name,
                        "order_number": doc.order_number,
                        "items_count": len(matched_items),
                        "unmatched_count": len(unmatched_items),
                    }
                )

                # Track unmatched items as warnings
                if unmatched_items:
                    for item in unmatched_items:
                        results["warnings"].append(
                            {
                                "order_number": order_data["order_number"],
                                "platform_order": doc.name,
                                "row": item["row"],
                                "type": "item_not_found",
                                "asin_sku": item["asin_sku"],
                                "message": f"Item not found for ASIN/SKU: {item['asin_sku']}",
                            }
                        )

            except Exception as e:
                error_msg = str(e)
                order_num = order_data.get("order_number", "Unknown")[:50]  # Limit length
                frappe.log_error(
                    title=f"Bulk Import - Order {order_num}",
                    message=f"Order: {order_data.get('order_number', 'Unknown')}\nError: {error_msg}",
                )
                results["failed"].append(
                    {"order_number": order_data.get("order_number", "Unknown"), "error": error_msg}
                )

        # Build summary
        results["summary"] = {
            "total_orders_in_file": len(orders_data),
            "created": len(results["created"]),
            "failed": len(results["failed"]),
            "warnings": len(results["warnings"]),
        }

        return {"success": True, "results": results}

    except Exception as e:
        frappe.log_error(title="Platform Order Bulk Import", message=f"Fatal Error: {str(e)}")
        return {"success": False, "message": str(e)}
