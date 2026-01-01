"""
API endpoints for electro_zone app

This module contains whitelisted API functions migrated from Server Scripts.
All functions are accessible via /api/method/<function_name>
"""

import frappe


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


@frappe.whitelist()
def receive_dn_return(dn_return_name=None):
	"""Transition DN Return from Return Issued to Return Received.

	Creates Credit Note, updates balance, returns stock.

	Args:
		dn_return_name: Delivery Note Return name

	Returns:
		dict: Success status and message
	"""
	if not dn_return_name:
		return {"success": False, "message": "DN Return name is required"}

	# Get DN Return document
	dn_return = frappe.get_doc("Delivery Note", dn_return_name)

	# Validate it's a return and in correct status
	if dn_return.is_return != 1:
		return {"success": False, "message": "This is not a DN Return"}

	if dn_return.custom_return_status != "Return Issued":
		return {"success": False, "message": "DN Return must be in 'Return Issued' status"}

	# Find the original Sales Invoice
	sales_invoices = frappe.db.get_all(
		"Sales Invoice Item", filters={"delivery_note": dn_return.return_against}, fields=["parent"], distinct=True
	)

	if not sales_invoices:
		return {"success": False, "message": f"No Sales Invoice found for original DN: {dn_return.return_against}"}

	original_si_name = sales_invoices[0].parent

	# Check if Credit Note already exists
	existing_credit_note = frappe.db.get_all(
		"Sales Invoice",
		filters={"is_return": 1, "return_against": original_si_name, "docstatus": ["in", [0, 1]]},
		limit=1,
	)

	if existing_credit_note:
		return {"success": False, "message": f"Credit Note already exists: {existing_credit_note[0].name}"}

	try:
		# STEP 1: Get original source warehouse from Sales Order
		so_name = None
		original_source_warehouse = None

		# Find SO from original DN (not DN Return)
		so_names = frappe.db.sql(
			"""
			SELECT DISTINCT dni.against_sales_order
			FROM `tabDelivery Note Item` dni
			WHERE dni.parent = %s
				AND dni.against_sales_order IS NOT NULL
				AND dni.against_sales_order != ''
		""",
			(dn_return.return_against,),
			as_dict=1,
		)

		if so_names and len(so_names) > 0:
			so_name = so_names[0].against_sales_order
			original_source_warehouse = frappe.db.get_value("Sales Order", so_name, "custom_source_warehouse")

		if not original_source_warehouse:
			frappe.throw(
				f"Cannot find original source warehouse from Sales Order {so_name or 'Unknown'}. "
				"Returns must go to the original source warehouse, not Hold warehouse."
			)

		# STEP 2: Update DN Return items' warehouse to original source warehouse
		for item in dn_return.items:
			frappe.db.set_value("Delivery Note Item", item.name, "warehouse", original_source_warehouse)

		# Reload DN Return to get updated warehouse values
		dn_return.reload()

		# STEP 3: Update DN Return status to "Return Received"
		frappe.db.set_value(
			"Delivery Note",
			dn_return_name,
			{"custom_return_status": "Return Received", "status": "Return Received", "workflow_state": "Return Received"},
		)

		# STEP 4: Create Credit Note
		credit_note = frappe.new_doc("Sales Invoice")
		credit_note.customer = dn_return.customer
		credit_note.company = dn_return.company
		credit_note.posting_date = frappe.utils.nowdate()
		credit_note.posting_time = frappe.utils.nowtime()
		credit_note.set_posting_time = 1
		credit_note.is_return = 1
		credit_note.return_against = original_si_name
		credit_note.custom_from_receive_return_api = 1
		credit_note.customer_name = dn_return.customer_name

		if dn_return.get("contact_mobile"):
			credit_note.contact_mobile = dn_return.contact_mobile

		# Copy items from DN Return
		for item in dn_return.items:
			credit_note.append(
				"items",
				{
					"item_code": item.item_code,
					"item_name": item.item_name,
					"description": item.description,
					"qty": item.qty,
					"uom": item.uom,
					"stock_uom": item.stock_uom,
					"conversion_factor": item.conversion_factor,
					"rate": item.rate,
					"amount": item.amount,
					"warehouse": item.warehouse,
					"delivery_note": dn_return.name,
					"dn_detail": item.name,
				},
			)

		# Insert and submit Credit Note
		credit_note.insert()
		credit_note.submit()

		# STEP 5: Update Sales Order
		if so_name:
			frappe.db.set_value(
				"Sales Order",
				so_name,
				{
					"custom_is_returned": 1,
					"custom_return_date": frappe.utils.nowdate(),
					"custom_return_reference": dn_return_name,
					"status": "Closed",
				},
			)

		# Get updated balance for response
		updated_balance = frappe.db.get_value("Customer", dn_return.customer, "custom_current_balance") or 0

		return {
			"success": True,
			"message": f"Return received successfully. Credit Note: {credit_note.name}, Customer Balance: {updated_balance}",
		}

	except Exception as e:
		# Rollback DN Return status if Credit Note creation fails
		frappe.db.set_value("Delivery Note", dn_return_name, {"custom_return_status": "Return Issued", "status": "Return Issued"})

		frappe.log_error(f"DN Return Error: {str(e)}", "DN Return Processing Failed")
		return {"success": False, "message": f"Failed to process return: {str(e)}"}


@frappe.whitelist()
def get_customer_by_phone(phone_number=None):
	"""Search customer by phone from Address table via customer_primary_address.

	Args:
		phone_number: Phone number to search (partial match supported)

	Returns:
		dict: Success status, customers list, and count
	"""
	if not phone_number:
		return {"success": False, "message": "Phone number is required"}

	# Search customer by phone in Address table
	customers = frappe.db.sql(
		"""
		SELECT DISTINCT
			c.name,
			c.customer_name,
			a.phone as mobile_no,
			c.email_id,
			c.customer_group,
			c.territory,
			c.custom_current_balance
		FROM `tabCustomer` c
		INNER JOIN `tabAddress` a ON c.customer_primary_address = a.name
		WHERE a.phone LIKE %s
		AND c.disabled = 0
	""",
		(f"%{phone_number}%",),
		as_dict=1,
	)

	if not customers:
		return {"success": False, "message": f"No customer found with phone number: {phone_number}"}

	# Get additional details for each customer
	for customer in customers:
		# Get total sales
		total_sales = frappe.db.sql(
			"""
			SELECT
				COUNT(*) as total_orders,
				SUM(grand_total) as total_amount
			FROM `tabSales Order`
			WHERE customer = %s
			AND docstatus = 1
		""",
			(customer.name,),
			as_dict=1,
		)

		if total_sales and len(total_sales) > 0:
			customer["total_orders"] = total_sales[0].get("total_orders", 0)
			customer["total_sales_amount"] = total_sales[0].get("total_amount", 0)
		else:
			customer["total_orders"] = 0
			customer["total_sales_amount"] = 0

		# Get last order date
		last_order = frappe.db.get_value(
			"Sales Order", filters={"customer": customer.name, "docstatus": 1}, fieldname="transaction_date", order_by="transaction_date desc"
		)
		customer["last_order_date"] = last_order

	return {"success": True, "customers": customers, "count": len(customers)}


@frappe.whitelist()
def recalculate_customer_balance(customer=None):
	"""Recalculate customer balances from ledger entries.

	Args:
		customer: Customer name (optional - if blank, recalculates all)

	Returns:
		dict: Success status, updated count, error count, and messages
	"""
	# Determine which customers to recalculate
	if customer:
		customers = [customer]
	else:
		# Recalculate ALL customers with ledger entries
		customers = frappe.db.sql(
			"""
			SELECT DISTINCT customer
			FROM `tabCustomer Balance Ledger`
			ORDER BY customer
		""",
			as_dict=0,
		)
		customers = [c[0] for c in customers]

	updated_count = 0
	error_count = 0
	error_customers = []

	# Process each customer
	for cust in customers:
		try:
			# Get all ledger entries for this customer (chronological order)
			ledger_entries = frappe.db.get_all(
				"Customer Balance Ledger",
				filters={"customer": cust},
				fields=["name", "debit_amount", "credit_amount", "transaction_date", "creation"],
				order_by="transaction_date asc, creation asc",
			)

			# Calculate running balance from scratch
			running_balance = 0.0

			for entry in ledger_entries:
				# Calculate new running balance
				running_balance = running_balance + entry.credit_amount - entry.debit_amount

				# Update ledger entry running_balance field
				frappe.db.set_value("Customer Balance Ledger", entry.name, "running_balance", running_balance, update_modified=False)

			# Update customer balance (final running balance)
			frappe.db.set_value("Customer", cust, "custom_current_balance", running_balance, update_modified=False)

			updated_count += 1

		except Exception as e:
			error_count += 1
			error_customers.append(cust)
			frappe.log_error(f"Recalculation failed for customer {cust}: {str(e)}", "Customer Balance Recalculation Error")

	# Prepare response
	if error_count > 0:
		return {
			"success": False,
			"updated_count": updated_count,
			"error_count": error_count,
			"error_customers": error_customers,
			"message": f"Recalculated {updated_count} customers, but {error_count} failed. Check Error Log.",
		}
	else:
		return {
			"success": True,
			"updated_count": updated_count,
			"error_count": 0,
			"message": f"Successfully recalculated balances for {updated_count} customers.",
		}
