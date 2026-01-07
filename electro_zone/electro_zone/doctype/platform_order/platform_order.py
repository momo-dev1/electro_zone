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
    if frappe.db.has_column("Stock Entry", "platform_order"):
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
    Creates Sales Invoice with Update Stock enabled (deducts from Hold Warehouse)
    Auto-submits Sales Invoice

    Args:
        platform_order_name: Name of the Platform Order document

    Returns:
        dict: Success status and sales invoice name
    """
    doc = frappe.get_doc("Platform Order", platform_order_name)

    # Validation: Must be in Ready to Ship status
    if doc.delivery_status != "Ready to Ship":
        frappe.throw(_("Platform Order must be in 'Ready to Ship' status to mark as shipped"))

    # Validation: Must be submitted
    if doc.docstatus != 1:
        frappe.throw(_("Platform Order must be submitted before marking as shipped"))

    # Validate customer is set
    if not doc.customer:
        frappe.throw(_("Customer is required to create Sales Invoice. Please set Customer first."))

    # Get company and warehouses
    company = frappe.defaults.get_defaults().get("company") or frappe.db.get_single_value("Global Defaults", "default_company")
    if not company:
        company = frappe.db.get_value("Company", {}, "name")
    hold_warehouse = get_hold_warehouse()

    # Validate stock availability in Hold Warehouse
    stock_errors = []
    for item in doc.items:
        stock_qty = frappe.db.get_value(
            "Bin",
            {"item_code": item.item_code, "warehouse": hold_warehouse},
            "actual_qty"
        ) or 0

        if stock_qty < item.quantity:
            stock_errors.append(
                _("Item {0}: Required {1}, Available {2}").format(
                    item.item_code, item.quantity, stock_qty
                )
            )

    if stock_errors:
        frappe.throw(_("Insufficient stock in Hold Warehouse:<br>") + "<br>".join(stock_errors))

    # Create Sales Invoice with Update Stock enabled
    sales_invoice = frappe.new_doc("Sales Invoice")
    sales_invoice.customer = doc.customer
    sales_invoice.posting_date = frappe.utils.nowdate()
    sales_invoice.posting_time = frappe.utils.nowtime()
    sales_invoice.set_posting_time = 1
    sales_invoice.company = company

    # Enable Update Stock (this will auto-create stock ledger entries)
    sales_invoice.update_stock = 1
    sales_invoice.set_warehouse = hold_warehouse  # Deduct from Hold Warehouse

    # Link back to Platform Order (if custom field exists)
    if frappe.db.has_column("Sales Invoice", "platform_order"):
        sales_invoice.platform_order = doc.name

    # Add items (only unit_price * quantity - NO shipping/commission)
    for item in doc.items:
        item_doc = frappe.get_doc("Item", item.item_code)

        sales_invoice.append("items", {
            "item_code": item.item_code,
            "item_name": item_doc.item_name,
            "description": item_doc.description or item_doc.item_name,
            "qty": item.quantity,
            "uom": item_doc.stock_uom,
            "stock_uom": item_doc.stock_uom,
            "conversion_factor": 1.0,
            "warehouse": hold_warehouse,  # Source warehouse for stock deduction
            "rate": item.unit_price,
            "amount": item.quantity * item.unit_price,
        })

    try:
        sales_invoice.insert()
        sales_invoice.submit()

        # Update Platform Order
        doc.sales_invoice = sales_invoice.name
        doc.delivery_status = "Shipped"
        doc.shipped_date = frappe.utils.now()
        doc.flags.ignore_permissions = True
        doc.save()

        frappe.msgprint(_("Sales Invoice {0} created and submitted successfully").format(sales_invoice.name))

        return {
            "success": True,
            "sales_invoice": sales_invoice.name,
            "message": "Order marked as Shipped and Sales Invoice created"
        }

    except Exception as e:
        frappe.log_error(
            f"Sales Invoice creation failed for {doc.name}: {str(e)}",
            "Platform Order Sales Invoice Error"
        )
        frappe.throw(_("Failed to create Sales Invoice: {0}").format(str(e)))


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


# Platform-specific Excel column mappings
PLATFORM_EXCEL_MAPPINGS = {
    "Amazon": {
        "order_number": "amazon-order-id",
        "platform_date": "purchase-date",
        "asin_sku": "asin",
        "quantity": "quantity",
        "unit_price": "item-price",
        "shipping_price": "shipping-price",
        "ship_promotion_discount": "ship-promotion-discount",
        "status_filter": None,
        "status_value": None,
        "default_qty": None,
    },
    "Noon": {
        "order_number": "purchase_item_nr",
        "platform_date": "fulfillment_timestamp",
        "asin_sku": "sku",
        "quantity": "quantity",
        "status_filter": "order_status",
        "status_value": None,
        "default_qty": None,
    },
    "Jumia": {
        "order_number": "Order Number",
        "platform_date": "Updated At",
        "asin_sku": "Sku",
        "unit_price": "Unit Price",
        "shipping_fees": "Shipping Fee",
        "customer_first_name": "Customer First Name",
        "customer_last_name": "Customer Last Name",
        "status_filter": "Status",
        "status_value": "ready to ship",
        "default_qty": 1,  # Jumia always qty = 1
    },
    "Homzmart": {
        "order_number": ["itemid", "orderId"],  # Concatenated
        "platform_date": "addedDate",
        "asin_sku": "itemSku",
        "quantity": "itemQty",
        "unit_price": "itemPrice",
        "shipping_fees": "itemShippingFees",
        "cod_fees": "cod_fees",
        "grand_total": "itemGrandTotal",
        "customer_name": "customerName",
        "customer_mobile": "customerMobile",
        "customer_address": "customer_address",
        "customer_region": "customer_region",
        "status_filter": "status",
        "status_value": "ready to ship",
        "default_qty": None,
    },
}


# Platform detection patterns - unique columns that identify each platform
PLATFORM_DETECTION_PATTERNS = {
    "Amazon": {
        "required_columns": ["amazon-order-id", "asin"],  # Must have these
        "optional_columns": ["item-price", "shipping-price", "ship-promotion-discount", "purchase-date", "quantity"],  # Nice to have
        "min_match": 2,  # Need at least 2 columns to confirm
    },
    "Noon": {
        "required_columns": ["order_nr", "sku"],
        "optional_columns": ["fulfillment_timestamp", "order_status"],
        "min_match": 2,
    },
    "Jumia": {
        "required_columns": ["Sku", "Order Number"],  # Note: capital S in Sku
        "optional_columns": ["Updated At", "Shipping Fee", "Unit Price", "Customer First Name", "Customer Last Name", "Status"],
        "min_match": 2,
    },
    "Homzmart": {
        "required_columns": ["itemid", "itemSku"],
        "optional_columns": ["orderId", "itemQty", "itemPrice", "itemShippingFees", "itemGrandTotal", "customerName", "customerMobile", "customer_address", "customer_region", "cod_fees", "addedDate", "status"],
        "min_match": 2,
    },
}


def get_excel_value(row, platform, field_name):
    """Extract value from Excel row based on platform-specific mapping"""
    import re

    mapping = PLATFORM_EXCEL_MAPPINGS.get(platform, {})
    column_name = mapping.get(field_name)

    if not column_name:
        return None

    # Handle concatenated fields (Homzmart order_number)
    if isinstance(column_name, list):
        values = [str(row.get(col, "")).strip() for col in column_name]
        return "-".join(values) if all(values) else None

    # Single column
    value = row.get(column_name)

    # Handle default values (e.g., Jumia qty = 1)
    if value is None or value == "":
        default_value = mapping.get(f"default_{field_name}")
        if default_value is not None:
            return default_value

    # Special handling for Noon's order_number (purchase_item_nr): Remove -P1, -P2, etc. suffix
    if platform == "Noon" and field_name == "order_number" and value:
        value = str(value).strip()
        # Remove -Pn suffix (where n is one or more digits)
        value = re.sub(r'-P\d+$', '', value)

    return value


def calculate_shipping_fees(row, platform):
    """
    Calculate shipping fees based on platform-specific logic

    For Amazon: shipping-price - ship-promotion-discount
    For other platforms: use direct shipping_fees field

    Args:
        row: Excel row data
        platform: Platform name

    Returns:
        float: Calculated shipping fees
    """
    if platform == "Amazon":
        # For Amazon: shipping_fees = shipping-price - ship-promotion-discount
        shipping_price = float(get_excel_value(row, platform, "shipping_price") or 0)
        ship_promotion_discount = float(get_excel_value(row, platform, "ship_promotion_discount") or 0)
        return shipping_price - ship_promotion_discount
    else:
        # For other platforms: use direct shipping_fees mapping
        return float(get_excel_value(row, platform, "shipping_fees") or 0)


def should_import_row(row, platform):
    """Check if row should be imported based on platform status filter"""
    mapping = PLATFORM_EXCEL_MAPPINGS.get(platform, {})
    status_filter = mapping.get("status_filter")

    if not status_filter:
        return True

    status_value = mapping.get("status_value")
    if not status_value:
        return True

    row_status = str(row.get(status_filter, "")).strip().lower()
    required_status = str(status_value).lower()

    return row_status == required_status


def detect_platform_from_columns(column_headers):
    """
    Detect platform by analyzing column headers

    Args:
        column_headers: List of column names from Excel sheet

    Returns:
        str: Platform name ("Amazon", "Noon", "Jumia", "Homzmart") or None
    """
    if not column_headers:
        return None

    # Normalize column headers (strip whitespace, preserve case)
    normalized_headers = [str(col).strip() for col in column_headers]

    # Score each platform based on column matches
    platform_scores = {}

    for platform, pattern in PLATFORM_DETECTION_PATTERNS.items():
        score = 0
        required_matches = 0

        # Check required columns
        for req_col in pattern["required_columns"]:
            if req_col in normalized_headers:
                required_matches += 1
                score += 10  # High weight for required columns

        # Check optional columns
        for opt_col in pattern["optional_columns"]:
            if opt_col in normalized_headers:
                score += 1  # Lower weight for optional

        # Only consider if minimum required columns are matched
        if required_matches >= pattern["min_match"]:
            platform_scores[platform] = score

    # Return platform with highest score
    if platform_scores:
        return max(platform_scores, key=platform_scores.get)

    return None


def detect_noon_import_type(column_headers):
    """
    Detect if Noon Excel is for order import, price update, or customer name update

    Args:
        column_headers: List of column names from Excel

    Returns:
        str: "order_import", "price_update", "customer_name_update", or None
    """
    if not column_headers:
        return None

    # Normalize column headers (strip whitespace, lowercase, remove extra spaces)
    normalized = [" ".join(str(col).strip().lower().split()) for col in column_headers]

    # Check customer name update pattern (Source Doc Line Nr, Receiver Legal Entity)
    has_source_doc = any("source doc line nr" in col or col == "source doc line nr" for col in normalized)
    has_receiver = any("receiver legal entity" in col or col == "receiver legal entity" for col in normalized)

    if has_source_doc and has_receiver:
        return "customer_name_update"

    # Check price update pattern (item_nr, offer_price, status)
    if all(col in normalized for col in ["item_nr", "offer_price", "status"]):
        return "price_update"

    # Check order import pattern (purchase_item_nr, sku, quantity)
    if "purchase_item_nr" in normalized or "sku" in normalized:
        return "order_import"

    return None


def filter_columns_by_platform(row, platform):
    """
    Filter row to only include columns defined in platform mapping

    Args:
        row: Dictionary of all columns from Excel
        platform: Detected platform name

    Returns:
        dict: Filtered row with only relevant columns
    """
    if not platform or platform not in PLATFORM_EXCEL_MAPPINGS:
        return row

    mapping = PLATFORM_EXCEL_MAPPINGS[platform]
    filtered_row = {}

    # Keep standard column if exists
    if "Platform" in row:
        filtered_row["Platform"] = row["Platform"]

    # Include only mapped columns
    for field_name, column_name in mapping.items():
        if field_name.startswith("status_") or field_name.startswith("default_"):
            continue  # Skip config keys

        if isinstance(column_name, list):
            # Handle concatenated columns (Homzmart)
            for col in column_name:
                if col in row:
                    filtered_row[col] = row[col]
        elif column_name and column_name in row:
            filtered_row[column_name] = row[column_name]

    return filtered_row


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
            # Extract platform (standard column)
            platform = str(row.get("Platform", "")).strip()

            # Skip if status filter doesn't match
            if platform and not should_import_row(row, platform):
                continue

            # Extract platform-specific fields
            platform_date_raw = get_excel_value(row, platform, "platform_date") or row.get("Platform Date", "")
            order_number = get_excel_value(row, platform, "order_number") or str(row.get("Order Number", "")).strip()
            asin_sku = get_excel_value(row, platform, "asin_sku") or str(row.get("Asin/Sku", "")).strip()
            quantity = float(get_excel_value(row, platform, "quantity") or row.get("Quantity", 0))
            unit_price = float(get_excel_value(row, platform, "unit_price") or row.get("Unit Price", 0))
            shipping_fees = calculate_shipping_fees(row, platform)
            commission = float(get_excel_value(row, platform, "commission") or 0)
            total_price = float(row.get("Total Price", 0))  # Fallback to row value if exists

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
                                "shipping_fees": shipping_fees,
                                "commission": commission,
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
                    "shipping_fees": shipping_fees,
                    "commission": commission,
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
                        "shipping_fees": shipping_fees,
                        "commission": commission,
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

            # Update customer_name from first data row
            if not doc.customer_name:
                # Try direct customer_name first (Homzmart)
                customer_name = get_excel_value(row, platform, "customer_name")
                if customer_name:
                    doc.customer_name = str(customer_name).strip()
                else:
                    # Fallback to concatenate first and last name (Jumia)
                    customer_first_name = get_excel_value(row, platform, "customer_first_name") or ""
                    customer_last_name = get_excel_value(row, platform, "customer_last_name") or ""
                    if customer_first_name or customer_last_name:
                        doc.customer_name = f"{customer_first_name} {customer_last_name}".strip()

            # Update additional customer fields (Homzmart)
            if not doc.customer_mobile:
                customer_mobile = get_excel_value(row, platform, "customer_mobile")
                if customer_mobile:
                    doc.customer_mobile = str(customer_mobile).strip()

            if not doc.customer_address:
                customer_address = get_excel_value(row, platform, "customer_address")
                if customer_address:
                    doc.customer_address = str(customer_address).strip()

            if not doc.customer_region:
                customer_region = get_excel_value(row, platform, "customer_region")
                if customer_region:
                    doc.customer_region = str(customer_region).strip()

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
            lambda: {
                "platform": None,
                "platform_date": None,
                "order_number": None,
                "customer_name": None,
                "customer_mobile": None,
                "customer_address": None,
                "customer_region": None,
                "items": []
            }
        )

        # Get main warehouse for stock checks
        main_warehouse = get_main_warehouse()

        for row_idx, row in enumerate(data, start=2):
            # Extract platform (standard column)
            platform = str(row.get("Platform", "")).strip()

            # Skip if status filter doesn't match
            if platform and not should_import_row(row, platform):
                continue

            # Extract platform-specific fields
            platform_date_raw = get_excel_value(row, platform, "platform_date") or row.get("Platform Date", "")
            order_number = get_excel_value(row, platform, "order_number") or str(row.get("Order Number", "")).strip()

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

                # Extract customer name (Homzmart has direct customerName, Jumia has first/last name)
                customer_name = get_excel_value(row, platform, "customer_name")
                if customer_name:
                    orders_data[order_key]["customer_name"] = str(customer_name).strip()
                else:
                    # Fallback to first_name + last_name for platforms like Jumia
                    customer_first_name = get_excel_value(row, platform, "customer_first_name") or ""
                    customer_last_name = get_excel_value(row, platform, "customer_last_name") or ""
                    if customer_first_name or customer_last_name:
                        orders_data[order_key]["customer_name"] = f"{customer_first_name} {customer_last_name}".strip()

                # Extract additional customer fields (Homzmart)
                customer_mobile = get_excel_value(row, platform, "customer_mobile")
                if customer_mobile:
                    orders_data[order_key]["customer_mobile"] = str(customer_mobile).strip()

                customer_address = get_excel_value(row, platform, "customer_address")
                if customer_address:
                    orders_data[order_key]["customer_address"] = str(customer_address).strip()

                customer_region = get_excel_value(row, platform, "customer_region")
                if customer_region:
                    orders_data[order_key]["customer_region"] = str(customer_region).strip()

            # Extract item data with platform-specific mappings
            asin_sku = get_excel_value(row, platform, "asin_sku") or str(row.get("Asin/Sku", "")).strip()
            quantity = float(get_excel_value(row, platform, "quantity") or row.get("Quantity", 0))
            unit_price = float(get_excel_value(row, platform, "unit_price") or row.get("Unit Price", 0))
            shipping_fees = calculate_shipping_fees(row, platform)
            commission = float(get_excel_value(row, platform, "commission") or 0)
            total_price = float(row.get("Total Price", 0))  # Fallback to row value if exists

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
                        "shipping_fees": shipping_fees,
                        "commission": commission,
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
                    "shipping_fees": shipping_fees,
                    "commission": commission,
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
                existing_order = frappe.db.get_value(
                    "Platform Order",
                    {"order_number": order_data["order_number"]},
                    ["name", "platform", "delivery_status"],
                    as_dict=True
                )
                if existing_order:
                    results["failed"].append({
                        "order_number": order_data["order_number"],
                        "error": f"Order already exists: {existing_order.name} (Platform: {existing_order.platform}, Status: {existing_order.delivery_status})"
                    })
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

                # Set customer fields if available
                if order_data.get("customer_name"):
                    doc.customer_name = order_data["customer_name"]
                if order_data.get("customer_mobile"):
                    doc.customer_mobile = order_data["customer_mobile"]
                if order_data.get("customer_address"):
                    doc.customer_address = order_data["customer_address"]
                if order_data.get("customer_region"):
                    doc.customer_region = order_data["customer_region"]

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


@frappe.whitelist()
def update_prices_from_noon_excel(data):
    """
    Update unit_price for Noon Platform Orders from price Excel

    Args:
        data: JSON string with rows containing [item_nr, offer_price, status]

    Returns:
        dict: Update results with summary and details
    """
    try:
        import json

        if isinstance(data, str):
            data = json.loads(data)

        results = {
            "updated_items": [],
            "skipped_items": [],
            "not_found": [],
            "errors": [],
            "summary": {}
        }

        processing_rows = 0

        for row_idx, row in enumerate(data, start=2):
            # Extract columns
            status = str(row.get("status", "")).strip().lower()
            item_nr = str(row.get("item_nr", "")).strip()
            offer_price = row.get("offer_price")

            # Filter by status
            if status != "processing":
                continue

            processing_rows += 1

            # Validate offer_price
            try:
                offer_price = float(offer_price)
            except (ValueError, TypeError):
                results["errors"].append({
                    "row": row_idx,
                    "item_nr": item_nr,
                    "error": f"Invalid offer_price: {offer_price}"
                })
                continue

            # Find Platform Orders with matching order_number
            platform_orders = frappe.get_all(
                "Platform Order",
                filters={
                    "order_number": item_nr,
                    "platform": "Noon"
                },
                fields=["name", "docstatus"]
            )

            if not platform_orders:
                results["not_found"].append({
                    "row": row_idx,
                    "item_nr": item_nr
                })
                continue

            # Update each matching order
            for po in platform_orders:
                # Skip submitted orders
                if po.docstatus == 1:
                    results["skipped_items"].append({
                        "row": row_idx,
                        "item_nr": item_nr,
                        "order": po.name,
                        "reason": "Order is submitted - cannot modify"
                    })
                    continue

                try:
                    doc = frappe.get_doc("Platform Order", po.name)
                    updated_count = 0

                    # Update matched items
                    for item in doc.items:
                        if item.unit_price in [None, 0, 0.0]:
                            old_price = item.unit_price or 0
                            item.unit_price = offer_price
                            updated_count += 1
                            results["updated_items"].append({
                                "row": row_idx,
                                "order": doc.name,
                                "item_nr": item_nr,
                                "item_code": item.item_code,
                                "old_price": old_price,
                                "new_price": offer_price
                            })
                        else:
                            results["skipped_items"].append({
                                "row": row_idx,
                                "order": doc.name,
                                "item_nr": item_nr,
                                "item_code": item.item_code,
                                "reason": f"Price already set ({item.unit_price})",
                                "current_price": item.unit_price
                            })

                    # Update unmatched items
                    for item in doc.unmatched_items:
                        if item.unit_price in [None, 0, 0.0]:
                            old_price = item.unit_price or 0
                            item.unit_price = offer_price
                            updated_count += 1
                            results["updated_items"].append({
                                "row": row_idx,
                                "order": doc.name,
                                "item_nr": item_nr,
                                "asin_sku": item.asin_sku,
                                "old_price": old_price,
                                "new_price": offer_price
                            })
                        else:
                            results["skipped_items"].append({
                                "row": row_idx,
                                "order": doc.name,
                                "item_nr": item_nr,
                                "asin_sku": item.asin_sku,
                                "reason": f"Price already set ({item.unit_price})",
                                "current_price": item.unit_price
                            })

                    # Save if any items were updated
                    if updated_count > 0:
                        doc.save()

                except Exception as e:
                    results["errors"].append({
                        "row": row_idx,
                        "item_nr": item_nr,
                        "order": po.name,
                        "error": str(e)
                    })
                    frappe.db.rollback()

        # Build summary
        results["summary"] = {
            "total_rows": len(data),
            "processing_rows": processing_rows,
            "orders_updated": len(set(item["order"] for item in results["updated_items"])),
            "items_updated": len(results["updated_items"]),
            "items_skipped": len(results["skipped_items"]),
            "not_found": len(results["not_found"]),
            "errors": len(results["errors"])
        }

        return {"success": True, "results": results}

    except Exception as e:
        frappe.log_error(title="Noon Price Update", message=f"Fatal Error: {str(e)}")
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def update_customer_names_from_noon_excel(data):
    """
    Update customer_name for Noon Platform Orders from customer name Excel

    Args:
        data: JSON string with rows containing [Source Doc Line Nr, Receiver Legal Entity]

    Returns:
        dict: Update results with summary and details
    """
    try:
        import json
        import re

        if isinstance(data, str):
            data = json.loads(data)

        results = {
            "updated_orders": [],
            "not_found": [],
            "errors": [],
            "summary": {}
        }

        for row_idx, row in enumerate(data, start=2):
            # Extract columns (check both possible column names)
            source_doc_line_nr = str(row.get("Source Doc Line Nr", "")).strip()
            receiver_legal_entity = str(row.get("Receiver Legal Entity", "") or row.get("Receiver Legal Name", "")).strip()

            if not source_doc_line_nr:
                continue

            # Remove -Pn suffix (same logic as purchase_item_nr)
            order_number = re.sub(r'-P\d+$', '', source_doc_line_nr)

            if not receiver_legal_entity:
                results["errors"].append({
                    "row": row_idx,
                    "order_number": order_number,
                    "error": "Receiver Legal Entity is empty"
                })
                continue

            # Find Platform Orders with matching order_number
            platform_orders = frappe.get_all(
                "Platform Order",
                filters={
                    "order_number": order_number,
                    "platform": "Noon"
                },
                fields=["name", "docstatus", "customer_name"]
            )

            if not platform_orders:
                results["not_found"].append({
                    "row": row_idx,
                    "order_number": order_number
                })
                continue

            # Update each matching order
            for po in platform_orders:
                # Skip submitted orders
                if po.docstatus == 1:
                    results["errors"].append({
                        "row": row_idx,
                        "order_number": order_number,
                        "order": po.name,
                        "error": "Order is submitted - cannot modify"
                    })
                    continue

                try:
                    doc = frappe.get_doc("Platform Order", po.name)
                    old_customer_name = doc.customer_name or ""

                    # Update customer_name
                    doc.customer_name = receiver_legal_entity
                    doc.save()

                    results["updated_orders"].append({
                        "row": row_idx,
                        "order": doc.name,
                        "order_number": order_number,
                        "old_customer_name": old_customer_name,
                        "new_customer_name": receiver_legal_entity
                    })

                except Exception as e:
                    results["errors"].append({
                        "row": row_idx,
                        "order_number": order_number,
                        "order": po.name,
                        "error": str(e)
                    })
                    frappe.db.rollback()

        # Build summary
        results["summary"] = {
            "total_rows": len(data),
            "orders_updated": len(results["updated_orders"]),
            "not_found": len(results["not_found"]),
            "errors": len(results["errors"])
        }

        return {"success": True, "results": results}

    except Exception as e:
        frappe.log_error(title="Noon Customer Name Update", message=f"Fatal Error: {str(e)}")
        return {"success": False, "message": str(e)}


@frappe.whitelist()
def process_multi_sheet_excel(sheets_data):
    """
    Process multiple sheets from Excel file with auto-detection

    Args:
        sheets_data: JSON string containing array of {sheet_name, data} objects

    Returns:
        dict: Results summary with created orders, warnings, errors per sheet
    """
    import json

    if isinstance(sheets_data, str):
        sheets_data = json.loads(sheets_data)

    results = {
        "sheets_processed": 0,
        "sheets_skipped": 0,
        "total_orders_created": 0,
        "sheet_results": [],
        "warnings": [],
        "errors": [],
    }

    for sheet_info in sheets_data:
        sheet_name = sheet_info.get("sheet_name", "Unknown")
        data = sheet_info.get("data", [])

        if not data or len(data) == 0:
            results["sheets_skipped"] += 1
            results["warnings"].append({"sheet": sheet_name, "message": "Sheet is empty - skipped"})
            continue

        # Detect platform OR import type from first row headers
        first_row = data[0]
        column_headers = list(first_row.keys())

        # First check if it's a Noon price update
        noon_import_type = detect_noon_import_type(column_headers)

        if noon_import_type == "price_update":
            # Route to price update handler
            try:
                update_result = update_prices_from_noon_excel(data)

                if update_result.get("success"):
                    res = update_result["results"]
                    results["sheets_processed"] += 1
                    results["sheet_results"].append({
                        "sheet_name": sheet_name,
                        "import_type": "price_update",
                        "platform": "Noon",
                        "items_updated": res["summary"]["items_updated"],
                        "items_skipped": res["summary"]["items_skipped"],
                        "not_found": res["summary"]["not_found"],
                        "warnings": res.get("errors", [])
                    })
                else:
                    results["sheets_skipped"] += 1
                    results["errors"].append({
                        "sheet": sheet_name,
                        "import_type": "price_update",
                        "error": update_result.get("message", "Unknown error")
                    })

            except Exception as e:
                results["sheets_skipped"] += 1
                results["errors"].append({
                    "sheet": sheet_name,
                    "import_type": "price_update",
                    "error": str(e)
                })
                frappe.log_error(
                    f"Price update failed for {sheet_name}: {str(e)}",
                    "Noon Price Update Error"
                )
            continue

        if noon_import_type == "customer_name_update":
            # Route to customer name update handler
            try:
                update_result = update_customer_names_from_noon_excel(data)

                if update_result.get("success"):
                    res = update_result["results"]
                    results["sheets_processed"] += 1
                    results["sheet_results"].append({
                        "sheet_name": sheet_name,
                        "import_type": "customer_name_update",
                        "platform": "Noon",
                        "orders_updated": res["summary"]["orders_updated"],
                        "not_found": res["summary"]["not_found"],
                        "warnings": res.get("errors", [])
                    })
                else:
                    results["sheets_skipped"] += 1
                    results["errors"].append({
                        "sheet": sheet_name,
                        "import_type": "customer_name_update",
                        "error": update_result.get("message", "Unknown error")
                    })

            except Exception as e:
                results["sheets_skipped"] += 1
                results["errors"].append({
                    "sheet": sheet_name,
                    "import_type": "customer_name_update",
                    "error": str(e)
                })
                frappe.log_error(
                    f"Customer name update failed for {sheet_name}: {str(e)}",
                    "Noon Customer Name Update Error"
                )
            continue

        # Existing platform detection for order import
        detected_platform = detect_platform_from_columns(column_headers)

        if not detected_platform:
            results["sheets_skipped"] += 1
            results["warnings"].append({
                "sheet": sheet_name,
                "message": f"Could not detect platform from columns: {', '.join(column_headers[:5])}...",
            })
            continue

        # Add platform to each row
        for row in data:
            row["Platform"] = detected_platform

        # Call existing bulk import with enhanced data
        try:
            import_result = bulk_import_platform_orders_from_excel(data)

            # Extract created orders count from import result
            created_count = 0
            failed_count = 0
            if import_result.get("success") and import_result.get("results"):
                res = import_result["results"]
                created_count = len(res.get("created", []))
                failed_count = len(res.get("failed", []))

            results["sheets_processed"] += 1
            results["total_orders_created"] += created_count
            results["sheet_results"].append({
                "sheet_name": sheet_name,
                "import_type": "order_import",
                "platform": detected_platform,
                "orders_created": created_count,
                "orders_failed": failed_count,
                "warnings": import_result.get("results", {}).get("warnings", []),
            })

        except Exception as e:
            results["sheets_skipped"] += 1
            results["errors"].append({"sheet": sheet_name, "platform": detected_platform, "error": str(e)})
            frappe.log_error(
                f"Multi-sheet import failed for {sheet_name}: {str(e)}", "Platform Order Multi-Sheet Import Error"
            )

    return results
