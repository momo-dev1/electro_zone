"""
Delivery Note event handlers for electro_zone app

This module contains event handlers migrated from Server Scripts for better
maintainability, testing, and version control.
"""

import frappe
import frappe.utils


# ============================================================================
# API METHODS (Whitelisted for client-side access)
# ============================================================================


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


# ============================================================================
# EVENT HANDLERS & HELPER FUNCTIONS
# ============================================================================


# Warehouse field mappings (used across multiple handlers)
WAREHOUSE_FIELDS = {
	"custom_stock_store_display": "Store Display - EZ",
	"custom_stock_store_warehouse": "Store Warehouse - EZ",
	"custom_stock_damage": "Damage - EZ",
	"custom_stock_damage_for_sale": "Damage For Sale - EZ",
	"custom_stock_zahran_main": "Zahran Main - EZ",
	"custom_stock_hold": "Hold (Reserved / Pending Shipment) - EZ",
}


def update_item_stock_fields(doc, method=None):
	"""Update Item warehouse stock fields after Delivery Note submission.

	Updates custom stock display fields on Item master for multiple warehouses
	using robust stock fetching from Bin table.

	Event: After Submit

	Args:
		doc: Delivery Note document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	# Get all unique item codes from this delivery note
	item_codes = set()
	for item in doc.items:
		if item.item_code:
			item_codes.add(item.item_code)

	# Update stock fields for each item
	for item_code in item_codes:
		if frappe.db.exists("Item", item_code):
			for field_name, warehouse_name in WAREHOUSE_FIELDS.items():
				bin_data = frappe.db.sql(
					"""
					SELECT actual_qty
					FROM `tabBin`
					WHERE item_code = %s AND warehouse = %s
				""",
					(item_code, warehouse_name),
					as_dict=1,
				)

				actual_qty = bin_data[0].actual_qty if bin_data else 0
				frappe.db.set_value("Item", item_code, field_name, actual_qty, update_modified=False)


def validate_sales_order_reference(doc, method=None):
	"""Validate that Delivery Note has Sales Order reference.

	Ensures DN cannot be submitted without Sales Order reference.

	Event: Before Submit

	Args:
		doc: Delivery Note document
		method: Event method name (unused, required by Frappe hook signature)

	Raises:
		frappe.ValidationError: If no SO reference found
	"""
	# Check if any item has Sales Order reference
	has_so_reference = False
	for item in doc.items:
		if item.against_sales_order:
			has_so_reference = True
			break

	# Block submission if no SO reference found
	if not has_so_reference:
		frappe.throw(
			"Cannot submit Delivery Note without Sales Order reference. "
			"All items must be linked to a Sales Order.<br><br>"
			"Please create Delivery Note from a Sales Order instead.",
			title="Sales Order Required",
		)


def block_cancel_if_delivered(doc, method=None):
	"""Prevent cancellation of DNs that are already delivered or out for delivery.

	This blocks ERPNext's automatic cascade cancel from Sales Order.

	Event: Before Cancel

	Args:
		doc: Delivery Note document
		method: Event method name (unused, required by Frappe hook signature)

	Raises:
		frappe.ValidationError: If DN is in forbidden state
	"""
	# Get workflow state (safely handle if field doesn't exist)
	workflow_state = doc.get("workflow_state", "")

	# Define states that CANNOT be cancelled
	forbidden_states = ["Out for Delivery", "Delivered"]

	# Check if DN is in a forbidden state
	if workflow_state in forbidden_states:
		# Calculate docstatus display
		docstatus_display = "Submitted (1)" if doc.docstatus == 1 else "Draft (0)"

		frappe.throw(
			f"❌ <b>Cannot Cancel Delivery Note</b><br><br>"
			f"<b>Delivery Note:</b> {doc.name}<br>"
			f"<b>Current State:</b> {workflow_state}<br>"
			f"<b>Doc Status:</b> {docstatus_display}<br><br>"
			"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━<br><br>"
			"<b>⚠️ Why This Is Blocked:</b><br>"
			f"This delivery note is already <b>{workflow_state}</b>. Items have been dispatched or delivered to the customer. "
			"Cancelling this document would create incorrect audit trails and accounting records.<br><br>"
			"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━<br><br>"
			"<b>✅ What To Do Instead:</b><br><br>"
			"If items need to be returned:<br>"
			f"1. Open this Delivery Note: <b>{doc.name}</b><br>"
			"2. Click <b>Actions</b> → <b>Return Items</b><br>"
			"3. This will properly handle:<br>"
			"   • Stock return to warehouse<br>"
			"   • Customer refund/balance update<br>"
			"   • Proper audit trail<br><br>"
			"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━<br><br>"
			"<b>If You're Trying to Cancel the Sales Order:</b><br>"
			f"You cannot cancel a Sales Order when its Delivery Note is in <b>{workflow_state}</b> state. "
			"The DN must be in <b>Pending Dispatch</b> state to allow SO cancellation.<br><br>"
			"━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━<br><br>"
			"<b>Cancellation Is ONLY Allowed When:</b><br>"
			"• workflow_state = <b>Pending Dispatch</b> (items not yet sent out)<br><br>"
			"Contact your system administrator if you need assistance.",
			title="🚫 Cancellation Blocked - Already Delivered",
		)

	# Add info message for successful cancellation of Pending Dispatch DNs
	if workflow_state == "Pending Dispatch":
		frappe.msgprint(
			"Cancelling Delivery Note in <b>Pending Dispatch</b> state.<br>"
			"Stock will be returned from Hold warehouse to source warehouse.",
			indicator="blue",
			title="DN Cancellation",
		)


def create_reference_ledger_entry(doc, method=None):
	"""Create Customer Balance Ledger entry for DN (reference only - no balance change).

	Tracks DN in ledger for audit trail without changing customer balance.

	Event: After Submit

	Args:
		doc: Delivery Note document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	# Only for regular deliveries (not returns)
	if not doc.is_return:
		customer = doc.customer

		# Get current balance (unchanged)
		current_balance = frappe.db.get_value("Customer", customer, "custom_current_balance") or 0.0

		# Find linked Sales Order
		sales_order = None
		for item in doc.items:
			if item.get("against_sales_order"):
				sales_order = item.against_sales_order
				break

		# Create REFERENCE-ONLY ledger entry
		ledger = frappe.new_doc("Customer Balance Ledger")
		ledger.transaction_date = doc.posting_date
		ledger.posting_time = doc.posting_time or frappe.utils.nowtime()
		ledger.customer = customer
		ledger.customer_name = doc.customer_name
		ledger.reference_doctype = "Delivery Note"
		ledger.reference_document = doc.name
		ledger.reference_date = doc.posting_date
		ledger.debit_amount = 0.0  # NO change - reference only
		ledger.credit_amount = 0.0  # NO change - reference only
		ledger.balance_before = current_balance
		ledger.running_balance = current_balance  # UNCHANGED
		ledger.remarks = f"Delivery Note {doc.name} - Goods delivered (SO: {sales_order or 'N/A'})"
		ledger.company = doc.company
		ledger.created_by = frappe.session.user
		ledger.insert(ignore_permissions=True)


def auto_close_so_on_cancel(doc, method=None):
	"""Auto-close linked Sales Order when DN is cancelled.

	Event: After Cancel

	Args:
		doc: Delivery Note document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	# Get all Sales Order references from DN items
	sales_orders = set()
	for item in doc.items:
		if item.against_sales_order:
			sales_orders.add(item.against_sales_order)

	# Close each linked Sales Order
	for so_name in sales_orders:
		# Check if SO exists and is not already closed or cancelled
		so = frappe.get_doc("Sales Order", so_name)

		# Only close if SO is still open (not closed or cancelled)
		if so.docstatus == 1 and so.status != "Closed":
			# Close the Sales Order
			so.update_status("Closed")

			# Add comment to SO
			so.add_comment(
				"Comment",
				f"Sales Order automatically closed because Delivery Note {doc.name} was canceled by {frappe.session.user}",
			)

			frappe.msgprint(
				f"Sales Order {so_name} has been automatically closed.", indicator="orange", title="SO Auto-Closed"
			)

	# Add comment to DN
	if sales_orders:
		doc.add_comment("Comment", f"Linked Sales Order(s) automatically closed: {', '.join(sales_orders)}")


def auto_invoice_on_out_for_delivery(doc, method=None):
	"""Auto-create Sales Invoice when DN is submitted with "Out for Delivery" state.

	Includes retry logic to handle Payment Entry concurrency conflicts.

	Event: After Submit

	Args:
		doc: Delivery Note document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	# Only process when DN moves to "Delivered" state (non-return DNs)
	if doc.workflow_state == "Delivered" and doc.is_return != 1:
		# Check if invoice already exists
		existing = frappe.db.exists("Sales Invoice Item", {"delivery_note": doc.name, "docstatus": ["!=", 2]})

		if existing:
			frappe.msgprint(f"Sales Invoice already exists for DN {doc.name}", indicator="orange")
		else:
			# Retry logic to handle concurrency conflicts
			max_retries = 3
			retry_count = 0
			success = False
			last_error = None

			while retry_count < max_retries and not success:
				try:
					# Create Sales Invoice (as Draft - SI script will handle submission)
					si = frappe.new_doc("Sales Invoice")
					si.customer = doc.customer
					si.posting_date = frappe.utils.nowdate()
					si.company = doc.company

					# Copy items
					for dn_item in doc.items:
						si.append(
							"items",
							{
								"item_code": dn_item.item_code,
								"item_name": dn_item.item_name,
								"description": dn_item.description,
								"qty": dn_item.qty,
								"rate": dn_item.rate,
								"amount": dn_item.amount,
								"warehouse": dn_item.warehouse,
								"uom": dn_item.uom,
								"stock_uom": dn_item.stock_uom,
								"conversion_factor": dn_item.conversion_factor or 1,
								"delivery_note": doc.name,
								"dn_detail": dn_item.name,
								"sales_order": dn_item.against_sales_order,
							},
						)

					# Copy taxes if any
					for tax in doc.get("taxes", []):
						si.append(
							"taxes",
							{
								"charge_type": tax.charge_type,
								"account_head": tax.account_head,
								"description": tax.description,
								"rate": tax.get("rate", 0),
								"tax_amount": tax.tax_amount,
							},
						)

					# Insert and submit invoice automatically
					si.insert(ignore_permissions=True)
					si.submit()

					# Show success message
					frappe.msgprint(
						f"Sales Invoice {si.name} created and submitted automatically.<br>"
						"Payment processing completed via SI After Submit script.",
						alert=True,
						indicator="green",
						title="Invoice Submitted",
					)

					# Add comment to DN
					doc.add_comment("Comment", f"Sales Invoice {si.name} auto-created from Delivery Note {doc.name}")

					# Mark as successful
					success = True

				except Exception as e:
					last_error = str(e)
					retry_count += 1

					# Check if it's a concurrency error that we can retry
					is_concurrency_error = (
						"modified after you pulled" in last_error
						or "has been modified" in last_error
						or "TimestampMismatchError" in last_error
					)

					if is_concurrency_error and retry_count < max_retries:
						# Log retry attempt
						frappe.log_error(
							f"Retry {retry_count}/{max_retries} for DN {doc.name}: {last_error}", "DN Auto Invoice Retry"
						)
						# Wait briefly before retry
						frappe.db.sql("SELECT SLEEP(0.5)")
						continue
					else:
						break

			# Handle final result
			if not success:
				frappe.log_error(
					f"Auto SI creation failed after {retry_count} retries: {last_error}", "DN Auto Invoice Error"
				)
				frappe.msgprint(
					f"❌ Failed to create Sales Invoice after {retry_count} retries.<br>"
					f"Error: {last_error}<br><br>"
					"Please try again or contact administrator.",
					alert=True,
					indicator="red",
					title="Invoice Creation Failed",
				)


def auto_return_stock_on_delivery_failed(doc, method=None):
	"""Auto-return stock and cancel DN when delivery fails.

	Event: After Submit

	Args:
		doc: Delivery Note document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	# Only process regular DNs (not returns)
	if doc.is_return != 1:
		# Check if this is a "Delivery Failed" submission
		if doc.workflow_state == "Delivery Failed":
			# Check if already processed (prevent double-execution)
			already_processed = frappe.db.exists(
				"Comment",
				{
					"reference_doctype": "Delivery Note",
					"reference_name": doc.name,
					"content": ["like", "%automatically cancelled due to Delivery Failed%"],
				},
			)

			# Only proceed if not already processed
			if not already_processed:
				# Get linked Sales Order to retrieve original source warehouse
				so_name = None
				for item in doc.items:
					if item.get("against_sales_order"):
						so_name = item.against_sales_order
						break

				if not so_name:
					frappe.throw("Cannot find linked Sales Order. Cannot determine source warehouse.")

				# Get original source warehouse from SO
				source_warehouse = frappe.db.get_value("Sales Order", so_name, "custom_source_warehouse")

				if not source_warehouse:
					frappe.throw(f"Source warehouse not found on Sales Order {so_name}. Cannot return stock.")

				# Find Hold warehouse
				hold_warehouse = frappe.db.get_value(
					"Warehouse", {"warehouse_name": ["like", "%Hold%"], "company": doc.company, "is_group": 0}, "name"
				)

				if not hold_warehouse:
					frappe.throw(f"Hold warehouse not found for company {doc.company}. Cannot return stock.")

				try:
					# Step 1: Cancel the Delivery Note FIRST
					dn = frappe.get_doc("Delivery Note", doc.name)
					dn.cancel()

					# Step 2: Create Stock Entry to return stock
					stock_entry = frappe.new_doc("Stock Entry")
					stock_entry.stock_entry_type = "Material Transfer"
					stock_entry.company = doc.company
					stock_entry.posting_date = frappe.utils.nowdate()
					stock_entry.posting_time = frappe.utils.nowtime()

					# Add items from DN
					for item in doc.items:
						stock_entry.append(
							"items",
							{
								"item_code": item.item_code,
								"qty": item.qty,
								"s_warehouse": hold_warehouse,
								"t_warehouse": source_warehouse,
								"uom": item.uom,
								"stock_uom": item.stock_uom,
								"conversion_factor": item.conversion_factor or 1,
								"transfer_qty": item.qty * (item.conversion_factor or 1),
							},
						)

					# Create and submit Stock Entry
					stock_entry.insert(ignore_permissions=True)
					stock_entry.submit()

					# Add comment to DN
					comment_doc = frappe.new_doc("Comment")
					comment_doc.comment_type = "Comment"
					comment_doc.reference_doctype = "Delivery Note"
					comment_doc.reference_name = doc.name
					comment_doc.content = (
						f"DN cancelled due to Delivery Failed. Stock returned from Hold to {source_warehouse} via {stock_entry.name}"
					)
					comment_doc.insert(ignore_permissions=True)

					# Step 3: Close Sales Order with delivery failed tracking
					if so_name:
						frappe.db.set_value(
							"Sales Order",
							so_name,
							{
								"custom_delivery_status": "Delivery Failed",
								"custom_is_delivery_failed": 1,
								"custom_delivery_failed_date": frappe.utils.today(),
								"custom_delivery_failed_reference": doc.name,
								"status": "Closed",
							},
							update_modified=False,
						)

						# Add audit comment to SO
						so_doc = frappe.get_doc("Sales Order", so_name)
						so_doc.add_comment("Comment", f"Closed due to delivery failure: DN {doc.name} (workflow_state = Delivery Failed)")

					# Show minimal message
					frappe.msgprint("DN cancelled • Stock returned • SO closed", indicator="orange")

				except Exception as e:
					frappe.log_error(f"Failed to return stock or cancel DN {doc.name}: {str(e)}")
					frappe.throw(f"Failed to process Delivery Failed: {str(e)}")
