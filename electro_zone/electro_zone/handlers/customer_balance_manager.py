"""
Customer Balance Manager for electro_zone app

Central utility module for all customer balance operations.
Provides atomic balance updates with database locking to prevent race conditions.
"""

import frappe
import frappe.utils


def get_available_balance(customer):
	"""Get customer's available balance (current - reserved).

	Args:
		customer: Customer name

	Returns:
		float: Available balance amount
	"""
	current_balance = frappe.db.get_value("Customer", customer, "custom_current_balance") or 0.0
	reserved_balance = frappe.db.get_value("Customer", customer, "custom_reserved_balance") or 0.0

	available_balance = current_balance - reserved_balance

	return available_balance


def reserve_balance_for_so(customer, so_name, amount):
	"""Reserve balance for Sales Order (atomic operation with locking).

	Args:
		customer: Customer name
		so_name: Sales Order name
		amount: Amount to reserve

	Returns:
		dict: Result with success status and new balances
	"""
	if amount <= 0:
		return {"success": False, "message": "Amount must be positive"}

	frappe.db.begin()

	try:
		# Lock customer record
		current_balance, reserved_balance = frappe.db.sql(
			"""
			SELECT custom_current_balance, custom_reserved_balance
			FROM `tabCustomer`
			WHERE name=%s
			FOR UPDATE
			""",
			(customer,),
		)[0]

		current_balance = current_balance or 0.0
		reserved_balance = reserved_balance or 0.0

		# Calculate available balance
		available = current_balance - reserved_balance

		if available <= 0:
			frappe.db.rollback()
			return {"success": False, "message": "No available balance to reserve", "available_balance": 0}

		# Reserve the amount (or partial if insufficient)
		reserved_amount = min(available, amount)
		new_reserved_balance = reserved_balance + reserved_amount

		# Update customer reserved balance
		frappe.db.set_value("Customer", customer, "custom_reserved_balance", new_reserved_balance, update_modified=False)

		# Create ledger entry
		create_ledger_entry(
			customer=customer,
			debit=0,
			credit=0,  # Reference only for reservation
			reference_doctype="Sales Order",
			reference_name=so_name,
			remarks=f"Balance reserved for SO {so_name}: {frappe.format_value(reserved_amount, {'fieldtype': 'Currency'})}",
		)

		frappe.db.commit()

		return {
			"success": True,
			"reserved_amount": reserved_amount,
			"new_reserved_balance": new_reserved_balance,
			"available_balance": current_balance - new_reserved_balance,
		}

	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(f"Failed to reserve balance for SO {so_name}: {str(e)}", "Balance Reservation Error")
		raise


def release_reserved_balance(customer, so_name):
	"""Release reserved balance when Sales Order is cancelled.

	Args:
		customer: Customer name
		so_name: Sales Order name

	Returns:
		dict: Result with success status and new balances
	"""
	frappe.db.begin()

	try:
		# Get SO reserved amount
		so_reserved = frappe.db.get_value("Sales Order", so_name, "custom_balance_reserved") or 0.0

		if so_reserved <= 0:
			frappe.db.commit()
			return {"success": True, "message": "No balance was reserved for this SO", "released_amount": 0}

		# Lock customer record
		reserved_balance = frappe.db.sql(
			"""
			SELECT custom_reserved_balance
			FROM `tabCustomer`
			WHERE name=%s
			FOR UPDATE
			""",
			(customer,),
		)[0][0] or 0.0

		# Release the reserved amount
		new_reserved_balance = max(0, reserved_balance - so_reserved)

		# Update customer reserved balance
		frappe.db.set_value("Customer", customer, "custom_reserved_balance", new_reserved_balance, update_modified=False)

		# Create ledger entry
		create_ledger_entry(
			customer=customer,
			debit=0,
			credit=0,  # Reference only
			reference_doctype="Sales Order",
			reference_name=so_name,
			remarks=f"Released reserved balance for cancelled SO {so_name}: {frappe.format_value(so_reserved, {'fieldtype': 'Currency'})}",
		)

		frappe.db.commit()

		return {"success": True, "released_amount": so_reserved, "new_reserved_balance": new_reserved_balance}

	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(f"Failed to release reserved balance for SO {so_name}: {str(e)}", "Balance Release Error")
		raise


def consume_balance_for_invoice(customer, si_name, amount):
	"""Consume balance for invoice payment (atomic operation with locking).

	This function:
	1. Decreases custom_current_balance by the payment amount
	2. Decreases custom_reserved_balance if invoice is from SO
	3. Creates a debit ledger entry

	Args:
		customer: Customer name
		si_name: Sales Invoice name
		amount: Amount to consume

	Returns:
		dict: Result with success status and new balances
	"""
	if amount <= 0:
		return {"success": False, "message": "Amount must be positive"}

	frappe.db.begin()

	try:
		# Lock customer record
		current_balance, reserved_balance = frappe.db.sql(
			"""
			SELECT custom_current_balance, custom_reserved_balance
			FROM `tabCustomer`
			WHERE name=%s
			FOR UPDATE
			""",
			(customer,),
		)[0]

		current_balance = current_balance or 0.0
		reserved_balance = reserved_balance or 0.0

		# Check if invoice is from SO
		si_doc = frappe.get_doc("Sales Invoice", si_name)
		sales_order = None
		so_reserved = 0

		for item in si_doc.items:
			if item.get("sales_order"):
				sales_order = item.sales_order
				break

		if sales_order:
			# Get SO's reserved amount
			so_reserved = frappe.db.get_value("Sales Order", sales_order, "custom_balance_reserved") or 0.0

		# Decrease current balance
		new_current_balance = current_balance - amount

		# Decrease reserved balance if from SO
		amount_to_release = min(so_reserved, amount)
		new_reserved_balance = reserved_balance - amount_to_release

		# Update customer balances
		frappe.db.set_value(
			"Customer",
			customer,
			{"custom_current_balance": new_current_balance, "custom_reserved_balance": new_reserved_balance},
			update_modified=False,
		)

		# Update SO consumed amount if applicable
		if sales_order and amount_to_release > 0:
			so_consumed = frappe.db.get_value("Sales Order", sales_order, "custom_balance_consumed") or 0.0
			new_so_consumed = so_consumed + amount_to_release
			frappe.db.set_value("Sales Order", sales_order, "custom_balance_consumed", new_so_consumed, update_modified=False)

		# Create debit ledger entry (actual balance decrease)
		create_ledger_entry(
			customer=customer,
			debit=amount,
			credit=0,
			reference_doctype="Sales Invoice",
			reference_name=si_name,
			remarks=f"Invoice payment from balance: {frappe.format_value(amount, {'fieldtype': 'Currency'})}",
		)

		frappe.db.commit()

		return {
			"success": True,
			"consumed_amount": amount,
			"new_current_balance": new_current_balance,
			"new_reserved_balance": new_reserved_balance,
			"from_so": sales_order,
			"released_reservation": amount_to_release,
		}

	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(f"Failed to consume balance for SI {si_name}: {str(e)}", "Balance Consumption Error")
		raise


def increase_balance_for_credit_note(customer, cn_name, amount):
	"""Increase customer balance for Credit Note (atomic operation with locking).

	Args:
		customer: Customer name
		cn_name: Credit Note (Sales Invoice) name
		amount: Amount to increase (positive value)

	Returns:
		dict: Result with success status and new balance
	"""
	if amount <= 0:
		return {"success": False, "message": "Amount must be positive"}

	frappe.db.begin()

	try:
		# Lock customer record
		current_balance = frappe.db.sql(
			"""
			SELECT custom_current_balance
			FROM `tabCustomer`
			WHERE name=%s
			FOR UPDATE
			""",
			(customer,),
		)[0][0] or 0.0

		# Increase balance
		new_balance = current_balance + amount

		# Update customer balance
		frappe.db.set_value("Customer", customer, "custom_current_balance", new_balance, update_modified=False)

		# Create credit ledger entry (actual balance increase)
		create_ledger_entry(
			customer=customer,
			debit=0,
			credit=amount,
			reference_doctype="Sales Invoice",
			reference_name=cn_name,
			remarks=f"Credit Note balance increase: {frappe.format_value(amount, {'fieldtype': 'Currency'})}",
		)

		frappe.db.commit()

		return {"success": True, "increased_amount": amount, "new_balance": new_balance, "previous_balance": current_balance}

	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(f"Failed to increase balance for Credit Note {cn_name}: {str(e)}", "Credit Note Balance Error")
		raise


def update_balance(customer, amount, description, reference_doctype, reference_name):
	"""Core function to update customer balance (atomic operation with locking).

	Positive amount = increase balance (credit)
	Negative amount = decrease balance (debit)

	Args:
		customer: Customer name
		amount: Amount to add (positive) or subtract (negative)
		description: Description for ledger entry
		reference_doctype: DocType of reference document
		reference_name: Name of reference document

	Returns:
		dict: Result with success status and new balance
	"""
	frappe.db.begin()

	try:
		# Lock customer record
		current_balance = frappe.db.sql(
			"""
			SELECT custom_current_balance
			FROM `tabCustomer`
			WHERE name=%s
			FOR UPDATE
			""",
			(customer,),
		)[0][0] or 0.0

		# Update balance
		new_balance = current_balance + amount

		# Update customer balance
		frappe.db.set_value("Customer", customer, "custom_current_balance", new_balance, update_modified=False)

		# Determine debit/credit for ledger
		debit = abs(amount) if amount < 0 else 0
		credit = amount if amount > 0 else 0

		# Create ledger entry
		create_ledger_entry(
			customer=customer,
			debit=debit,
			credit=credit,
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			remarks=description,
		)

		frappe.db.commit()

		return {"success": True, "amount": amount, "new_balance": new_balance, "previous_balance": current_balance}

	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(f"Failed to update balance for {customer}: {str(e)}", "Balance Update Error")
		raise


def reverse_balance_update(customer, reference_doctype, reference_name):
	"""Reverse balance update when document is cancelled.

	Finds the original ledger entry and creates an opposite entry.

	Args:
		customer: Customer name
		reference_doctype: DocType of cancelled document
		reference_name: Name of cancelled document

	Returns:
		dict: Result with success status and new balance
	"""
	frappe.db.begin()

	try:
		# Find original ledger entry
		ledger_entry = frappe.db.get_value(
			"Customer Balance Ledger",
			filters={"customer": customer, "reference_doctype": reference_doctype, "reference_document": reference_name},
			fieldname=["debit_amount", "credit_amount", "name"],
			as_dict=True,
		)

		if not ledger_entry:
			frappe.db.commit()
			return {"success": True, "message": "No ledger entry found to reverse"}

		original_debit = ledger_entry.debit_amount or 0
		original_credit = ledger_entry.credit_amount or 0

		# Calculate reversal amount (swap debit/credit)
		reversal_amount = original_credit - original_debit

		# Lock customer record and update balance
		current_balance = frappe.db.sql(
			"""
			SELECT custom_current_balance
			FROM `tabCustomer`
			WHERE name=%s
			FOR UPDATE
			""",
			(customer,),
		)[0][0] or 0.0

		new_balance = current_balance - reversal_amount

		# Update customer balance
		frappe.db.set_value("Customer", customer, "custom_current_balance", new_balance, update_modified=False)

		# Create reversal ledger entry (swap debit and credit)
		create_ledger_entry(
			customer=customer,
			debit=original_credit,  # Swap
			credit=original_debit,  # Swap
			reference_doctype=reference_doctype,
			reference_name=reference_name,
			remarks=f"Reversal of {reference_doctype} {reference_name} (cancelled)",
		)

		frappe.db.commit()

		return {
			"success": True,
			"reversed_amount": reversal_amount,
			"new_balance": new_balance,
			"previous_balance": current_balance,
		}

	except Exception as e:
		frappe.db.rollback()
		frappe.log_error(
			f"Failed to reverse balance update for {reference_doctype} {reference_name}: {str(e)}",
			"Balance Reversal Error",
		)
		raise


def create_ledger_entry(customer, debit, credit, reference_doctype, reference_name, remarks):
	"""Create Customer Balance Ledger entry.

	Args:
		customer: Customer name
		debit: Debit amount (balance decrease)
		credit: Credit amount (balance increase)
		reference_doctype: Reference document type
		reference_name: Reference document name
		remarks: Entry remarks
	"""
	# Get customer details
	customer_doc = frappe.get_doc("Customer", customer)
	customer_name = customer_doc.customer_name
	company = frappe.defaults.get_user_default("Company") or frappe.db.get_value("Company", filters={}, fieldname="name")

	# Get primary address and phone
	primary_address = customer_doc.get("customer_primary_address")
	phone = frappe.db.get_value("Address", primary_address, "phone") if primary_address else None

	# Get current balance and calculate running balance
	current_balance = frappe.db.get_value("Customer", customer, "custom_current_balance") or 0.0

	# Create ledger entry
	ledger = frappe.new_doc("Customer Balance Ledger")
	ledger.transaction_date = frappe.utils.today()
	ledger.posting_time = frappe.utils.nowtime()
	ledger.customer = customer
	ledger.customer_name = customer_name
	ledger.reference_doctype = reference_doctype
	ledger.reference_document = reference_name
	ledger.reference_date = frappe.utils.today()
	ledger.debit_amount = debit
	ledger.credit_amount = credit
	ledger.balance_before = current_balance - credit + debit  # Balance before this transaction
	ledger.running_balance = current_balance
	ledger.remarks = remarks
	ledger.company = company
	ledger.created_by = frappe.session.user

	if phone:
		ledger.phone = phone
	if primary_address:
		ledger.customer_primary_address = primary_address

	ledger.insert(ignore_permissions=True)
