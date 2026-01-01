"""
Stock Entry event handlers for electro_zone app
"""

import frappe
import frappe.utils


def update_item_stock_fields(doc, method=None):
	"""Update Item warehouse stock fields after Stock Entry submission.

	Uses robust stock fetching from Bin table with proper error handling.
	Ensures both source AND target warehouses show correct quantities in transfers.
	Runs after ALL ledger entries and Bin updates are committed.

	Args:
		doc: Stock Entry document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	# Define warehouse mappings (custom_field_name: warehouse_name)
	warehouse_fields = {
		"custom_stock_store_display": "Store Display - EZ",
		"custom_stock_store_warehouse": "Store Warehouse - EZ",
		"custom_stock_damage": "Damage - EZ",
		"custom_stock_damage_for_sale": "Damage For Sale - EZ",
		"custom_stock_zahran_main": "Zahran Main - EZ",
		"custom_stock_hold": "Hold (Reserved / Pending Shipment) - EZ",
	}

	# Get all unique item codes from this stock entry
	item_codes = set()
	for item in doc.items:
		if item.item_code:
			item_codes.add(item.item_code)

	# Update stock fields for each item
	for item_code in item_codes:
		if not frappe.db.exists("Item", item_code):
			frappe.log_error(
				f"Item {item_code} does not exist in Item master. Skipping stock update.",
				"Stock Entry - Item Not Found",
			)
			continue

		# Batch update dictionary for this item
		update_values = {}

		for field_name, warehouse_name in warehouse_fields.items():
			try:
				# Use robust stock fetching (handles all edge cases)
				actual_qty = get_warehouse_stock_robust(item_code, warehouse_name)
				update_values[field_name] = actual_qty

			except Exception as e:
				frappe.log_error(
					f"Failed to fetch stock for Item {item_code} in Warehouse {warehouse_name}: {str(e)}",
					"Stock Entry - Stock Fetch Error",
				)
				# Set to 0 on error to avoid stale data
				update_values[field_name] = 0

		# Batch update all fields at once (more efficient than individual updates)
		if update_values:
			try:
				frappe.db.set_value("Item", item_code, update_values, update_modified=False)
			except Exception as e:
				frappe.log_error(
					f"Failed to update stock fields for Item {item_code}: {str(e)}",
					"Stock Entry - Item Update Error",
				)


def get_warehouse_stock_robust(item_code, warehouse):
	"""Get actual stock quantity for an item in a warehouse using robust error handling.

	This function provides a robust stock fetching mechanism with:
	1. Direct Bin table query (fastest, most reliable)
	2. Warehouse existence validation
	3. Proper error handling and logging
	4. Returns 0 for missing data (safe fallback)

	Args:
		item_code: Item code to fetch stock for
		warehouse: Warehouse name

	Returns:
		float: Actual quantity in warehouse (0 if not found or error)
	"""
	try:
		# First, validate that warehouse exists
		warehouse_exists = frappe.db.exists("Warehouse", warehouse)
		if not warehouse_exists:
			frappe.log_error(
				f"Warehouse '{warehouse}' does not exist. Cannot fetch stock for {item_code}.",
				"Stock Fetch - Warehouse Not Found",
			)
			return 0

		# Direct Bin table query (most efficient and reliable)
		bin_data = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty")

		if bin_data is not None:
			return frappe.utils.flt(bin_data)

		# If no Bin record exists, stock is 0
		# This is normal for items that have never been in this warehouse
		return 0

	except Exception as e:
		# Log any unexpected errors
		frappe.log_error(
			f"Unexpected error fetching stock for {item_code} in {warehouse}: {str(e)}",
			"Stock Fetch - Unexpected Error",
		)
		return 0
