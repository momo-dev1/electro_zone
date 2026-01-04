"""
Payment Entry event handlers for electro_zone app
"""

import frappe
import frappe.utils
from typing import Dict, List, Optional, Tuple


# Constants for magic strings
PAYMENT_TYPE_RECEIVE = "Receive"
PAYMENT_TYPE_PAY = "Pay"
PARTY_TYPE_CUSTOMER = "Customer"
AUTO_CREATED_COMMENT = "AUTO_CREATED_FROM_SO_BALANCE_DO_NOT_UPDATE_BALANCE"


def auto_allocate_outstanding_invoices_fifo(doc, method=None):
	"""Auto-allocate payment to outstanding invoices (oldest first) using FIFO logic.

	Supports:
	- Payment Type "Receive": Allocates to outstanding Sales Invoices
	- Payment Type "Pay": Allocates to outstanding Credit Notes

	Skips auto-allocation for:
	- Payments auto-created from Sales Order advance
	- Payments already linked to Sales Orders

	Args:
		doc: Payment Entry document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	# Only process Customer payments
	if doc.party_type != PARTY_TYPE_CUSTOMER:
		return

	customer = doc.party
	if not customer or not doc.paid_amount or doc.paid_amount <= 0:
		return

	# Handle Receive type (allocate to invoices)
	if doc.payment_type == PAYMENT_TYPE_RECEIVE:
		_auto_allocate_to_invoices(doc, customer)

	# Handle Pay type (allocate to credit notes)
	elif doc.payment_type == PAYMENT_TYPE_PAY:
		_auto_allocate_to_credit_notes(doc, customer)


def balance_topup_and_refund_handler(doc, method=None):
	"""Update customer balance when payment received or refund paid to customer.

	Handles:
	- Payment Type "Receive": Increases customer credit balance
	- Payment Type "Pay": Decreases balance (refund)
	- Creates Customer Balance Ledger entries
	- Validates primary address and phone
	- Prevents duplicate ledger entries
	- Updates SO per_billed for Credit Note refunds

	Args:
		doc: Payment Entry document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	# Only process Customer payments
	if doc.party_type != PARTY_TYPE_CUSTOMER:
		return

	customer = doc.party
	if not customer:
		frappe.log_error(f"No customer found on Payment Entry {doc.name}", "Customer Balance Error")
		return

	# Check if already processed (prevent duplicates)
	if _is_ledger_entry_exists(doc.name, customer):
		frappe.log_error(
			f"Ledger entry already exists for Payment Entry {doc.name} - skipping duplicate",
			"Customer Balance - Duplicate Prevention",
		)
		return

	# Check if auto-created from SO (skip balance update)
	if _is_auto_created_from_so(doc.name):
		frappe.msgprint("Auto-created PE from SO balance. Balance already updated.", indicator="blue")
		return

	# Get current balance
	current_balance = frappe.db.get_value("Customer", customer, "custom_current_balance") or 0.0
	payment_amount = doc.paid_amount

	# Process based on payment type
	if doc.payment_type == PAYMENT_TYPE_RECEIVE:
		_process_payment_receive(doc, customer, current_balance, payment_amount)

	elif doc.payment_type == PAYMENT_TYPE_PAY:
		_process_payment_refund(doc, customer, current_balance, payment_amount)


def update_so_on_payment(doc, method=None):
	"""Recalculate Sales Order per_billed when Payment Entry allocated to Sales Invoice.

	Updates:
	- per_billed percentage
	- billing_status
	- overall SO status

	Args:
		doc: Payment Entry document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	# Only process Customer Receive payments
	if doc.payment_type != PAYMENT_TYPE_RECEIVE or doc.party_type != PARTY_TYPE_CUSTOMER:
		return

	# Track updated Sales Orders (avoid duplicates)
	updated_sales_orders = []

	# Check all references in Payment Entry
	for ref in doc.references:
		if ref.reference_doctype != "Sales Invoice":
			continue

		si_name = ref.reference_name

		# Get Sales Order linked to this Sales Invoice
		so_name = _get_so_from_sales_invoice(si_name)

		if so_name and so_name not in updated_sales_orders:
			updated_sales_orders.append(so_name)

			try:
				# Recalculate and update SO billing status
				update_result = _update_so_billing_status(so_name)

				if update_result:
					_show_so_update_message(so_name, update_result, "Payment")

			except Exception as e:
				frappe.log_error(
					f"Failed to update SO billing status for {so_name}: {str(e)}", "PE SO Update Error"
				)


# ============================================================================
# HELPER FUNCTIONS - FIFO Allocation
# ============================================================================


def _auto_allocate_to_invoices(doc, customer: str) -> None:
	"""Auto-allocate Payment (Receive) to outstanding Sales Invoices using FIFO.

	Args:
		doc: Payment Entry document
		customer: Customer name
	"""
	# Skip if auto-created from SO or already has SO reference
	if _should_skip_auto_allocation(doc):
		return

	# Get outstanding invoices (FIFO order)
	outstanding_invoices = frappe.db.sql(
		"""
		SELECT name, posting_date, outstanding_amount, grand_total
		FROM `tabSales Invoice`
		WHERE customer = %s
		  AND docstatus = 1
		  AND outstanding_amount > 0
		ORDER BY posting_date ASC, creation ASC
	""",
		(customer,),
		as_dict=1,
	)

	if not outstanding_invoices:
		frappe.msgprint(
			"No outstanding invoices found for this customer.<br>"
			"Payment will be recorded as advance payment.",
			indicator="blue",
			title="No Outstanding Invoices",
		)
		return

	# Allocate to invoices
	doc.references = []
	remaining_amount = doc.paid_amount
	allocated_count = 0

	for invoice in outstanding_invoices:
		if remaining_amount <= 0:
			break

		outstanding = invoice.outstanding_amount
		allocated_amount = min(remaining_amount, outstanding)

		doc.append(
			"references",
			{
				"reference_doctype": "Sales Invoice",
				"reference_name": invoice.name,
				"total_amount": invoice.grand_total,
				"outstanding_amount": outstanding,
				"allocated_amount": allocated_amount,
			},
		)

		remaining_amount -= allocated_amount
		allocated_count += 1

	# Show allocation summary
	if allocated_count > 0:
		_show_allocation_message(doc.paid_amount, remaining_amount, allocated_count, "invoice")


def _auto_allocate_to_credit_notes(doc, customer: str) -> None:
	"""Auto-allocate Payment (Pay/Refund) to outstanding Credit Notes using FIFO.

	Args:
		doc: Payment Entry document
		customer: Customer name
	"""
	# Get outstanding Credit Notes (FIFO order)
	outstanding_credit_notes = frappe.db.sql(
		"""
		SELECT name, posting_date, outstanding_amount, grand_total
		FROM `tabSales Invoice`
		WHERE customer = %s
		  AND docstatus = 1
		  AND is_return = 1
		  AND outstanding_amount < 0
		ORDER BY posting_date ASC, creation ASC
	""",
		(customer,),
		as_dict=1,
	)

	if not outstanding_credit_notes:
		frappe.msgprint(
			"No outstanding Credit Notes found for this customer.<br>"
			"Payment will be processed as direct refund (customer balance will be updated).",
			indicator="orange",
			title="No Credit Notes to Close",
		)
		return

	# Allocate to Credit Notes
	doc.references = []
	remaining_amount = doc.paid_amount
	allocated_count = 0

	for credit_note in outstanding_credit_notes:
		if remaining_amount <= 0:
			break

		outstanding_abs = abs(credit_note.outstanding_amount)
		allocated_amount = min(remaining_amount, outstanding_abs)

		# Allocated amount must be negative to match Credit Note's negative outstanding
		doc.append(
			"references",
			{
				"reference_doctype": "Sales Invoice",
				"reference_name": credit_note.name,
				"total_amount": credit_note.grand_total,
				"outstanding_amount": credit_note.outstanding_amount,
				"allocated_amount": -allocated_amount,
			},
		)

		remaining_amount -= allocated_amount
		allocated_count += 1

	# Show allocation summary
	if allocated_count > 0:
		_show_allocation_message(doc.paid_amount, remaining_amount, allocated_count, "Credit Note", is_refund=True)


def _should_skip_auto_allocation(doc) -> bool:
	"""Check if auto-allocation should be skipped.

	Returns:
		bool: True if should skip, False otherwise
	"""
	# Check if auto-created from SO
	is_auto_created = _is_auto_created_from_so(doc.name)

	# Check if already has SO reference
	has_so_reference = any(ref.reference_doctype == "Sales Order" for ref in doc.references or [])

	if is_auto_created:
		frappe.msgprint(
			"Auto-allocation skipped: Payment Entry created from Sales Order advance.<br>"
			"This payment is linked to a specific Sales Order.",
			indicator="blue",
			title="SO Advance Payment",
		)
		return True

	if has_so_reference:
		frappe.msgprint(
			"Auto-allocation skipped: Payment Entry already linked to Sales Order.",
			indicator="blue",
			title="SO Payment",
		)
		return True

	return False


def _show_allocation_message(
	total_amount: float, remaining_amount: float, allocated_count: int, doc_type: str, is_refund: bool = False
) -> None:
	"""Show allocation summary message to user.

	Args:
		total_amount: Total payment amount
		remaining_amount: Unallocated amount
		allocated_count: Number of documents allocated to
		doc_type: Document type (invoice/Credit Note)
		is_refund: Whether this is a refund payment
	"""
	allocated_amount = total_amount - remaining_amount
	action = "Refund" if is_refund else "Payment"

	if remaining_amount > 0:
		msg = (
			f"{action} auto-allocated to <b>{allocated_count} {doc_type}(s)</b>.<br>"
			f"Allocated: <b>{frappe.format_value(allocated_amount, {'fieldtype': 'Currency'})}</b><br>"
		)
		if is_refund:
			msg += f"Remaining: <b>{frappe.format_value(remaining_amount, {'fieldtype': 'Currency'})}</b> (will increase customer balance)"
		else:
			msg += f"Unallocated (Advance): <b>{frappe.format_value(remaining_amount, {'fieldtype': 'Currency'})}</b>"

		frappe.msgprint(msg, indicator="blue", title="Auto-Allocation Complete")
	else:
		frappe.msgprint(
			f"{action} auto-allocated to <b>{allocated_count} {doc_type}(s)</b>.<br>All amount allocated.",
			indicator="green",
			title="Auto-Allocation Complete",
		)


# ============================================================================
# HELPER FUNCTIONS - Balance Management
# ============================================================================


def _is_ledger_entry_exists(pe_name: str, customer: str) -> bool:
	"""Check if ledger entry already exists for this Payment Entry.

	Args:
		pe_name: Payment Entry name
		customer: Customer name

	Returns:
		bool: True if exists, False otherwise
	"""
	return frappe.db.exists(
		"Customer Balance Ledger",
		{"reference_doctype": "Payment Entry", "reference_document": pe_name, "customer": customer},
	)


def _is_auto_created_from_so(pe_name: str) -> bool:
	"""Check if Payment Entry was auto-created from Sales Order.

	Args:
		pe_name: Payment Entry name

	Returns:
		bool: True if auto-created, False otherwise
	"""
	comments = frappe.get_all(
		"Comment",
		filters={"reference_doctype": "Payment Entry", "reference_name": pe_name, "content": AUTO_CREATED_COMMENT},
		limit=1,
	)
	return len(comments) > 0


def _process_payment_receive(doc, customer: str, current_balance: float, payment_amount: float) -> None:
	"""Create reference-only ledger entry for Payment Entry (Receive).

	Balance is now tracked in GL Entry. This creates an audit trail entry only.

	Args:
		doc: Payment Entry document
		customer: Customer name
		current_balance: Current customer balance (from GL)
		payment_amount: Payment amount
	"""
	# Create REFERENCE-ONLY ledger entry
	_create_balance_ledger_entry(
		doc=doc,
		customer=customer,
		debit_amount=0.0,  # Reference only
		credit_amount=0.0,  # Reference only
		current_balance=current_balance,
		new_balance=current_balance,  # Unchanged
		remarks=f"Reference only - GL tracked. Payment {doc.name} - {doc.mode_of_payment or 'Cash'} (Amount: {frappe.format_value(payment_amount, {'fieldtype': 'Currency'})})",
	)

	frappe.msgprint(
		f"Payment recorded. Balance tracked in GL.<br>"
		f"Current balance: <b>{frappe.format_value(current_balance, {'fieldtype': 'Currency'})}</b>",
		indicator="blue",
		title="Payment Recorded",
	)


def _process_payment_refund(doc, customer: str, current_balance: float, payment_amount: float) -> None:
	"""Create reference-only ledger entry for Payment Entry (Refund/Pay).

	Balance is now tracked in GL Entry. This creates an audit trail entry only.

	Args:
		doc: Payment Entry document
		customer: Customer name
		current_balance: Current customer balance (from GL)
		payment_amount: Refund amount
	"""
	# Create REFERENCE-ONLY ledger entry
	_create_balance_ledger_entry(
		doc=doc,
		customer=customer,
		debit_amount=0.0,  # Reference only
		credit_amount=0.0,  # Reference only
		current_balance=current_balance,
		new_balance=current_balance,  # Unchanged
		remarks=f"Reference only - GL tracked. Refund {doc.name} - {doc.mode_of_payment or 'Cash'} (Amount: {frappe.format_value(payment_amount, {'fieldtype': 'Currency'})})",
	)

	frappe.msgprint(
		f"Refund recorded. Balance tracked in GL.<br>"
		f"Current balance: <b>{frappe.format_value(current_balance, {'fieldtype': 'Currency'})}</b>",
		indicator="blue",
		title="Refund Recorded",
	)

	# Update SO per_billed for Credit Note refunds
	_update_so_for_credit_note_refunds(doc)


def _create_balance_ledger_entry(
	doc,
	customer: str,
	debit_amount: float,
	credit_amount: float,
	current_balance: float,
	new_balance: float,
	remarks: str,
) -> None:
	"""Create Customer Balance Ledger entry.

	Args:
		doc: Payment Entry document
		customer: Customer name
		debit_amount: Debit amount
		credit_amount: Credit amount
		current_balance: Balance before transaction
		new_balance: Balance after transaction
		remarks: Ledger entry remarks
	"""
	# Get phone and address
	primary_address = frappe.db.get_value("Customer", customer, "customer_primary_address")
	phone = frappe.db.get_value("Address", primary_address, "phone") if primary_address else None

	ledger = frappe.new_doc("Customer Balance Ledger")
	ledger.transaction_date = doc.posting_date
	ledger.posting_time = frappe.utils.nowtime()
	ledger.customer = customer
	ledger.customer_name = doc.party_name
	ledger.reference_doctype = "Payment Entry"
	ledger.reference_document = doc.name
	ledger.reference_date = doc.posting_date
	ledger.debit_amount = debit_amount
	ledger.credit_amount = credit_amount
	ledger.balance_before = current_balance
	ledger.running_balance = new_balance
	ledger.remarks = remarks
	ledger.company = doc.company
	ledger.created_by = frappe.session.user

	if phone:
		ledger.phone = phone
	if primary_address:
		ledger.customer_primary_address = primary_address

	ledger.insert(ignore_permissions=True)


def _update_so_for_credit_note_refunds(doc) -> None:
	"""Update SO per_billed when Payment Entry (Pay) is allocated to Credit Notes.

	Args:
		doc: Payment Entry document
	"""
	updated_sales_orders = []

	for ref in doc.references:
		if ref.reference_doctype != "Sales Invoice":
			continue

		si_name = ref.reference_name

		# Check if this is a Credit Note
		is_credit_note = frappe.db.get_value("Sales Invoice", si_name, "is_return")

		if not is_credit_note:
			continue

		# Get original invoice
		original_invoice = frappe.db.get_value("Sales Invoice", si_name, "return_against")

		if not original_invoice:
			continue

		# Get SO from original invoice
		so_name = _get_so_from_sales_invoice(original_invoice)

		if so_name and so_name not in updated_sales_orders:
			updated_sales_orders.append(so_name)

			try:
				update_result = _update_so_billing_status(so_name, include_credit_notes=True)

				if update_result:
					_show_so_update_message(so_name, update_result, "Refund")

			except Exception as e:
				frappe.log_error(
					f"Failed to update SO billing status for {so_name} after refund: {str(e)}",
					"PE Refund SO Update Error",
				)


# ============================================================================
# HELPER FUNCTIONS - Sales Order Billing Updates
# ============================================================================


def _get_so_from_sales_invoice(si_name: str) -> Optional[str]:
	"""Get Sales Order name from Sales Invoice.

	Args:
		si_name: Sales Invoice name

	Returns:
		str: Sales Order name or None
	"""
	result = frappe.db.sql(
		"""
		SELECT DISTINCT si_item.sales_order
		FROM `tabSales Invoice Item` si_item
		WHERE si_item.parent = %s
		  AND si_item.sales_order IS NOT NULL
		LIMIT 1
	""",
		(si_name,),
	)

	return result[0][0] if result and result[0][0] else None


def _update_so_billing_status(so_name: str, include_credit_notes: bool = False) -> Optional[Dict]:
	"""Recalculate and update Sales Order billing status.

	Args:
		so_name: Sales Order name
		include_credit_notes: Whether to include Credit Notes in calculation

	Returns:
		dict: Update result with percentages and statuses, or None if error
	"""
	so_doc = frappe.get_doc("Sales Order", so_name)
	so_grand_total = so_doc.grand_total or 0

	if so_grand_total == 0:
		return None

	# Calculate total invoiced
	total_invoiced = frappe.db.sql(
		"""
		SELECT IFNULL(SUM(si.grand_total), 0) as total
		FROM `tabSales Invoice` si
		INNER JOIN `tabSales Invoice Item` si_item ON si_item.parent = si.name
		WHERE si_item.sales_order = %s
		  AND si.docstatus = 1
		  AND si.is_return = 0
	""",
		(so_name,),
	)[0][0] or 0

	# Calculate total outstanding
	total_outstanding = frappe.db.sql(
		"""
		SELECT IFNULL(SUM(si.outstanding_amount), 0) as total
		FROM `tabSales Invoice` si
		INNER JOIN `tabSales Invoice Item` si_item ON si_item.parent = si.name
		WHERE si_item.sales_order = %s
		  AND si.docstatus = 1
		  AND si.is_return = 0
	""",
		(so_name,),
	)[0][0] or 0

	# Calculate total Credit Notes (if requested)
	total_credit_notes = 0
	if include_credit_notes:
		total_credit_notes = frappe.db.sql(
			"""
			SELECT IFNULL(SUM(ABS(si.grand_total)), 0) as total
			FROM `tabSales Invoice` si
			WHERE si.return_against IN (
				SELECT parent FROM `tabSales Invoice Item`
				WHERE sales_order = %s
			)
			  AND si.docstatus = 1
			  AND si.is_return = 1
			  AND si.outstanding_amount = 0
		""",
			(so_name,),
		)[0][0] or 0

	# Calculate total paid
	total_paid = total_invoiced - total_outstanding - total_credit_notes

	# Calculate percentage
	per_billed = (total_paid / so_grand_total * 100) if so_grand_total > 0 else 0

	# Determine billing status
	if per_billed == 0:
		billing_status = "Not Paid"
	elif per_billed < 100:
		billing_status = "Partly Paid"
	else:
		billing_status = "Fully Paid"

	# Check if SO is returned
	is_returned = so_doc.get("custom_is_returned", 0)

	if is_returned == 1:
		# SO is closed due to return - only update per_billed and billing_status
		frappe.db.set_value(
			"Sales Order", so_name, {"per_billed": per_billed, "billing_status": billing_status}, update_modified=False
		)
		return {
			"per_billed": per_billed,
			"billing_status": billing_status,
			"status": "Closed",
			"per_delivered": so_doc.per_delivered or 0,
			"is_returned": True,
		}
	else:
		# Normal SO - update status based on delivery and billing
		per_delivered = so_doc.per_delivered or 0

		# Determine overall SO status
		if per_billed >= 100 and per_delivered >= 100:
			so_status = "Completed"
		elif per_delivered >= 100 and per_billed < 100:
			so_status = "To Bill"
		elif per_billed >= 100 and per_delivered < 100:
			so_status = "To Deliver"
		else:
			so_status = "To Deliver and Bill"

		# Update Sales Order
		frappe.db.set_value(
			"Sales Order",
			so_name,
			{"per_billed": per_billed, "billing_status": billing_status, "status": so_status},
			update_modified=False,
		)

		return {
			"per_billed": per_billed,
			"billing_status": billing_status,
			"status": so_status,
			"per_delivered": per_delivered,
			"is_returned": False,
		}


def _show_so_update_message(so_name: str, update_result: Dict, action: str) -> None:
	"""Show Sales Order update message to user.

	Args:
		so_name: Sales Order name
		update_result: Dictionary with update results
		action: Action type (Payment/Refund)
	"""
	per_billed = update_result["per_billed"]
	billing_status = update_result["billing_status"]
	so_status = update_result["status"]
	per_delivered = update_result["per_delivered"]
	is_returned = update_result.get("is_returned", False)

	# Determine indicator color
	if per_billed >= 100:
		msg_indicator = "green"
	elif per_billed > 0:
		msg_indicator = "orange"
	else:
		msg_indicator = "blue"

	if is_returned:
		frappe.msgprint(
			f"{action} processed for returned SO <b>{so_name}</b>:<br>"
			f"• Status remains: <b>Closed</b> (return workflow)<br>"
			f"• Paid (net after refund): <b>{per_billed:.2f}%</b><br>"
			f"• Payment Status: <b>{billing_status}</b>",
			indicator="blue",
			title=f"{action} Processed",
		)
	else:
		frappe.msgprint(
			f"{action} recorded. Sales Order <b>{so_name}</b> status updated:<br>"
			f"• Delivered: <b>{per_delivered:.2f}%</b><br>"
			f"• Paid: <b>{per_billed:.2f}%</b><br>"
			f"• Payment Status: <b>{billing_status}</b><br>"
			f"• Overall Status: <b>{so_status}</b>",
			indicator=msg_indicator,
			title=f"SO {action} Update",
		)
