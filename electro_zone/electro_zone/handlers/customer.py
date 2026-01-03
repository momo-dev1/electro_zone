"""
Customer event handlers for electro_zone app
"""

import frappe
from erpnext.selling.doctype.customer.customer import get_customer_outstanding


@frappe.whitelist()
def sync_balance_from_gl(customer=None, company=None):
	"""Sync custom_current_balance from GL Entry using get_customer_outstanding().

	Syncs customer balance from ERPNext General Ledger instead of manual tracking.
	Uses get_customer_outstanding() which includes:
	- GL balance (debit - credit)
	- Unbilled Sales Orders
	- Unbilled Delivery Notes

	Sign Convention:
	- ERPNext GL: Positive outstanding = customer owes us
	- Custom Balance: Negative = customer owes us, Positive = we owe customer
	- Conversion: custom_current_balance = -1 * gl_outstanding

	Args:
		customer: Customer name (optional - if blank, syncs all customers)
		company: Company name (optional - uses customer's default if not provided)

	Returns:
		dict: Success status, updated count, error count, and messages
	"""
	# Determine which customers to sync
	if customer:
		customers = [{"name": customer}]
	else:
		# Sync ALL customers
		customers = frappe.db.get_all("Customer", fields=["name"], filters={"disabled": 0})

	if not customers:
		return {"success": True, "updated_count": 0, "message": "No customers to sync."}

	updated_count = 0
	error_count = 0
	error_customers = []

	# Process each customer
	for cust in customers:
		cust_name = cust["name"] if isinstance(cust, dict) else cust

		try:
			# Get company for this customer
			if not company:
				# Use default company from system or first company
				customer_company = frappe.defaults.get_user_default("Company")
				if not customer_company:
					# Use first company in the system
					customer_company = frappe.db.get_value("Company", filters={}, fieldname="name")
			else:
				customer_company = company

			if not customer_company:
				frappe.log_error(f"No company found for customer {cust_name}", "GL Balance Sync Error")
				error_count += 1
				error_customers.append(cust_name)
				continue

			# Get GL outstanding from ERPNext
			gl_outstanding = get_customer_outstanding(
				customer=cust_name,
				company=customer_company,
				ignore_outstanding_sales_order=False,  # Include unbilled SO
			)

			# Convert sign: ERPNext GL (positive = customer owes) → Custom (negative = customer owes)
			custom_current_balance = -1 * gl_outstanding

			# Update customer balance field
			frappe.db.set_value(
				"Customer",
				cust_name,
				"custom_current_balance",
				custom_current_balance,
				update_modified=False,
			)

			updated_count += 1

		except Exception as e:
			error_count += 1
			error_customers.append(cust_name)
			frappe.log_error(
				f"GL balance sync failed for customer {cust_name}: {str(e)}", "GL Balance Sync Error"
			)

	# Get final balance for single customer sync (for display)
	final_balance = None
	gl_balance = None
	if customer and updated_count == 1:
		final_balance = frappe.db.get_value("Customer", customer, "custom_current_balance")
		# Get GL balance for display
		try:
			company_for_display = company or frappe.defaults.get_user_default("Company")
			if not company_for_display:
				company_for_display = frappe.db.get_value("Company", filters={}, fieldname="name")
			gl_balance = get_customer_outstanding(customer=customer, company=company_for_display, ignore_outstanding_sales_order=False)
		except:
			pass

	# Prepare response
	if error_count > 0:
		return {
			"success": False,
			"updated_count": updated_count,
			"error_count": error_count,
			"error_customers": error_customers,
			"message": f"Synced {updated_count} customers, but {error_count} failed. Check Error Log.",
			"gl_outstanding": gl_balance,
			"custom_current_balance": final_balance,
		}
	else:
		return {
			"success": True,
			"updated_count": updated_count,
			"error_count": 0,
			"message": f"Successfully synced balances from GL for {updated_count} customers.",
			"gl_outstanding": gl_balance,
			"custom_current_balance": final_balance,
		}


@frappe.whitelist()
def recalculate_customer_balance(customer=None):
	"""Recalculate customer balances from GL Entry (replaces ledger-based calculation).

	This function now syncs balances from ERPNext General Ledger instead of
	recalculating from Customer Balance Ledger entries.

	Args:
		customer: Customer name (optional - if blank, recalculates all)

	Returns:
		dict: Success status, updated count, error count, and messages
	"""
	# Delegate to GL sync function
	return sync_balance_from_gl(customer=customer)


# ============================================================================
# EVENT HANDLERS
# ============================================================================


def validate_phone_uniqueness(doc, _method=None):
	"""Validate that the phone number on primary address is unique across all customers.

	Ensures no two customers can have the same phone number.

	Event: Before Save

	Args:
		doc: Customer document
		_method: Event method name (unused, required by Frappe hook signature)

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
