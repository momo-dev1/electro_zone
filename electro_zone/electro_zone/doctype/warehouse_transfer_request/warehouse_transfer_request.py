# Copyright (c) 2025, didy1234567@gmail.com and contributors
# For license information, please see license.txt

import json

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now, today


class WarehouseTransferRequest(Document):
	def before_insert(self):
		"""Auto-set fields on creation"""
		if not self.requested_by:
			self.requested_by = frappe.session.user

		if not self.requested_date:
			self.requested_date = today()

		if not self.approval_status:
			self.approval_status = "Draft"

	def before_save(self):
		"""Auto-determine transfer type, validate warehouse rules, and calculate pending quantities"""
		self.auto_determine_transfer_type()
		self.validate_transfer_type()
		self.calculate_pending_quantities()

	def auto_determine_transfer_type(self):
		"""Determine if transfer is Internal or External based on warehouse groups"""
		if not self.source_warehouse or not self.target_warehouse:
			frappe.throw(_("Source and Target warehouses required"))

		if self.source_warehouse == self.target_warehouse:
			frappe.throw(_("Source and Target must be different"))

		source_parent = self.get_warehouse_group(self.source_warehouse)
		target_parent = self.get_warehouse_group(self.target_warehouse)

		if source_parent == target_parent:
			self.transfer_type = "Internal Transfer"
		else:
			self.transfer_type = "External Transfer"

	def get_warehouse_group(self, warehouse_name):
		"""Get the parent warehouse group for a given warehouse"""
		if not warehouse_name:
			return None

		wh = frappe.db.get_value(
			"Warehouse", warehouse_name, ["parent_warehouse", "is_group"], as_dict=True
		)

		if not wh:
			return None

		return wh.parent_warehouse if wh.parent_warehouse else (warehouse_name if wh.is_group else warehouse_name)

	def validate_transfer_type(self):
		"""Validate warehouse selection based on transfer type rules"""
		# Define warehouse groups
		GROUP_A = ["Zahran Main - EZ", "Damage - EZ", "Damage For Sale - EZ"]
		GROUP_B = ["Store Warehouse - EZ", "Store Display - EZ", "Store Damage - EZ"]
		EXTERNAL_WAREHOUSES = ["Zahran Main - EZ", "Store Warehouse - EZ"]
		HOLD_WAREHOUSE = "Hold (Reserved / Pending Shipment) - EZ"

		# Basic validation
		if not self.source_warehouse or not self.target_warehouse:
			frappe.throw(_("Source and Target warehouses are required"))

		if self.source_warehouse == self.target_warehouse:
			frappe.throw(_("Source and Target warehouses must be different"))

		if not self.transfer_type:
			frappe.throw(_("Transfer Type is required"))

		# Check if Hold warehouse is selected (not allowed in transfers)
		if self.source_warehouse == HOLD_WAREHOUSE or self.target_warehouse == HOLD_WAREHOUSE:
			frappe.throw(_("Hold (Reserved / Pending Shipment) warehouse cannot be used in transfers"))

		# Validate based on Transfer Type
		if self.transfer_type == "Internal Transfer":
			# Check if both warehouses are in same group
			source_in_group_a = self.source_warehouse in GROUP_A
			target_in_group_a = self.target_warehouse in GROUP_A
			source_in_group_b = self.source_warehouse in GROUP_B
			target_in_group_b = self.target_warehouse in GROUP_B

			# Both must be in the same group
			if source_in_group_a and not target_in_group_a:
				valid_targets = [w for w in GROUP_A if w != self.source_warehouse]
				frappe.throw(
					_(
						"For Internal Transfer with source '{0}', target must be one of: {1}"
					).format(self.source_warehouse, ", ".join(valid_targets))
				)

			if source_in_group_b and not target_in_group_b:
				valid_targets = [w for w in GROUP_B if w != self.source_warehouse]
				frappe.throw(
					_(
						"For Internal Transfer with source '{0}', target must be one of: {1}"
					).format(self.source_warehouse, ", ".join(valid_targets))
				)

			if not (source_in_group_a or source_in_group_b):
				frappe.throw(
					_(
						"Source warehouse '{0}' is not valid for Internal Transfer. Must be from Group A or Group B."
					).format(self.source_warehouse)
				)

			if not (target_in_group_a or target_in_group_b):
				frappe.throw(
					_(
						"Target warehouse '{0}' is not valid for Internal Transfer. Must be from Group A or Group B."
					).format(self.target_warehouse)
				)

		elif self.transfer_type == "External Transfer":
			# Both must be in EXTERNAL_WAREHOUSES list
			if self.source_warehouse not in EXTERNAL_WAREHOUSES:
				frappe.throw(
					_(
						"Source warehouse '{0}' is not valid for External Transfer. Must be: {1}"
					).format(self.source_warehouse, " or ".join(EXTERNAL_WAREHOUSES))
				)

			if self.target_warehouse not in EXTERNAL_WAREHOUSES:
				frappe.throw(
					_(
						"Target warehouse '{0}' is not valid for External Transfer. Must be: {1}"
					).format(self.target_warehouse, " or ".join(EXTERNAL_WAREHOUSES))
				)

			# Must be different warehouses (already checked above)
			if self.source_warehouse == self.target_warehouse:
				frappe.throw(_("For External Transfer, source and target must be different warehouses"))

	def calculate_pending_quantities(self):
		"""Calculate pending quantities and validate shipped/received quantities"""
		for item in self.items:
			# Auto-set accepted_qty = requested_qty on Draft (before approval)
			# External Manager can edit this during approval
			if self.approval_status == "Draft" and (not item.accepted_qty or item.accepted_qty == 0):
				item.accepted_qty = item.requested_qty

			# Skip items with accepted_qty = 0 (excluded by External Manager)
			if item.accepted_qty == 0:
				continue

			# Calculate pending based on workflow stage
			if item.shipped_qty > 0:
				# After shipping: pending = shipped - received
				item.pending_qty = item.shipped_qty - item.received_qty
			else:
				# Before shipping: pending = accepted - shipped
				item.pending_qty = item.accepted_qty - item.shipped_qty

			# Validation 1: Shipped cannot exceed accepted
			if item.shipped_qty > item.accepted_qty:
				frappe.throw(
					_(
						"Shipped quantity ({0}) cannot exceed accepted quantity ({1}) for {2}"
					).format(item.shipped_qty, item.accepted_qty, item.item_code)
				)

			# Validation 2: Received cannot exceed shipped
			if item.shipped_qty > 0 and item.received_qty > item.shipped_qty:
				frappe.throw(
					_(
						"Received quantity ({0}) cannot exceed shipped quantity ({1}) for {2}"
					).format(item.received_qty, item.shipped_qty, item.item_code)
				)

			# Validation 3: If not shipped yet, received must be 0
			if item.shipped_qty == 0 and item.received_qty > 0:
				frappe.throw(
					_("Cannot receive items before they are shipped. Item: {0}").format(item.item_code)
				)

			# Validation 4: Accepted cannot exceed requested
			if item.accepted_qty > item.requested_qty:
				frappe.throw(
					_(
						"Accepted quantity ({0}) cannot exceed requested quantity ({1}) for {2}"
					).format(item.accepted_qty, item.requested_qty, item.item_code)
				)


@frappe.whitelist()
def approve_transfer(transfer_name, accepted_items=None):
	"""
	API method to approve a warehouse transfer request

	Args:
		transfer_name: Name of the Warehouse Transfer Request document
		accepted_items: JSON string with accepted quantities [{item_code: str, qty: float}]

	Returns:
		dict: {success: bool, message: str}
	"""
	if not transfer_name:
		return {"success": False, "message": "Transfer name required"}

	doc = frappe.get_doc("Warehouse Transfer Request", transfer_name)

	# Check if user has External Transfer Manager role (v15.8 compatible)
	has_role = frappe.db.exists(
		"Has Role", {"parent": frappe.session.user, "role": "External Transfer Manager"}
	)

	if not has_role:
		return {"success": False, "message": "Only External Transfer Manager can approve"}

	if doc.approval_status != "Pending Approval":
		return {"success": False, "message": f"Cannot approve from status: {doc.approval_status}"}

	validation_error = None

	# Update accepted quantities if provided
	if accepted_items:
		accepted_data = json.loads(accepted_items)

		# Validate accepted quantities
		for accepted in accepted_data:
			if not validation_error:
				for item in doc.items:
					if item.item_code == accepted["item_code"]:
						accepted_qty = accepted.get("qty", 0)

						# Validate: accepted cannot exceed requested
						if accepted_qty > item.requested_qty:
							validation_error = f"Accepted quantity ({accepted_qty}) cannot exceed requested quantity ({item.requested_qty}) for {accepted['item_code']}"
							break

						# Update accepted_qty
						item.accepted_qty = accepted_qty
						break

	# Process approval only if no validation errors
	if validation_error:
		return {"success": False, "message": validation_error}

	# Check if at least one item has accepted_qty > 0
	has_accepted_items = any(item.accepted_qty > 0 for item in doc.items)
	if not has_accepted_items:
		return {
			"success": False,
			"message": "Cannot approve: all items have been excluded (accepted_qty = 0)",
		}

	doc.approval_status = "Approved - Pending Shipment"
	doc.approved_by = frappe.session.user
	doc.approval_date = now()
	doc.save()

	return {"success": True, "message": "Approved successfully"}


@frappe.whitelist()
def submit_for_approval(transfer_name):
	"""
	API method to submit a transfer request for approval

	Args:
		transfer_name: Name of the Warehouse Transfer Request document

	Returns:
		dict: {success: bool, message: str}
	"""
	if not transfer_name:
		return {"success": False, "message": "Transfer name required"}

	doc = frappe.get_doc("Warehouse Transfer Request", transfer_name)

	if not doc.items or len(doc.items) == 0:
		return {"success": False, "message": "Cannot submit without items"}

	if doc.transfer_type == "Internal Transfer":
		# Internal: Auto-complete with Stock Entry
		insufficient = []
		for item in doc.items:
			# Use accepted_qty (defaults to requested_qty if not set)
			qty_to_transfer = item.accepted_qty if item.accepted_qty > 0 else item.requested_qty

			available = (
				frappe.db.get_value(
					"Bin", {"item_code": item.item_code, "warehouse": doc.source_warehouse}, "actual_qty"
				)
				or 0
			)

			if available < qty_to_transfer:
				insufficient.append(f"{item.item_code}: Available={available}, Required={qty_to_transfer}")

		if insufficient:
			return {"success": False, "message": "<br>".join(insufficient)}

		# Create Stock Entry
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Transfer"
		se.company = frappe.db.get_single_value("Global Defaults", "default_company")
		se.posting_date = today()

		for item in doc.items:
			# Use accepted_qty (defaults to requested_qty if not set)
			qty_to_transfer = item.accepted_qty if item.accepted_qty > 0 else item.requested_qty

			se.append(
				"items",
				{
					"item_code": item.item_code,
					"qty": qty_to_transfer,
					"s_warehouse": doc.source_warehouse,
					"t_warehouse": doc.target_warehouse,
					"uom": item.uom,
				},
			)

		try:
			se.insert()
			se.submit()

			# Set shipped_qty and received_qty for internal transfers
			for item in doc.items:
				qty_to_transfer = item.accepted_qty if item.accepted_qty > 0 else item.requested_qty
				item.shipped_qty = qty_to_transfer
				item.received_qty = qty_to_transfer
				item.pending_qty = 0

			doc.approval_status = "Completed"
			doc.approved_by = frappe.session.user
			doc.approval_date = now()

			total_qty = sum(
				item.accepted_qty if item.accepted_qty > 0 else item.requested_qty for item in doc.items
			)

			doc.append(
				"stock_entries",
				{"stock_entry": se.name, "posting_date": se.posting_date, "total_qty": total_qty},
			)

			# Submit document when Completed
			doc.docstatus = 1
			doc.save()
			doc.submit()

			return {"success": True, "message": f"Completed and submitted. Stock Entry {se.name} created"}
		except Exception as e:
			return {"success": False, "message": str(e)}
	else:
		# External: Submit for approval
		doc.approval_status = "Pending Approval"
		doc.save()
		return {"success": True, "message": "Submitted for approval"}


@frappe.whitelist()
def reject_transfer(transfer_name, rejection_reason=None):
	"""
	API method to reject a warehouse transfer request

	Args:
		transfer_name: Name of the Warehouse Transfer Request document
		rejection_reason: Reason for rejection

	Returns:
		dict: {success: bool, message: str}
	"""
	if not transfer_name:
		return {"success": False, "message": "Transfer name required"}

	if not rejection_reason:
		return {"success": False, "message": "Rejection reason required"}

	doc = frappe.get_doc("Warehouse Transfer Request", transfer_name)

	# Check if user has External Transfer Manager role (v15.8 compatible)
	has_role = frappe.db.exists(
		"Has Role", {"parent": frappe.session.user, "role": "External Transfer Manager"}
	)

	if not has_role:
		return {"success": False, "message": "Only External Transfer Manager can reject"}

	doc.approval_status = "Rejected"
	doc.rejection_reason = rejection_reason
	doc.save()

	return {"success": True, "message": "Rejected"}


@frappe.whitelist()
def mark_as_shipped(transfer_name, shipped_items):
	"""
	API method to mark items as shipped

	Args:
		transfer_name: Name of the Warehouse Transfer Request document
		shipped_items: JSON string with shipped quantities [{item_code: str, qty: float}]

	Returns:
		dict: {success: bool, message: str}
	"""
	if not transfer_name or not shipped_items:
		return {"success": False, "message": "Transfer name and shipped items required"}

	doc = frappe.get_doc("Warehouse Transfer Request", transfer_name)

	if doc.approval_status != "Approved - Pending Shipment":
		return {"success": False, "message": f"Cannot ship from status: {doc.approval_status}"}

	# Validate authority
	source_parent = frappe.db.get_value("Warehouse", doc.source_warehouse, "parent_warehouse")
	has_authority = False

	if source_parent:
		# Check roles using v15.8 compatible method
		has_zahran_role = frappe.db.exists(
			"Has Role", {"parent": frappe.session.user, "role": "Zahran Warehouse Manager"}
		)
		has_store_role = frappe.db.exists(
			"Has Role", {"parent": frappe.session.user, "role": "Store Warehouse Manager"}
		)

		if "Zahran" in source_parent and has_zahran_role:
			has_authority = True
		elif "Store" in source_parent and has_store_role:
			has_authority = True

	if not has_authority:
		return {"success": False, "message": f"Only {source_parent} manager can ship"}

	# Parse shipped items JSON
	shipped_data = json.loads(shipped_items)
	validation_error = None

	# Validation 1: Check for negative or zero quantities
	for ship in shipped_data:
		if not validation_error:
			shipping_now = ship.get("qty", 0)
			if shipping_now <= 0:
				validation_error = f"Shipping quantity must be greater than 0 for {ship['item_code']}"

	# Validation 2: Check if shipped exceeds accepted
	if not validation_error:
		for ship in shipped_data:
			if not validation_error:
				for item in doc.items:
					if item.item_code == ship["item_code"]:
						shipping_now = ship.get("qty", 0)
						new_total_shipped = item.shipped_qty + shipping_now

						# Check if item has accepted_qty > 0 (not excluded)
						if item.accepted_qty == 0:
							validation_error = f"Cannot ship {ship['item_code']} - item was excluded by External Transfer Manager (accepted_qty = 0)"
							break

						# Validate against accepted_qty (not requested_qty)
						if new_total_shipped > item.accepted_qty:
							validation_error = f"Cannot ship {shipping_now} more of {ship['item_code']}. Already shipped {item.shipped_qty}, accepted {item.accepted_qty}. Maximum can ship: {item.accepted_qty - item.shipped_qty}."
							break

	# Validation 3: Check if sufficient stock available
	if not validation_error:
		insufficient = []
		for ship in shipped_data:
			shipping_now = ship.get("qty", 0)
			if shipping_now > 0:
				available = (
					frappe.db.get_value(
						"Bin", {"item_code": ship["item_code"], "warehouse": doc.source_warehouse}, "actual_qty"
					)
					or 0
				)

				if shipping_now > available:
					insufficient.append(
						f"{ship['item_code']}: Available={available}, Trying to ship={shipping_now}"
					)

		if insufficient:
			validation_error = "<br>".join(insufficient)

	# Process shipment if all validations passed
	if validation_error:
		return {"success": False, "message": validation_error}

	# Update shipped quantities
	for ship in shipped_data:
		for item in doc.items:
			if item.item_code == ship["item_code"]:
				shipping_now = ship.get("qty", 0)
				if shipping_now > 0:
					item.shipped_qty = item.shipped_qty + shipping_now

	# Check if all ACCEPTED items fully shipped (exclude items with accepted_qty = 0)
	all_shipped = all(item.shipped_qty >= item.accepted_qty for item in doc.items if item.accepted_qty > 0)

	if all_shipped:
		doc.approval_status = "Shipped"
	else:
		doc.approval_status = "Partially Shipped"

	doc.save()
	return {"success": True, "message": "Shipment recorded successfully"}


@frappe.whitelist()
def confirm_receipt(transfer_name, received_items):
	"""
	API method to confirm receipt of shipped items

	Args:
		transfer_name: Name of the Warehouse Transfer Request document
		received_items: JSON string with received quantities [{item_code: str, qty: float}]

	Returns:
		dict: {success: bool, message: str}
	"""
	if not transfer_name or not received_items:
		return {"success": False, "message": "Transfer name and items required"}

	doc = frappe.get_doc("Warehouse Transfer Request", transfer_name)

	if doc.approval_status not in ["Shipped", "Partially Completed"]:
		return {"success": False, "message": f"Cannot receive from status: {doc.approval_status}"}

	# Validate authority
	target_parent = frappe.db.get_value("Warehouse", doc.target_warehouse, "parent_warehouse")
	has_authority = False

	if target_parent:
		has_zahran_role = frappe.db.exists(
			"Has Role", {"parent": frappe.session.user, "role": "Zahran Warehouse Manager"}
		)
		has_store_role = frappe.db.exists(
			"Has Role", {"parent": frappe.session.user, "role": "Store Warehouse Manager"}
		)

		if "Zahran" in target_parent and has_zahran_role:
			has_authority = True
		elif "Store" in target_parent and has_store_role:
			has_authority = True

	if not has_authority:
		return {"success": False, "message": f"Only {target_parent} manager can receive"}

	# Parse JSON
	received_data = json.loads(received_items)
	validation_error = None

	# Validation 1: Check for negative or zero quantities
	for recv in received_data:
		if not validation_error:
			receiving_now = recv.get("qty", 0)
			if receiving_now <= 0:
				validation_error = f"Receiving quantity must be greater than 0 for {recv['item_code']}"

	# Validation 2: Check if total received (current + new) exceeds shipped_qty
	if not validation_error:
		for recv in received_data:
			if not validation_error:
				for item in doc.items:
					if item.item_code == recv["item_code"]:
						receiving_now = recv.get("qty", 0)
						new_total = item.received_qty + receiving_now

						# Check if items were shipped
						if item.shipped_qty == 0:
							validation_error = f"Cannot receive {recv['item_code']} - no items shipped yet"
							break

						# Cannot receive more than shipped
						if new_total > item.shipped_qty:
							validation_error = f"Cannot receive {receiving_now} more of {recv['item_code']}. Already received {item.received_qty}, shipped {item.shipped_qty}. Maximum can receive: {item.shipped_qty - item.received_qty}."
							break

	# Validation 3: Check if sufficient stock available in source warehouse
	if not validation_error:
		for recv in received_data:
			if not validation_error:
				receiving_now = recv.get("qty", 0)
				if receiving_now > 0:
					# Check actual stock in source warehouse
					available_qty = (
						frappe.db.get_value(
							"Bin", {"item_code": recv["item_code"], "warehouse": doc.source_warehouse}, "actual_qty"
						)
						or 0
					)

					if receiving_now > available_qty:
						validation_error = f"Insufficient stock in source warehouse. Item: {recv['item_code']}, Available: {available_qty}, Trying to receive: {receiving_now}"

	# Process receipt if all validations passed
	if validation_error:
		return {"success": False, "message": validation_error}

	# Update received quantities (all validations passed)
	items_to_transfer = []
	for recv in received_data:
		for item in doc.items:
			if item.item_code == recv["item_code"]:
				receiving_now = recv.get("qty", 0)
				if receiving_now > 0:
					# Add to cumulative received_qty
					item.received_qty = item.received_qty + receiving_now
				item.pending_qty = item.requested_qty - item.received_qty

				# Track for Stock Entry creation
				items_to_transfer.append(
					{
						"item_code": item.item_code,
						"qty": receiving_now,
						"uom": item.uom,
					}
				)

	# Save document immediately to persist received_qty changes
	doc.save()

	# Create Stock Entry for newly received items
	stock_entry_error = None
	if items_to_transfer:
		se = frappe.new_doc("Stock Entry")
		se.stock_entry_type = "Material Transfer"
		se.company = frappe.db.get_single_value("Global Defaults", "default_company")
		se.posting_date = today()

		for item_data in items_to_transfer:
			se.append(
				"items",
				{
					"item_code": item_data["item_code"],
					"qty": item_data["qty"],
					"s_warehouse": doc.source_warehouse,
					"t_warehouse": doc.target_warehouse,
					"uom": item_data["uom"],
				},
			)

		try:
			se.insert()
			se.submit()
			doc.append(
				"stock_entries",
				{
					"stock_entry": se.name,
					"posting_date": se.posting_date,
					"total_qty": sum(i["qty"] for i in items_to_transfer),
				},
			)
		except Exception as e:
			stock_entry_error = str(e)

	if stock_entry_error:
		return {"success": False, "message": stock_entry_error}

	# Update status - check if all SHIPPED items have been received
	all_received = all(item.received_qty >= item.shipped_qty for item in doc.items if item.shipped_qty > 0)

	if all_received:
		# All shipped items received - mark as Completed and submit document
		doc.approval_status = "Completed"
		doc.docstatus = 1  # Submit the document
		doc.save()
		return {"success": True, "message": "Transfer completed and submitted"}
	else:
		# Partial receipt - keep status
		doc.approval_status = "Partially Completed"
		doc.save()
		return {"success": True, "message": "Partial receipt confirmed"}


@frappe.whitelist()
def get_accepted_qty(transfer_name, item_code):
	"""
	API method to get accepted quantity for barcode scanning
	Runs with elevated permissions (bypasses Stock User restrictions)

	Args:
		transfer_name: Name of the Warehouse Transfer Request document
		item_code: Item code to look up

	Returns:
		dict: {success: bool, accepted_qty: float, shipped_qty: float, error: str}
	"""
	if not transfer_name or not item_code:
		return {"success": False, "error": "Missing transfer_name or item_code"}

	# Get accepted_qty and shipped_qty from Warehouse Transfer Request Item
	result = frappe.db.get_value(
		"Warehouse Transfer Request Item",
		{"parent": transfer_name, "item_code": item_code},
		["accepted_qty", "shipped_qty"],
		as_dict=True,
	)

	if result:
		return {"success": True, "accepted_qty": result.accepted_qty or 0, "shipped_qty": result.shipped_qty or 0}
	else:
		return {"success": False, "error": "Item not found in transfer request"}


@frappe.whitelist()
def get_item_by_barcode(barcode):
	"""
	API method to find item code by barcode
	Runs with elevated permissions to bypass Stock User restrictions

	Args:
		barcode: Barcode string to search

	Returns:
		dict: {success: bool, item_code: str, error: str}
	"""
	if not barcode:
		return {"success": False, "error": "Barcode is required"}

	# Search for barcode in Item Barcode child table
	result = frappe.db.get_value("Item Barcode", {"barcode": barcode}, "parent")

	if result:
		return {"success": True, "item_code": result}
	else:
		return {"success": False, "error": f"Barcode '{barcode}' not found"}


@frappe.whitelist()
def validate_items_for_upload(items, source_warehouse=None, target_warehouse=None):
	"""
	API method to validate items for bulk upload

	Args:
		items: JSON string with items [{item_code: str, requested_qty: int, requester_notes: str}]
		source_warehouse: Source warehouse name
		target_warehouse: Target warehouse name

	Returns:
		dict: {success: bool, errors: list, validated_items: list}
	"""
	# Parse items JSON
	items_data = json.loads(items) if isinstance(items, str) else items

	errors = []
	validated_items = []
	seen_items = set()

	# Validate each item
	for idx, item_data in enumerate(items_data):
		line_num = idx + 2  # Excel row number (data starts at row 2)
		item_code = item_data.get("item_code", "").strip()
		requested_qty = item_data.get("requested_qty")
		requester_notes = item_data.get("requester_notes", "").strip()

		# Truncate requester_notes to 55 chars if needed
		if len(requester_notes) > 55:
			requester_notes = requester_notes[:55]

		# Skip empty rows
		if not item_code and not requested_qty:
			continue

		# Validation 1: Item code is required
		if not item_code:
			errors.append(f"Line {line_num}: Item code is required")
			continue

		# Validation 2: Quantity is required and must be > 0
		if not requested_qty or requested_qty <= 0:
			errors.append(f"Line {line_num}: Quantity must be greater than 0 for item {item_code}")
			continue

		# Validation 3: Quantity must be integer
		if not isinstance(requested_qty, int):
			errors.append(f"Line {line_num}: Quantity must be a whole number for item {item_code}")
			continue

		# Validation 4: Check for duplicate item_code in upload
		if item_code in seen_items:
			errors.append(f"Line {line_num}: Duplicate item code {item_code} found in upload")
			continue
		seen_items.add(item_code)

		# Validation 5: Check if item exists in Item master
		item_exists = frappe.db.exists("Item", {"item_code": item_code})
		if not item_exists:
			errors.append(f"Line {line_num}: Item {item_code} does not exist in Item master")
			continue

		# Validation 6: Check stock availability in source warehouse (if source_warehouse is set)
		if source_warehouse:
			available_qty = (
				frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": source_warehouse}, "actual_qty")
				or 0
			)

			if available_qty < requested_qty:
				errors.append(
					f"Line {line_num}: Insufficient stock for {item_code}. "
					f"Available: {available_qty}, Requested: {requested_qty}"
				)
				continue

		# Get item details for validated items
		item_name = frappe.db.get_value("Item", item_code, "item_name")
		uom = frappe.db.get_value("Item", item_code, "stock_uom")

		# Get available quantities from source warehouse (if set)
		available_qty = 0
		if source_warehouse:
			available_qty = (
				frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": source_warehouse}, "actual_qty")
				or 0
			)

		# Get available quantities from target warehouse (if set)
		available_qty_target = 0
		if target_warehouse:
			available_qty_target = (
				frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": target_warehouse}, "actual_qty")
				or 0
			)

		# Add to validated items
		validated_items.append(
			{
				"item_code": item_code,
				"item_name": item_name,
				"requested_qty": requested_qty,
				"uom": uom,
				"available_qty": available_qty,
				"available_qty_target": available_qty_target,
				"requester_notes": requester_notes,
			}
		)

	# Return response
	if errors:
		return {"success": False, "errors": errors, "validated_items": []}
	else:
		return {"success": True, "errors": [], "validated_items": validated_items}
