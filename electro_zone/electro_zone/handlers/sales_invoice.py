"""
Sales Invoice event handlers for electro_zone app
"""

import frappe
import frappe.utils


def block_credit_note_if_dn_return_not_received(doc, method=None):
	"""Prevent manual Credit Note creation - Force DN Return workflow only.

	Only allows Credit Notes created via "Receive Return" API to ensure
	proper balance tracking and stock management.

	Event: Before Insert

	Args:
		doc: Sales Invoice document
		method: Event method name (unused, required by Frappe hook signature)

	Raises:
		frappe.ValidationError: If manual Credit Note creation attempted
	"""
	# Only validate for Credit Notes (Sales Invoice Returns)
	if doc.is_return == 1:
		# VALIDATION 1: Block ALL manual Credit Note creation
		# Only allow Credit Notes created through "Receive Return" API
		from_api = doc.get("custom_from_receive_return_api", 0)

		if from_api != 1:
			# This is a manual creation attempt - BLOCK IT
			frappe.throw(
				"<b>Manual Credit Note creation is not allowed.</b><br><br>"
				"You must use the DN Return workflow:<br>"
				"1. Open the original <b>Sales Invoice</b><br>"
				"2. Click <b>'Create Return'</b> to generate a Delivery Note Return (Draft)<br>"
				"3. Click <b>'Issue Return'</b> button (marks items as in transit)<br>"
				"4. Click <b>'Receive Return'</b> button (creates Credit Note automatically)<br><br>"
				"<b>Note:</b> Credit Notes can only be created through the authorized workflow to ensure "
				"proper balance tracking and stock management.",
				title="Manual Return Not Allowed",
			)

		# VALIDATION 2: Additional check - Verify DN Return status (if DN referenced)
		for item in doc.items:
			if item.get("delivery_note"):
				dn_name = item.delivery_note

				# Check if the DN is a return
				dn_is_return = frappe.db.get_value("Delivery Note", dn_name, "is_return")

				if dn_is_return == 1:
					# Get the DN Return status
					dn_return_status = frappe.db.get_value("Delivery Note", dn_name, "custom_return_status")

					# Block if status is "Return Issued" (items still in transit)
					if dn_return_status == "Return Issued":
						frappe.throw(
							f"Cannot create Credit Note for Delivery Note Return {dn_name}.<br><br>"
							f"<b>Current Status:</b> Return Issued (items in transit)<br>"
							f"<b>Required Status:</b> Return Received<br><br>"
							f"Please click the <b>'Receive Return'</b> button on the DN Return first to mark items as physically received."
						)

					# Block if status is "Draft" (not even issued yet)
					if dn_return_status == "Draft":
						frappe.throw(
							f"Cannot create Credit Note for Delivery Note Return {dn_name}.<br><br>"
							f"<b>Current Status:</b> Draft<br>"
							f"<b>Required Status:</b> Return Received<br><br>"
							f"Please first click <b>'Issue Return'</b> and then <b>'Receive Return'</b> on the DN Return."
						)


def auto_allocate_balance(doc, method=None):
	"""Create Payment Entry to allocate balance to invoice (ERPNext accounting integration).

	Only auto-allocates if invoice is from SO (balance was deducted at SO stage).

	Event: After Submit

	Args:
		doc: Sales Invoice document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	if not doc.is_return and doc.customer and doc.outstanding_amount > 0:
		customer = doc.customer

		# Check if this invoice is from a Sales Order
		sales_order = None
		for item in doc.items:
			if item.get("sales_order"):
				sales_order = item.sales_order
				break

		# Only auto-allocate if invoice is from SO (balance was deducted at SO stage)
		if sales_order:
			# Get SO advance_paid amount
			so_advance = frappe.db.get_value("Sales Order", sales_order, "advance_paid") or 0

			if so_advance > 0 and doc.outstanding_amount > 0:
				# Create PE to represent balance → invoice allocation
				allocate_amount = min(so_advance, doc.outstanding_amount)

				# Create Payment Entry
				pe = frappe.new_doc("Payment Entry")
				pe.payment_type = "Receive"
				pe.party_type = "Customer"
				pe.party = customer
				pe.posting_date = doc.posting_date
				pe.company = doc.company

				# Set accounts
				pe.paid_from = frappe.db.get_value("Company", doc.company, "default_receivable_account")
				pe.paid_to = frappe.db.get_value("Company", doc.company, "default_cash_account") or frappe.db.get_value(
					"Company", doc.company, "default_bank_account"
				)

				pe.paid_amount = allocate_amount
				pe.received_amount = allocate_amount

				# CRITICAL: Add invoice to references (creates real payment allocation)
				pe.append(
					"references",
					{
						"reference_doctype": "Sales Invoice",
						"reference_name": doc.name,
						"total_amount": doc.grand_total,
						"outstanding_amount": doc.outstanding_amount,
						"allocated_amount": allocate_amount,
					},
				)

				# Add flag to skip balance update (balance already updated at SO stage)
				pe.add_comment("Comment", "AUTO_CREATED_FROM_SO_BALANCE_DO_NOT_UPDATE_BALANCE")

				try:
					pe.insert(ignore_permissions=True)
					pe.submit()

					frappe.msgprint(
						f"Payment Entry <b>{pe.name}</b> auto-created from Sales Order advance.<br>"
						f"Amount: <b>{frappe.format_value(allocate_amount, {'fieldtype': 'Currency'})}</b><br>"
						"This creates proper accounting entries in ERPNext.",
						indicator="green",
						title="Payment Allocated",
					)

				except Exception as e:
					frappe.log_error(
						f"Failed to create Payment Entry for SI {doc.name}: {str(e)}", "Balance Allocation Error"
					)
					frappe.msgprint(
						f"Failed to auto-allocate balance: {str(e)}", indicator="orange", title="Auto-Allocation Failed"
					)


def update_so_billing_status_only(doc, method=None):
	"""Update SO billing status + Credit Note balance (REFERENCE-ONLY SYSTEM).

	Event: After Submit

	Args:
		doc: Sales Invoice document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	if not doc.customer:
		return

	customer = doc.customer
	grand_total = doc.grand_total or 0.0

	if not doc.is_return:
		# ==================================
		# REGULAR INVOICE - UPDATE SO STATUS ONLY
		# ==================================

		# Check if linked to Sales Order
		sales_order = None
		for item in doc.items:
			if item.get("sales_order"):
				sales_order = item.sales_order
				break

		if sales_order:
			# Update Sales Order billing status
			try:
				_update_so_billing_status(sales_order)
			except Exception as e:
				frappe.log_error(f"Failed to update SO billing status for {sales_order}: {str(e)}", "SO Billing Status Update Error")
				frappe.msgprint("Warning: SO billing status update failed. Invoice created successfully.", indicator="orange")

		# Create REFERENCE-ONLY ledger entry
		current_balance = frappe.db.get_value("Customer", customer, "custom_current_balance") or 0.0

		ledger = frappe.new_doc("Customer Balance Ledger")
		ledger.transaction_date = doc.posting_date
		ledger.posting_time = doc.posting_time or frappe.utils.nowtime()
		ledger.customer = customer
		ledger.customer_name = doc.customer_name
		ledger.reference_doctype = "Sales Invoice"
		ledger.reference_document = doc.name
		ledger.reference_date = doc.posting_date
		ledger.debit_amount = 0.0
		ledger.credit_amount = 0.0
		ledger.balance_before = current_balance
		ledger.running_balance = current_balance
		ledger.remarks = f"Sales Invoice {doc.name} - Reference only (Balance changed by SO: {sales_order if sales_order else 'Direct Invoice'})"
		ledger.company = doc.company
		ledger.created_by = frappe.session.user
		ledger.insert(ignore_permissions=True)

	else:
		# ==================================
		# CREDIT NOTE (Sales Return)
		# ==================================
		credit_amount = abs(grand_total)
		current_balance = frappe.db.get_value("Customer", customer, "custom_current_balance") or 0.0
		new_balance = current_balance + credit_amount

		# Update balance
		frappe.db.set_value("Customer", customer, "custom_current_balance", new_balance, update_modified=False)

		# Create ledger entry
		ledger = frappe.new_doc("Customer Balance Ledger")
		ledger.transaction_date = doc.posting_date
		ledger.posting_time = doc.posting_time or frappe.utils.nowtime()
		ledger.customer = customer
		ledger.customer_name = doc.customer_name
		ledger.reference_doctype = "Sales Invoice"
		ledger.reference_document = doc.name
		ledger.reference_date = doc.posting_date
		ledger.debit_amount = 0.0
		ledger.credit_amount = credit_amount
		ledger.balance_before = current_balance
		ledger.running_balance = new_balance
		ledger.remarks = f"Credit Note {doc.name} - Sales Return (Against: {doc.return_against or 'N/A'})"
		ledger.company = doc.company
		ledger.created_by = frappe.session.user
		ledger.insert(ignore_permissions=True)

		# Update SO billing status for credit note
		sales_order = None
		if doc.return_against:
			original_si_items = frappe.db.sql(
				"""
				SELECT sales_order
				FROM `tabSales Invoice Item`
				WHERE parent = %s
				AND sales_order IS NOT NULL
				LIMIT 1
			""",
				(doc.return_against,),
			)

			if original_si_items and original_si_items[0][0]:
				sales_order = original_si_items[0][0]

		if sales_order:
			try:
				_update_so_billing_status(sales_order, include_credit_notes=True)
			except Exception as e:
				frappe.log_error(
					f"Failed to update SO billing status for {sales_order} after Credit Note: {str(e)}",
					"SO Billing Status Update Error (Credit Note)",
				)


def auto_allocate_unallocated_payment_entries(doc, method=None):
	"""Auto-pull unallocated Payment Entries to reduce invoice outstanding.

	Gets unallocated_amount from PE document (calculated field).

	Event: Before Submit

	Args:
		doc: Sales Invoice document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	if not doc.is_return:
		# Only for regular invoices (not Credit Notes)
		customer = doc.customer

		if customer:
			# Check if ERPNext's auto-allocation already ran
			erpnext_auto_allocated = doc.get("allocate_advances_automatically", 0) == 1

			# Check if user manually added advances
			existing_advances = len(doc.advances) if doc.advances else 0

			if existing_advances == 0 and not erpnext_auto_allocated:
				# Get ALL submitted Payment Entries for this customer (FIFO)
				payment_entries = frappe.db.sql(
					"""
					SELECT
						pe.name,
						pe.posting_date,
						pe.paid_amount
					FROM `tabPayment Entry` pe
					WHERE pe.party_type = 'Customer'
					AND pe.party = %s
					AND pe.payment_type = 'Receive'
					AND pe.docstatus = 1
					ORDER BY pe.posting_date ASC, pe.creation ASC
				""",
					(customer,),
					as_dict=1,
				)

				if payment_entries:
					# Clear existing advances
					doc.advances = []

					total_allocated = 0
					allocated_count = 0

					# Allocate to invoice in FIFO order
					for entry in payment_entries:
						# Get Payment Entry document to access calculated field
						pe_doc = frappe.get_doc("Payment Entry", entry.name)

						# Get unallocated_amount from document (calculated field)
						available_amount = pe_doc.unallocated_amount or 0

						# Skip if this PE has no unallocated amount
						if available_amount <= 0:
							continue

						# Calculate how much to allocate from this PE
						remaining_to_allocate = doc.grand_total - total_allocated
						allocate_amount = min(available_amount, remaining_to_allocate)

						if allocate_amount > 0:
							# Add to advances table
							doc.append(
								"advances",
								{
									"reference_type": "Payment Entry",
									"reference_name": entry.name,
									"reference_row": "",
									"remarks": f"Auto-allocated payment (PE: {entry.name})",
									"advance_amount": available_amount,
									"allocated_amount": allocate_amount,
								},
							)

							total_allocated += allocate_amount
							allocated_count += 1

						# Stop if we've allocated enough to cover the invoice
						if total_allocated >= doc.grand_total:
							break

					if total_allocated > 0:
						remaining_outstanding = doc.grand_total - total_allocated

						if remaining_outstanding > 0:
							frappe.msgprint(
								f"Payment auto-allocated: <b>{frappe.format_value(total_allocated, {'fieldtype': 'Currency'})}</b> from {allocated_count} payment(s).<br>"
								f"Remaining outstanding: <b>{frappe.format_value(remaining_outstanding, {'fieldtype': 'Currency'})}</b>",
								indicator="blue",
								title="Payments Auto-Allocated",
							)
						else:
							frappe.msgprint(
								f"Invoice fully paid from auto-allocated payments.<br>"
								f"Amount: <b>{frappe.format_value(total_allocated, {'fieldtype': 'Currency'})}</b> from {allocated_count} payment(s).",
								indicator="green",
								title="Fully Paid",
							)


# ============================================================================
# HELPER FUNCTIONS
# ============================================================================


def _update_so_billing_status(so_name: str, include_credit_notes: bool = False):
	"""Recalculate and update Sales Order billing status.

	Args:
		so_name: Sales Order name
		include_credit_notes: Whether to include Credit Notes in calculation
	"""
	so_doc = frappe.get_doc("Sales Order", so_name)
	so_grand_total = so_doc.grand_total or 0

	if so_grand_total == 0:
		return

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

	# Calculate total Credit Notes
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

		# Determine indicator color
		if per_billed >= 100:
			msg_indicator = "green"
		elif per_billed > 0:
			msg_indicator = "orange"
		else:
			msg_indicator = "blue"

		frappe.msgprint(
			f"Sales Order status updated:<br>"
			f"• Delivered: <b>{per_delivered:.2f}%</b><br>"
			f"• Paid: <b>{per_billed:.2f}%</b><br>"
			f"• Payment Status: <b>{billing_status}</b><br>"
			f"• Overall Status: <b>{so_status}</b>",
			indicator=msg_indicator,
			title="SO Updated",
		)
