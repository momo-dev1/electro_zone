"""
Purchase Receipt event handlers for electro_zone app
"""

import frappe
import frappe.utils


def auto_populate_rate(doc, method=None):
	"""Auto-populate rate and valuation rate from Purchase Order or Item master.

	Priority:
	1. Purchase Order rate (if PO linked)
	2. Calculated from Item Repeat pricing data
	3. Item valuation_rate
	4. Default to 0

	Args:
		doc: Purchase Receipt document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	for item in doc.items:
		# If rate is not set and we have a PO reference
		if not item.rate and item.purchase_order:
			po_rate = frappe.db.get_value(
				"Purchase Order Item",
				{"parent": item.purchase_order, "item_code": item.item_code},
				"rate",
			)

			if po_rate:
				item.rate = po_rate
				item.valuation_rate = po_rate

		# If still no rate, try Item master
		if not item.rate:
			item_data = frappe.db.get_value(
				"Item",
				item.item_code,
				[
					"valuation_rate",
					"custom_repeat_final_rate_price",
					"custom_repeat_quarter_discount",
					"custom_repeat_yearly_dis",
				],
				as_dict=1,
			)

			if item_data:
				final_rate_price = item_data.get("custom_repeat_final_rate_price") or 0
				quarter_discount = item_data.get("custom_repeat_quarter_discount") or 0
				yearly_discount = item_data.get("custom_repeat_yearly_dis") or 0
				existing_valuation_rate = item_data.get("valuation_rate") or 0

				# If Item has Repeat data, recalculate valuation_rate
				if final_rate_price > 0:
					total_discount_pct = quarter_discount + yearly_discount
					calculated_valuation_rate = final_rate_price - (final_rate_price * total_discount_pct / 100)

					item.rate = calculated_valuation_rate
					item.valuation_rate = calculated_valuation_rate

					# Update Item master if different
					if existing_valuation_rate != calculated_valuation_rate:
						frappe.db.set_value(
							"Item", item.item_code, "valuation_rate", calculated_valuation_rate, update_modified=False
						)
				else:
					# No Repeat data - use existing valuation_rate or default to 0
					if existing_valuation_rate:
						item.rate = existing_valuation_rate
						item.valuation_rate = existing_valuation_rate
					else:
						item.rate = 0
						item.valuation_rate = 0


def validate_received_quantity(doc, method=None):
	"""Validate received quantity and update qty field.

	Ensures:
	1. Received quantity doesn't exceed ordered quantity
	2. Received quantity is greater than 0
	3. Updates qty field to match received quantity
	4. Recalculates amounts based on received quantity

	Args:
		doc: Purchase Receipt document
		method: Event method name (unused, required by Frappe hook signature)

	Raises:
		frappe.ValidationError: If quantity validations fail
	"""
	for item in doc.items:
		# Store original ordered quantity
		if not item.get("ordered_quantity_original"):
			item.ordered_quantity_original = item.qty

		ordered_qty = item.ordered_quantity_original
		received_qty = item.get("custom_received_quantity") or 0

		# Validation: Received quantity cannot exceed ordered quantity
		if received_qty > ordered_qty:
			frappe.throw(
				f"Row #{item.idx}: Received Quantity ({received_qty}) cannot be greater than "
				f"Ordered Quantity ({ordered_qty}) for item {item.item_code}"
			)

		# Validation: Received quantity must be greater than 0
		if received_qty <= 0:
			frappe.throw(f"Row #{item.idx}: Received Quantity must be greater than 0 for item {item.item_code}")

		# Update received/accepted quantities
		item.received_qty = received_qty
		item.accepted_qty = received_qty
		item.rejected_qty = 0

		# Update qty field with received quantity (updates stock and totals)
		item.qty = received_qty

		# Recalculate amounts based on received quantity
		if item.rate:
			item.amount = received_qty * item.rate
			item.base_amount = received_qty * item.base_rate if item.base_rate else item.amount

		# Notify if partial receipt
		if received_qty < ordered_qty:
			frappe.msgprint(
				f"Item {item.item_code}: Received {received_qty} out of {ordered_qty}. "
				"Purchase Order will remain open for the remaining quantity.",
				indicator="orange",
			)

	# Recalculate document totals
	doc.calculate_taxes_and_totals()


def strict_po_validation(doc, method=None):
	"""Validate that Purchase Receipt has valid Purchase Order references.

	Ensures:
	1. All items have Purchase Order reference
	2. Each item exists in its linked Purchase Order

	Args:
		doc: Purchase Receipt document
		method: Event method name (unused, required by Frappe hook signature)

	Raises:
		frappe.ValidationError: If PO validation fails
	"""
	# Validation 1: Check if ANY items have Purchase Order reference
	has_po_reference = False
	for item in doc.items:
		if item.purchase_order:
			has_po_reference = True
			break

	if not has_po_reference:
		frappe.throw(
			"Purchase Receipt cannot be created without a Purchase Order reference. "
			"Please create Purchase Receipt from an existing Purchase Order."
		)

	# Validation 2: Verify each item exists in its linked Purchase Order
	for item in doc.items:
		if not item.purchase_order:
			frappe.throw(
				f"Row #{item.idx}: Item {item.item_code} does not have a Purchase Order reference. "
				"All items must be from an existing Purchase Order."
			)

		# Check if item exists in the linked Purchase Order
		po_item_exists = frappe.db.exists(
			"Purchase Order Item", {"parent": item.purchase_order, "item_code": item.item_code}
		)

		if not po_item_exists:
			frappe.throw(
				f"Row #{item.idx}: Item {item.item_code} is not in the linked Purchase Order {item.purchase_order}. "
				"Only items from the Purchase Order can be received."
			)


def update_item_stock_fields(doc, method=None):
	"""Update Item warehouse stock fields after Purchase Receipt submission.

	Uses robust stock fetching from Bin table with proper error handling.
	Updates custom stock display fields on Item master for multiple warehouses.

	Args:
		doc: Purchase Receipt document
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

	# Get all unique item codes from this purchase receipt
	item_codes = set()
	for item in doc.items:
		if item.item_code:
			item_codes.add(item.item_code)

	# Update stock fields for each item
	for item_code in item_codes:
		if not frappe.db.exists("Item", item_code):
			frappe.log_error(
				f"Item {item_code} does not exist in Item master. Skipping stock update.",
				"Purchase Receipt - Item Not Found",
			)
			continue

		# Batch update dictionary for this item
		update_values = {}

		for field_name, warehouse_name in warehouse_fields.items():
			try:
				# Method 1: Use get_stock_balance for most reliable stock fetching
				# This handles all edge cases (batch, serial, valuation, etc.)
				actual_qty = get_warehouse_stock_robust(item_code, warehouse_name)

				update_values[field_name] = actual_qty

			except Exception as e:
				frappe.log_error(
					f"Failed to fetch stock for Item {item_code} in Warehouse {warehouse_name}: {str(e)}",
					"Purchase Receipt - Stock Fetch Error",
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
					"Purchase Receipt - Item Update Error",
				)


def get_warehouse_stock_robust(item_code, warehouse):
	"""Get actual stock quantity for an item in a warehouse using multiple fallback methods.

	This function provides a robust stock fetching mechanism with multiple fallback strategies:
	1. Direct Bin table query (fastest, most reliable for simple cases)
	2. Warehouse existence validation
	3. Proper error handling and logging

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

		# Method 1: Direct Bin table query (most efficient)
		# This is the most direct way to get actual_qty from the Bin table
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
