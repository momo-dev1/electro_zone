"""
Sales Order event handlers for electro_zone app
"""

import frappe
import frappe.utils


def recalculate_amount(doc, method=None):
	"""Recalculate item amounts based on qty, rate, and discount_value.

	Server-side backup for amount calculation (Client Script may not fire in API calls).
	Formula: amount = qty × (rate - discount_value)

	Args:
		doc: Sales Order document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	for item in doc.items:
		discount_value = item.get("custom_discount_value") or 0
		effective_rate = item.rate - discount_value
		item.amount = item.qty * effective_rate


def validate_discount(doc, method=None):
	"""Validate discount_value against business rules and Item.valuation_rate.

	Ensures:
	1. Discount does not exceed rate
	2. Effective rate (rate - discount) maintains minimum margin above valuation_rate

	Args:
		doc: Sales Order document
		method: Event method name (unused, required by Frappe hook signature)

	Raises:
		frappe.ValidationError: If discount validation fails
	"""
	for item in doc.items:
		discount_value = item.get("custom_discount_value") or 0

		# VALIDATION 1: Prevent Discount > Rate
		if discount_value > item.rate:
			frappe.throw(
				f"Row {item.idx}: Item {item.item_code}\n\n"
				f"Discount Value ({discount_value:,.2f}) cannot exceed Rate ({item.rate:,.2f}).\n"
				"Please reduce the discount value.",
				title="Discount Exceeds Rate",
			)

		# VALIDATION 2: Check Against Valuation Rate
		# Ensures minimum margin is maintained
		# Error message intentionally simple - does NOT reveal valuation_rate value
		# Skip validation if valuation_rate is 0 or not set
		valuation_rate = frappe.db.get_value("Item", item.item_code, "valuation_rate") or 0

		if valuation_rate > 0:
			effective_rate = item.rate - discount_value

			if effective_rate < valuation_rate:
				frappe.throw(
					f"Row {item.idx}: Item {item.item_code} - Discount not allowed",
					title="Discount Validation Failed",
				)


def force_closed_if_returned(doc, method=None):
	"""Force status to Closed for returned orders to prevent status reversion.

	This intercepts ERPNext's status recalculation BEFORE it saves.

	Args:
		doc: Sales Order document (submitted)
		method: Event method name (unused, required by Frappe hook signature)
	"""
	if doc.get("custom_is_returned", 0) == 1:
		doc.status = "Closed"
		frappe.log_error(
			f"SO {doc.name} status forced to Closed (custom_is_returned=1)",
			"Returned SO Status Protection",
		)


def validate_cancellation(doc, method=None):
	"""Validate Sales Order cancellation to prevent cascade cancellation issues.

	Blocks cancellation if:
	1. ANY Sales Invoice is linked (prevents accounting inconsistencies)
	2. Delivery Note is NOT in "Pending Dispatch" state

	Args:
		doc: Sales Order document
		method: Event method name (unused, required by Frappe hook signature)

	Raises:
		frappe.ValidationError: If cancellation conditions are not met
	"""
	# Check for linked Sales Invoice Items
	si_items = frappe.db.get_all(
		"Sales Invoice Item", filters={"sales_order": doc.name}, fields=["parent"], limit=1
	)

	if si_items:
		si_name = si_items[0].parent
		si_doc = frappe.db.get_value(
			"Sales Invoice",
			si_name,
			["name", "docstatus", "posting_date", "grand_total"],
			as_dict=True,
		)

		if si_doc:
			status_text = "Submitted" if si_doc.docstatus == 1 else "Cancelled" if si_doc.docstatus == 2 else "Draft"

			frappe.throw(
				"Cannot cancel Sales Order. A Sales Invoice is linked to this order:<br><br>"
				f"• <b>{si_doc.name}</b> (Status: {status_text})<br><br>"
				"<b>Why this is blocked:</b><br>"
				"This Sales Order has a linked Sales Invoice. ERPNext's cascade cancellation "
				"attempts to cancel all linked documents automatically, which causes:<br>"
				"• Incorrect balance restoration order<br>"
				"• Accounting inconsistencies<br>"
				"• Stock return errors<br><br>"
				"<b>Correct Process:</b><br>"
				f"1. Manually cancel the Sales Invoice: <b>{si_doc.name}</b><br>"
				"2. Wait for balance restoration to complete<br>"
				"3. Then cancel this Sales Order<br><br>"
				"<b>Note:</b> You MUST follow this sequence. There is no shortcut.",
				title="Cancellation Blocked - Invoice Linked",
			)

	# Check Delivery Note workflow states
	dn_items = frappe.db.get_all(
		"Delivery Note Item",
		filters={"against_sales_order": doc.name},
		fields=["parent"],
		distinct=True,
	)

	if dn_items:
		dn_names = [item.parent for item in dn_items]
		all_dns = frappe.db.get_all(
			"Delivery Note",
			filters={"name": ["in", dn_names]},
			fields=["name", "docstatus", "workflow_state", "posting_date", "grand_total"],
		)

		for dn in all_dns:
			dn_workflow_state = dn.get("workflow_state", "")
			dn_docstatus = dn.get("docstatus", 0)

			# STRICT RULE: ONLY allow cancellation if workflow_state is "Pending Dispatch"
			if dn_workflow_state != "Pending Dispatch":
				if dn_docstatus == 2:
					status_display = "Cancelled"
				elif dn_docstatus == 1:
					status_display = dn_workflow_state or "Submitted"
				else:
					status_display = dn_workflow_state or "Draft"

				frappe.throw(
					"Cannot cancel Sales Order. Delivery Note must be in <b>Pending Dispatch</b> status.<br><br>"
					f"• Delivery Note: <b>{dn.name}</b><br>"
					f"• Current Status: <b>{status_display}</b><br>"
					f"• docstatus: {dn_docstatus}<br><br>"
					"<b>Why this is blocked:</b><br>"
					"Sales Orders can ONLY be cancelled if the Delivery Note is in <b>Pending Dispatch</b> status.<br><br>"
					f"<b>Current workflow_state: '{dn_workflow_state or '(empty)'}'</b><br><br>"
					"<b>Options:</b><br>"
					"1. If DN is submitted: Cancel the Delivery Note first, then cancel this Sales Order<br>"
					"2. If DN is in wrong state: Contact administrator<br><br>"
					"<b>Allowed:</b> workflow_state = 'Pending Dispatch' only",
					title="Cancellation Blocked - DN Not in Pending Dispatch",
				)


def move_to_hold(doc, method=None):
	"""Move stock to Hold warehouse.

	Workflow:
	1. Capture source warehouse for later stock return
	2. Skip if auto-created or Pending Review
	3. Create Stock Entry to transfer stock to Hold warehouse
	4. Update Sales Order items to use Hold warehouse

	Args:
		doc: Sales Order document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	# STEP 1: Capture source warehouse before modifications
	source_warehouse = None
	for so_item in doc.items:
		if so_item.warehouse:
			source_warehouse = so_item.warehouse
			break

	if source_warehouse:
		frappe.db.set_value(
			"Sales Order", doc.name, "custom_source_warehouse", source_warehouse, update_modified=False
		)

	# STEP 2: Check skip conditions
	comments = frappe.get_all(
		"Comment",
		filters={
			"reference_doctype": "Sales Order",
			"reference_name": doc.name,
			"content": "AUTO_CREATED_FROM_MARKETPLACE_ORDER",
		},
		limit=1,
	)

	is_auto_created = len(comments) > 0
	skip_conditions = is_auto_created or doc.status == "Pending Review"

	if skip_conditions:
		frappe.msgprint(
			"Auto-created or Pending Review Sales Order. Skipping Hold movement on this submission.",
			indicator="orange",
			title="Skipped",
		)
		return

	# STEP 3: Move stock to Hold warehouse
	hold_warehouse = frappe.db.get_value(
		"Warehouse",
		{"warehouse_name": ["like", "%Hold%"], "company": doc.company, "is_group": 0},
		"name",
	)

	if not hold_warehouse:
		frappe.throw(f"Hold warehouse not found for company {doc.company}. Please create it first.")

	# Create Stock Entry (Material Transfer)
	stock_entry = frappe.new_doc("Stock Entry")
	stock_entry.stock_entry_type = "Material Transfer"
	stock_entry.company = doc.company
	stock_entry.posting_date = doc.transaction_date
	stock_entry.posting_time = frappe.utils.nowtime()
	stock_entry.set_posting_time = 1

	items_added = False

	for so_item in doc.items:
		if not so_item.warehouse or frappe.utils.flt(so_item.qty) <= 0:
			continue

		if so_item.warehouse == hold_warehouse:
			continue

		available_qty = frappe.utils.flt(
			frappe.db.get_value(
				"Bin", {"item_code": so_item.item_code, "warehouse": so_item.warehouse}, "actual_qty"
			)
			or 0
		)

		if available_qty < frappe.utils.flt(so_item.qty):
			frappe.throw(
				f"Insufficient stock for <b>{so_item.item_code}</b> in <b>{so_item.warehouse}</b>. "
				f"Available: <b>{available_qty}</b>, Required: <b>{so_item.qty}</b>"
			)

		stock_uom = frappe.db.get_value("Item", so_item.item_code, "stock_uom")

		stock_entry.append(
			"items",
			{
				"item_code": so_item.item_code,
				"qty": frappe.utils.flt(so_item.qty),
				"s_warehouse": so_item.warehouse,
				"t_warehouse": hold_warehouse,
				"uom": so_item.uom or stock_uom,
				"stock_uom": stock_uom,
				"conversion_factor": frappe.utils.flt(so_item.conversion_factor) or 1.0,
				"transfer_qty": frappe.utils.flt(so_item.qty) * (frappe.utils.flt(so_item.conversion_factor) or 1.0),
				"sales_order": doc.name,
			},
		)
		items_added = True

	if not items_added:
		frappe.msgprint("No items to transfer to Hold warehouse.", indicator="orange", title="No Transfer")
		return

	stock_entry.insert(ignore_permissions=True)
	stock_entry.submit()

	# Update Sales Order items to use Hold warehouse
	for so_item in doc.items:
		if so_item.warehouse and so_item.warehouse != hold_warehouse:
			frappe.db.set_value("Sales Order Item", so_item.name, "warehouse", hold_warehouse, update_modified=False)

	doc.add_comment(
		"Comment", f'Stock moved to Hold via Stock Entry <a href="/app/stock-entry/{stock_entry.name}">{stock_entry.name}</a>'
	)

	frappe.msgprint(
		f"Stock Entry <b>{stock_entry.name}</b> created. Stock moved to <b>{hold_warehouse}</b>",
		indicator="green",
		title="Stock Reserved",
		alert=True,
	)


def deduct_balance(doc, method=None):
	"""Reserve balance from custom_current_balance for Sales Order.

	Args:
		doc: Sales Order document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	from .customer_balance_manager import reserve_balance_for_so, get_available_balance

	customer = doc.customer
	so_total = doc.grand_total

	# Check available balance
	available_balance = get_available_balance(customer)

	if available_balance > 0:
		# Reserve the amount (or partial if insufficient)
		reserved_amount = min(available_balance, so_total)

		# Atomic operation: reserve balance and update SO
		result = reserve_balance_for_so(customer, doc.name, reserved_amount)

		if result.get("success"):
			# Update SO advance_paid to reflect reserved amount
			frappe.db.set_value("Sales Order", doc.name, "advance_paid", reserved_amount, update_modified=False)

			# Track reserved amount in custom field
			frappe.db.set_value("Sales Order", doc.name, "custom_balance_reserved", reserved_amount, update_modified=False)

			# Show message
			frappe.msgprint(
				f"Reserved {frappe.format_value(reserved_amount, {'fieldtype': 'Currency'})} from customer balance.<br>"
				f"Remaining available balance: <b>{frappe.format_value(result['available_balance'], {'fieldtype': 'Currency'})}</b>",
				indicator="green",
				title="Balance Reserved",
			)
		else:
			# Reservation failed
			frappe.msgprint(f"Failed to reserve balance: {result.get('message', 'Unknown error')}", indicator="orange")
	else:
		# No balance to reserve
		frappe.msgprint("Customer has no available balance. Order created as unpaid.", indicator="blue", title="No Balance to Reserve")


def cancel_and_return_stock(doc, method=None):
	"""Cancel Pending Dispatch DNs, return stock to source, and restore balance.

	Workflow:
	1. Release reserved balance FIRST
	2. Auto-cancel Delivery Notes in Pending Dispatch state
	3. Create Stock Entry to return stock from Hold to source warehouse

	Args:
		doc: Sales Order document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	from .customer_balance_manager import release_reserved_balance

	# STEP 1: Release reserved balance FIRST
	try:
		result = release_reserved_balance(doc.customer, doc.name)
		if result.get("success") and result.get("released_amount", 0) > 0:
			frappe.msgprint(
				f"Released {frappe.format_value(result['released_amount'], {'fieldtype': 'Currency'})} reserved balance.",
				indicator="green",
				title="Balance Released",
			)
	except Exception as e:
		frappe.log_error(f"Failed to release reserved balance for SO {doc.name}: {str(e)}", "Balance Release Error")
		frappe.msgprint(f"Warning: Failed to release reserved balance: {str(e)}", indicator="orange")

	# STEP 2: Auto-cancel draft Delivery Notes linked to this SO
	dn_items = frappe.db.get_all(
		"Delivery Note Item", filters={"against_sales_order": doc.name}, fields=["parent"], distinct=True
	)

	if dn_items:
		dn_names = list(set([item.parent for item in dn_items]))

		for dn_name in dn_names:
			try:
				dn_doc = frappe.get_doc("Delivery Note", dn_name)
				dn_workflow_state = dn_doc.get("workflow_state", "")

				# Only cancel DNs in Pending Dispatch state
				if dn_workflow_state == "Pending Dispatch":
					frappe.db.set_value(
						"Delivery Note", dn_name, {"docstatus": 2, "workflow_state": "Cancelled"}, update_modified=False
					)

					dn_doc.add_comment("Comment", f"Auto-cancelled because linked Sales Order {doc.name} was cancelled")

					frappe.msgprint(
						f"Cancelled Delivery Note: <b>{dn_name}</b> (was in Pending Dispatch status)",
						indicator="blue",
						title="DN Cancelled",
					)

					frappe.log_error(
						f"Cancelled DN {dn_name} (Pending Dispatch) linked to cancelled SO {doc.name}",
						"Auto-Cancel DN - Pending Dispatch",
					)
				else:
					frappe.msgprint(
						f"Skipped DN <b>{dn_name}</b> - not in Pending Dispatch (workflow_state: {dn_workflow_state or '(empty)'})",
						indicator="orange",
						title="DN Not Cancelled",
					)

			except Exception as e:
				frappe.log_error(
					f"Failed to cancel DN {dn_name} for SO {doc.name}: {str(e)}", "DN Cancellation Error"
				)
				frappe.msgprint(
					f"Failed to cancel Delivery Note <b>{dn_name}</b>: {str(e)}",
					indicator="orange",
					title="DN Cancellation Failed",
				)

	# Stock return logic
	hold_warehouse = frappe.db.get_value(
		"Warehouse",
		{"warehouse_name": ["like", "%Hold%"], "company": doc.company, "is_group": 0},
		"name",
	)

	if not hold_warehouse:
		frappe.msgprint("Hold warehouse not found. Stock return skipped.", indicator="yellow", title="No Hold Warehouse")
	else:
		target_warehouse = doc.get("custom_source_warehouse")

		if not target_warehouse:
			frappe.msgprint(
				"custom_source_warehouse not set. Cannot determine where to return stock. "
				"Please manually create Stock Entry to return stock from Hold warehouse.",
				indicator="orange",
				title="Manual Stock Return Required",
			)
		else:
			stock_entry = frappe.new_doc("Stock Entry")
			stock_entry.stock_entry_type = "Material Transfer"
			stock_entry.company = doc.company
			stock_entry.posting_date = frappe.utils.nowdate()
			stock_entry.posting_time = frappe.utils.nowtime()
			stock_entry.set_posting_time = 1

			items_added = False

			for so_item in doc.items:
				if not so_item.item_code or frappe.utils.flt(so_item.qty) <= 0:
					continue

				available_qty = frappe.utils.flt(
					frappe.db.get_value("Bin", {"item_code": so_item.item_code, "warehouse": hold_warehouse}, "actual_qty") or 0
				)

				if available_qty < frappe.utils.flt(so_item.qty):
					frappe.msgprint(
						f"Insufficient stock in Hold for <b>{so_item.item_code}</b>. "
						f"Available: {available_qty}, Required: {so_item.qty}. Item skipped.",
						indicator="orange",
						title="Insufficient Stock",
					)
					continue

				stock_uom = frappe.db.get_value("Item", so_item.item_code, "stock_uom")

				stock_entry.append(
					"items",
					{
						"item_code": so_item.item_code,
						"qty": frappe.utils.flt(so_item.qty),
						"s_warehouse": hold_warehouse,
						"t_warehouse": target_warehouse,
						"uom": so_item.uom or stock_uom,
						"stock_uom": stock_uom,
						"conversion_factor": frappe.utils.flt(so_item.conversion_factor) or 1.0,
						"transfer_qty": frappe.utils.flt(so_item.qty) * (frappe.utils.flt(so_item.conversion_factor) or 1.0),
					},
				)
				items_added = True

			if items_added:
				try:
					stock_entry.insert(ignore_permissions=True)
					stock_entry.submit()

					doc.add_comment(
						"Comment",
						f'Stock returned to {target_warehouse} via Stock Entry <a href="/app/stock-entry/{stock_entry.name}">{stock_entry.name}</a>',
					)

					frappe.msgprint(
						f"Stock Entry <b>{stock_entry.name}</b> created. Stock returned from Hold to <b>{target_warehouse}</b>.",
						indicator="green",
						title="Stock Returned",
						alert=True,
					)

				except Exception as e:
					error_msg = str(e)
					frappe.log_error(f"Failed to create Stock Entry for SO {doc.name}: {error_msg}", "Stock Return Error")
					frappe.msgprint(
						f"Failed to return stock from Hold to <b>{target_warehouse}</b>:<br>"
						f"<b>Error:</b> {error_msg}<br><br>"
						"Please manually create Stock Entry to return stock.",
						indicator="red",
						title="Stock Return Failed",
					)
			else:
				frappe.msgprint(
					"No items to return from Hold warehouse (insufficient stock or no items).",
					indicator="yellow",
					title="No Items to Return",
				)

	# Create reference-only ledger entry for cancellation
	so_total = doc.grand_total
	customer = doc.customer
	current_balance = frappe.db.get_value("Customer", customer, "custom_current_balance") or 0.0

	# Create REFERENCE-ONLY ledger entry
	ledger = frappe.new_doc("Customer Balance Ledger")
	ledger.transaction_date = frappe.utils.today()
	ledger.posting_time = frappe.utils.nowtime()
	ledger.customer = customer
	ledger.customer_name = doc.customer_name
	ledger.reference_doctype = "Sales Order"
	ledger.reference_document = doc.name
	ledger.reference_date = doc.transaction_date
	ledger.debit_amount = 0.0  # Reference only
	ledger.credit_amount = 0.0  # Reference only
	ledger.balance_before = current_balance
	ledger.running_balance = current_balance  # Unchanged
	ledger.remarks = f"Reference only - GL tracked. SO {doc.name} cancelled (Amount: {frappe.format_value(so_total, {'fieldtype': 'Currency'})})"
	ledger.company = doc.company
	ledger.created_by = frappe.session.user
	ledger.insert(ignore_permissions=True)

	# Show cancellation notification
	frappe.msgprint(
		f"Sales Order cancelled successfully. Balance tracked in GL.<br>"
		f"Current balance: <b>{frappe.format_value(current_balance, {'fieldtype': 'Currency'})}</b>",
		indicator="blue",
		title="SO Cancelled",
	)
