"""
Customer event handlers for electro_zone app
"""

import frappe


# ============================================================================
# API METHODS (Whitelisted for client-side access)
# ============================================================================


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


# ============================================================================
# EVENT HANDLERS
# ============================================================================


def validate_phone_uniqueness(doc, method=None):
	"""Validate that the phone number on primary address is unique across all customers.

	Ensures no two customers can have the same phone number.

	Event: Before Save

	Args:
		doc: Customer document
		method: Event method name (unused, required by Frappe hook signature)

	Raises:
		frappe.ValidationError: If duplicate phone found or validation fails
	"""
	# STEP 1: Validate customer has a primary address
	primary_address = doc.get("customer_primary_address")

	if not primary_address:
		frappe.throw(
			"Customer must have a Primary Address.<br><br>"
			"Please create an Address and link it as the Primary Address for this customer.",
			title="Primary Address Required",
		)

	# STEP 2: Get phone from the linked Address
	phone = frappe.db.get_value("Address", primary_address, "phone")

	if not phone:
		frappe.throw(
			f"The Primary Address ({primary_address}) does not have a phone number.<br><br>"
			"Please add a phone number to the Address before saving this customer.",
			title="Phone Number Required on Address",
		)

	# STEP 3: Remove spaces and formatting for consistent comparison
	phone_clean = phone.replace(" ", "").replace("-", "").replace("(", "").replace(")", "")

	# STEP 4: Validate phone format (basic check)
	if len(phone_clean) < 10:
		frappe.throw(
			f"Phone Number '{phone}' appears to be invalid.<br><br>" "Phone numbers must have at least 10 digits.",
			title="Invalid Phone Number",
		)

	# STEP 5: Check if another customer's primary address has the same phone
	# Find all addresses with this phone number
	addresses_with_phone = frappe.db.get_all("Address", filters={"phone": phone}, fields=["name"])

	if addresses_with_phone:
		# Check if any of these addresses are primary for OTHER customers
		for addr in addresses_with_phone:
			other_customer = frappe.db.get_value(
				"Customer",
				filters={"customer_primary_address": addr.name, "name": ["!=", doc.name]},  # Exclude current customer (for updates)
				fieldname=["name", "customer_name"],
				as_dict=1,
			)

			if other_customer:
				frappe.throw(
					f"Phone Number '{phone}' is already in use.<br><br>"
					f"<b>Existing Customer:</b> {other_customer.customer_name} ({other_customer.name})<br>"
					f"<b>Primary Address:</b> {addr.name}<br><br>"
					"Each customer must have a unique phone number on their primary address. "
					"Please use a different phone number or update the existing customer record.",
					title="Duplicate Phone Number",
				)

	# STEP 6: Success - validation passed
	frappe.msgprint(
		f"✓ Phone number '{phone}' validated successfully on Primary Address ({primary_address})", alert=True
	)
