"""
Purchase Order event handlers for electro_zone app
"""

import frappe


# ============================================================================
# API METHODS (Whitelisted for client-side access)
# ============================================================================


@frappe.whitelist()
def get_po_ordered_qty(po_reference=None, item_code=None):
	"""Get ordered quantity from Purchase Order for barcode scanning.

	Runs with elevated permissions (bypasses Stock User restrictions).

	Args:
		po_reference: Purchase Order name
		item_code: Item code

	Returns:
		dict: Success status and ordered quantity or error
	"""
	if not po_reference or not item_code:
		return {"success": False, "error": "Missing po_reference or item_code"}

	# Get ordered quantity from Purchase Order Item
	ordered_qty = frappe.db.get_value("Purchase Order Item", {"parent": po_reference, "item_code": item_code}, "qty")

	if ordered_qty:
		return {"success": True, "ordered_qty": ordered_qty}
	else:
		return {"success": False, "error": "Item not found in Purchase Order"}


# ============================================================================
# EVENT HANDLERS
# ============================================================================


def validate_supplier_items(doc, method=None):
	"""Validate that all items in PO belong to selected supplier.

	Ensures each item's custom_primary_supplier matches the PO supplier.

	Event: Before Save

	Args:
		doc: Purchase Order document
		method: Event method name (unused, required by Frappe hook signature)

	Raises:
		frappe.ValidationError: If item doesn't belong to supplier
	"""
	for item in doc.items:
		if item.item_code:
			# Get the item's custom_primary_supplier
			item_doc = frappe.get_doc("Item", item.item_code)

			# Use the get() method or check if field exists
			custom_primary_supplier = item_doc.get("custom_primary_supplier")

			if custom_primary_supplier:
				# Check if the item's custom_primary_supplier matches the PO supplier
				if custom_primary_supplier != doc.supplier:
					frappe.throw(
						f"Item {item.item_code} is not linked to supplier {doc.supplier}. "
						f"This item is linked to {custom_primary_supplier}. "
						"Please select items that are linked to the selected supplier."
					)


def auto_sync_standard_buying_on_item_add(doc, method=None):
	"""Auto-create/update Standard Buying Item Price for items added to PO.

	Ensures old items (historical data) have Standard Buying prices before PO creation.
	Prevents stale data in Purchase Orders.

	Workflow:
	1. Loop through all items in Purchase Order
	2. Check if Standard Buying Item Price exists
	3. If missing but custom_repeat_final_rate_price > 0: Auto-create Item Price
	4. Log action in PO comments for audit trail

	Event: Before Save

	Args:
		doc: Purchase Order document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	# Track items that were auto-synced for logging
	auto_synced_items = []

	# Loop through all items in the Purchase Order
	for item in doc.items:
		item_code = item.item_code

		if not item_code:
			continue

		# Check if Standard Buying Item Price exists
		existing_price = frappe.db.exists("Item Price", {"item_code": item_code, "price_list": "Standard Buying"})

		if not existing_price:
			# Item Price doesn't exist - check if we can create it
			try:
				# Get Item's repeat final rate price
				item_data = frappe.db.get_value(
					"Item",
					item_code,
					["custom_repeat_final_rate_price", "custom_repeat_last_updated"],
					as_dict=True,
				)

				if not item_data:
					continue

				final_rate_price = item_data.get("custom_repeat_final_rate_price")

				# Only create if item has a valid repeat price
				if final_rate_price and final_rate_price > 0:
					# Create new Item Price record for Standard Buying
					item_price = frappe.new_doc("Item Price")
					item_price.item_code = item_code
					item_price.price_list = "Standard Buying"
					item_price.price_list_rate = final_rate_price

					# Get currency from Global Defaults
					currency = frappe.db.get_single_value("Global Defaults", "default_currency") or "EGP"
					item_price.currency = currency

					# Set valid_from date
					item_price.valid_from = item_data.get("custom_repeat_last_updated") or frappe.utils.now()

					# Insert with permission bypass (system operation)
					item_price.flags.ignore_permissions = True
					item_price.insert()

					# Track for logging
					auto_synced_items.append(f"{item_code} ({final_rate_price})")

			except Exception as e:
				# Log error but don't block PO save
				frappe.log_error(
					f"Failed to auto-create Standard Buying price for {item_code}: {str(e)}",
					"PO Auto-Sync Error",
				)

	# Add comment to PO if items were auto-synced
	if auto_synced_items:
		doc.add_comment(
			"Info",
			f"Auto-created Standard Buying prices for: {', '.join(auto_synced_items[:5])}"
			+ (f" and {len(auto_synced_items) - 5} more" if len(auto_synced_items) > 5 else ""),
		)


def sync_price_edit_status(doc, method=None):
	"""Update Price Edit Status field.

	Updates custom_price_edit_status based on whether any item has manual price editing enabled.

	Event: Before Save

	Args:
		doc: Purchase Order document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	if not doc.items:
		doc.custom_price_edit_status = "Automatic"
	else:
		# Check if ANY item has manual edit enabled
		has_manual_edit = False
		for item in doc.items:
			if item.get("custom_allow_manual_price_edit") == 1:
				has_manual_edit = True
				break

		# Set status based on result
		if has_manual_edit:
			doc.custom_price_edit_status = "Manually Edited"
		else:
			doc.custom_price_edit_status = "Automatic"
