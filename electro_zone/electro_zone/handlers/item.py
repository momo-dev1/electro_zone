"""
Item event handlers for electro_zone app
"""

import frappe


# ============================================================================
# API METHODS (Whitelisted for client-side access)
# ============================================================================


@frappe.whitelist()
def item_list_get_items_with_stock():
	"""Fetch item details and warehouse stock quantities as JSON.

	Returns all items with stock quantities across 6 warehouses.

	Returns:
		dict: Response with success status, items list, warehouses, and count
	"""
	# Define warehouse list
	warehouses = [
		"Store Display - EZ",
		"Store Warehouse - EZ",
		"Damage - EZ",
		"Damage For Sale - EZ",
		"Zahran Main - EZ",
		"Hold (Reserved / Pending Shipment) - EZ",
	]

	# Get all items with required fields
	items = frappe.db.get_all(
		"Item", filters={"is_stock_item": 1}, fields=["item_code", "custom_item_model", "description"], order_by="item_code"
	)

	# Prepare result array
	result = []

	for item in items:
		# Create row with item details
		row = {
			"item_code": item.get("item_code"),
			"custom_item_model": item.get("custom_item_model") or "",
			"description": item.get("description") or "",
		}

		# Get stock quantities for each warehouse
		for warehouse in warehouses:
			# Query Bin doctype for actual_qty
			qty = frappe.db.get_value("Bin", {"item_code": item.get("item_code"), "warehouse": warehouse}, "actual_qty") or 0

			# Add warehouse column to row
			row[warehouse] = qty

		result.append(row)

	# Return JSON response
	return {"success": True, "items": result, "warehouses": warehouses, "total_count": len(result)}


@frappe.whitelist()
def sync_standard_buying_from_item(item_code=None, item_codes=None):
	"""Manually sync Standard Buying Item Price from Item's custom_repeat_final_rate_price.

	Useful when Item Repeat tab is manually edited.
	Supports single item or batch processing.

	Args:
		item_code: Single item code to sync
		item_codes: Multiple item codes for batch processing

	Returns:
		dict: Success status, message, updated count, and errors
	"""
	# Convert single item to list
	if item_code and not item_codes:
		item_codes = [item_code]

	if not item_codes:
		return {"success": False, "message": "No item codes provided", "updated_count": 0}

	updated_count = 0
	errors = []

	for code in item_codes:
		try:
			# Get Item's repeat final rate price
			item_data = frappe.db.get_value(
				"Item", code, ["custom_repeat_final_rate_price", "custom_repeat_last_updated"], as_dict=True
			)

			if not item_data:
				errors.append(f"{code}: Item not found")
				continue

			final_rate_price = item_data.get("custom_repeat_final_rate_price")

			if final_rate_price is None or final_rate_price == 0:
				errors.append(f"{code}: No repeat final rate price set")
				continue

			# Check if Item Price exists for Standard Buying
			existing_price = frappe.db.exists("Item Price", {"item_code": code, "price_list": "Standard Buying"})

			if existing_price:
				# Update existing Item Price
				frappe.db.set_value(
					"Item Price",
					existing_price,
					{
						"price_list_rate": final_rate_price,
						"valid_from": item_data.get("custom_repeat_last_updated") or frappe.utils.now(),
					},
				)
			else:
				# Create new Item Price record
				item_price = frappe.new_doc("Item Price")
				item_price.item_code = code
				item_price.price_list = "Standard Buying"
				item_price.price_list_rate = final_rate_price

				# Get currency from Global Defaults
				currency = frappe.db.get_single_value("Global Defaults", "default_currency") or "EGP"
				item_price.currency = currency

				item_price.valid_from = item_data.get("custom_repeat_last_updated") or frappe.utils.now()
				item_price.flags.ignore_permissions = True
				item_price.insert()

			updated_count += 1

		except Exception as e:
			errors.append(f"{code}: {str(e)}")

	# Return result
	if updated_count > 0:
		return {
			"success": True,
			"message": f"Successfully updated {updated_count} item(s). " + (f"Errors: {', '.join(errors[:3])}" if errors else ""),
			"updated_count": updated_count,
			"errors": errors,
		}
	else:
		return {
			"success": False,
			"message": f"No items updated. Errors: {', '.join(errors[:5])}",
			"updated_count": 0,
			"errors": errors,
		}


# ============================================================================
# EVENT HANDLERS
# ============================================================================


def auto_assign_supplier_from_brand(doc, method=None):
	"""Auto-assign default supplier from Brand to Item if not already set.

	Event: Before Save (Submitted Document)

	Args:
		doc: Item document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	if doc.brand and not doc.get("default_supplier"):
		supplier = frappe.db.get_value("Brand", doc.brand, "default_supplier")
		if supplier:
			doc.default_supplier = supplier
			frappe.msgprint(f"Supplier auto-assigned: {supplier}")


def validate_uniqueness(doc, method=None):
	"""Validate uniqueness of (Brand, Item Group, Item Model) combination.

	# Moved from electro_zone.electro_zone.validations (consolidated into handlers)

	Ensures that no two Items share the same combination of Brand, Item Group,
	and custom_item_model fields. This prevents duplicate product entries.

	Event: Before Save

	Args:
		doc: Item document being validated
		method: Event method name (unused, required by Frappe hook signature)

	Raises:
		frappe.ValidationError: If an Item with the same Brand, Item Group,
			and Model combination already exists

	Example:
		If Item "ITEM-001" has Brand="Samsung", Item Group="Smartphones",
		and custom_item_model="Galaxy S21", attempting to create another Item
		with the same combination will raise:
		"Item with Brand 'Samsung', Item Group 'Smartphones', and Model 'Galaxy S21'
		already exists: ITEM-001"
	"""
	# Skip validation if any required field is missing
	if not (doc.brand and doc.item_group and doc.get("custom_item_model")):
		return

	# Build filter for uniqueness check
	filters = {
		"brand": doc.brand,
		"item_group": doc.item_group,
		"custom_item_model": doc.get("custom_item_model"),
	}

	# Exclude current document when updating (not creating new)
	if not doc.is_new():
		filters["name"] = ["!=", doc.name]

	# Check if duplicate exists
	existing = frappe.db.exists("Item", filters)

	if existing:
		frappe.throw(
			f"Item with Brand '{doc.brand}', Item Group '{doc.item_group}', "
			f"and Model '{doc.get('custom_item_model')}' already exists: {existing}"
		)
