# Copyright (c) 2025, didy1234567@gmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class ItemPriceListHistory(Document):
	def before_save(self):
		"""Item Price List History - Calculate Final Prices
		Calculate Stock User and Accountant final prices
		"""
		# Calculate Stock User Final Price
		if self.stock_price_list:
			price_list = self.stock_price_list or 0
			promo = self.stock_promo or 0
			sellout = self.stock_sellout_promo or 0

			stock_final = price_list - promo - sellout

			if stock_final < 0:
				frappe.throw(
					f"Stock User: Invalid pricing - Final price cannot be negative.<br>"
					f"Price List: {price_list}, Promo: {promo}, Sellout: {sellout}"
				)

			self.stock_final_price_list = stock_final

		# Calculate Accountant Final Price
		if self.account_price_list:
			price_list = self.account_price_list or 0
			promo = self.account_promo or 0
			sellout = self.account_sellout_promo or 0

			account_final = price_list - promo - sellout

			if account_final < 0:
				frappe.throw(
					f"Accountant: Invalid pricing - Final price cannot be negative.<br>"
					f"Price List: {price_list}, Promo: {promo}, Sellout: {sellout}"
				)

			self.account_final_price_list = account_final

	def before_submit(self):
		"""Item Price List History - Validate Final Submit
		This script validates manual Final Submit OR allows auto-submit from API
		"""
		# Check if this is an auto-submit from API (has ignore_permissions flag)
		if self.flags.get("ignore_permissions"):
			# Auto-submit from API - already validated in the API
			# Just update status if not already set
			if self.comparison_status != "Final Submitted":
				self.comparison_status = "Final Submitted"
			# Skip further validation - API already checked everything
		else:
			# Manual Final Submit by user - validate everything
			# Get current user and roles
			current_user = frappe.session.user
			user_roles = frappe.db.get_all(
				"Has Role", filters={"parent": current_user}, fields=["role"], pluck="role"
			)

			is_accountant = "Accounts User" in user_roles or "Accounts Manager" in user_roles

			# Only Accountant can submit manually
			if not is_accountant:
				frappe.throw("Only Accountants can perform Final Submit")

			# Both must have submitted
			if not self.stock_submitted:
				frappe.throw("Stock User must submit before Final Submit")

			if not self.accountant_submitted:
				frappe.throw("Accountant must submit before Final Submit")

			# All fields must match
			if self.match_status != "Matched":
				frappe.throw("Cannot submit: Data mismatch detected. Use 'Refuse Submit' to correct.")

			# Update status
			self.comparison_status = "Final Submitted"

	def on_submit(self):
		"""Item Price List History - Update Item Price and Send Notifications
		1. Update Item Price with latest price from this submission
		2. Send in-app bell notifications to relevant roles
		"""
		# ===== PART 1: UPDATE ITEM PRICE =====

		# Get all submitted records for this item_code
		all_records = frappe.db.get_all(
			"Item Price List History",
			filters={"item_code": self.item_code, "docstatus": 1},  # Only submitted records
			fields=["name", "date", "creation"],
			order_by="date desc, creation desc",  # Latest date first, then latest creation
		)

		if not all_records:
			frappe.log_error("No submitted records found", f"Item: {self.item_code}")
		else:
			# Get the latest record
			latest_record = all_records[0]

			# Check if current document is the latest
			if latest_record.get("name") != self.name:
				# This is not the latest record, skip update
				self.add_comment(
					"Info", f"Price NOT updated - A newer record exists with date: {latest_record.get('date')}"
				)
				frappe.msgprint(
					"This record is submitted but NOT the latest. Item Price will NOT be updated.",
					indicator="orange",
					alert=True,
				)
			else:
				# This is the latest record - update Item Price
				item_code = self.item_code

				# Fetch Item's sellout_included checkbox to determine calculation
				item_doc = frappe.get_doc("Item", item_code)
				sellout_included = item_doc.custom_sellout_included or 0

				# Calculate final price based on sellout_included checkbox
				if sellout_included:
					# Checked: Include sellout promo in calculation
					final_price = self.stock_price_list - self.stock_promo - self.stock_sellout_promo
					calculation_note = f"Calculation: {self.stock_price_list} - {self.stock_promo} - {self.stock_sellout_promo} = {final_price} (Sellout INCLUDED)"
				else:
					# Unchecked (default): Exclude sellout promo
					final_price = self.stock_price_list - self.stock_promo
					calculation_note = f"Calculation: {self.stock_price_list} - {self.stock_promo} = {final_price} (Sellout EXCLUDED)"

				# Check if Item Price exists
				existing_price = frappe.db.exists(
					"Item Price", {"item_code": item_code, "price_list": "Standard Buying"}
				)

				if existing_price:
					# Update existing
					frappe.db.set_value(
						"Item Price", existing_price, {"price_list_rate": final_price, "valid_from": self.date}
					)
					self.add_comment("Info", f"Item Price UPDATED to {final_price}. {calculation_note}")
				else:
					# Create new Item Price
					item_price = frappe.new_doc("Item Price")
					item_price.item_code = item_code
					item_price.price_list = "Standard Buying"
					item_price.price_list_rate = final_price
					item_price.currency = (
						frappe.db.get_single_value("Global Defaults", "default_currency") or "EGP"
					)
					item_price.valid_from = self.date
					item_price.flags.ignore_permissions = True
					item_price.insert()
					self.add_comment("Info", f"Item Price CREATED with {final_price}. {calculation_note}")

				# Update Item custom fields (if they exist)
				try:
					frappe.db.set_value(
						"Item",
						item_code,
						{
							"custom_current_final_price_list": self.stock_price_list,
							"custom_current_final_price_list_calculated": final_price,
							"custom_current_final_promo": self.stock_promo,
							"custom_current_final_sellout_promo": self.stock_sellout_promo,
							"custom_price_last_updated": frappe.utils.now(),
						},
					)
				except:
					pass  # Custom fields may not exist

				# ===== PART 1.5: AUTO-RECALCULATE LATEST REBATE LIST =====

				try:
					# Get ONLY the latest submitted Rebate List record for this item
					rebate_records = frappe.db.get_all(
						"Rebate List",
						filters={"item_code": item_code, "docstatus": 1},  # Only submitted records
						fields=[
							"name",
							"date",
							"creation",
							"cash_discount",
							"invoice_discount",
							"method",
							"quarter_discount",
							"yearly_discount",
						],
						order_by="date desc, creation desc",  # Latest first
						limit=1,  # ONLY get the latest record
					)

					if rebate_records:
						# Get the latest (and only) record
						latest_record = rebate_records[0]

						# Calculate new final_rate_price based on method
						cash_discount = latest_record.get("cash_discount") or 0
						invoice_discount = latest_record.get("invoice_discount") or 0
						method = latest_record.get("method")

						if method == "Gross":
							# Gross Method: Final Price - (Final Price × (Cash% + Invoice%))
							total_discount_percent = (cash_discount + invoice_discount) / 100
							new_final_rate_price = final_price - (final_price * total_discount_percent)
						elif method == "Net":
							# Net Method: Apply discounts sequentially
							price_after_cash = final_price - (final_price * (cash_discount / 100))
							new_final_rate_price = price_after_cash - (
								price_after_cash * (invoice_discount / 100)
							)
						else:
							# Unknown method - skip update
							new_final_rate_price = None

						if new_final_rate_price is not None:
							# Round to 2 decimal places
							new_final_rate_price = round(new_final_rate_price, 2)

							# Update ONLY the latest record (bypass permissions for submitted docs)
							frappe.db.set_value(
								"Rebate List",
								latest_record.get("name"),
								{"final_price_list": final_price, "final_rate_price": new_final_rate_price},
								update_modified=False,
							)

							# Update Item's Repeat tab with latest Rebate List values
							frappe.db.set_value(
								"Item",
								item_code,
								{
									"custom_repeat_final_rate_price": new_final_rate_price,
									"custom_repeat_cash_discount": cash_discount,
									"custom_repeat_invoice_discount": invoice_discount,
									"custom_repeat_quarter_discount": latest_record.get("quarter_discount") or 0,
									"custom_repeat_yearly_dis": latest_record.get("yearly_discount") or 0,
									"custom_repeat_method": method,
									"custom_repeat_last_updated": frappe.utils.now(),
								},
							)

							# Update Standard Buying Item Price with new final_rate_price
							existing_price = frappe.db.exists(
								"Item Price", {"item_code": item_code, "price_list": "Standard Buying"}
							)

							if existing_price:
								# Update existing Item Price
								frappe.db.set_value(
									"Item Price",
									existing_price,
									{"price_list_rate": new_final_rate_price, "valid_from": self.date},
								)
							else:
								# Create new Item Price record
								item_price = frappe.new_doc("Item Price")
								item_price.item_code = item_code
								item_price.price_list = "Standard Buying"
								item_price.price_list_rate = new_final_rate_price
								currency = (
									frappe.db.get_single_value("Global Defaults", "default_currency") or "EGP"
								)
								item_price.currency = currency
								item_price.valid_from = self.date
								item_price.flags.ignore_permissions = True
								item_price.insert()

							# Add audit trail comment
							self.add_comment(
								"Info",
								f"Rebate List: Updated latest record ({latest_record.get('name')}) with new Final Price List: {final_price}. Standard Buying Item Price updated to {new_final_rate_price}",
							)

							frappe.msgprint(
								f"✅ Updated Rebate List, Item Repeat tab, and Standard Buying price (Final Rate: {new_final_rate_price})",
								indicator="green",
								alert=True,
							)

				except Exception as e:
					# Log error but don't fail the submission
					frappe.log_error(
						f"Rebate List recalculation failed for {item_code}: {str(e)}",
						"Price History - Rebate Recalculation Error",
					)

		# ===== PART 2: SEND IN-APP NOTIFICATIONS =====

		# Get item details
		item_code = self.item_code
		item_name = frappe.db.get_value("Item", item_code, "item_name") or item_code
		final_price = self.stock_final_price_list
		submission_date = self.date

		# Get submitted by user details
		submitted_by_user = self.owner
		submitted_by_name = frappe.db.get_value("User", submitted_by_user, "full_name") or submitted_by_user

		# Get all users with relevant roles
		roles_to_notify = ["Stock User", "Accounts User", "Accounts Manager"]

		# Get unique users with these roles (avoid duplicates)
		users_to_notify = set()

		for role in roles_to_notify:
			role_users = frappe.db.get_all(
				"Has Role", filters={"role": role, "parenttype": "User"}, fields=["parent"], pluck="parent"
			)
			users_to_notify.update(role_users)

		# Filter out disabled users and Administrator
		enabled_users = []
		for user in users_to_notify:
			user_enabled = frappe.db.get_value("User", user, "enabled")
			if user_enabled and user not in ["Administrator", "Guest"]:
				enabled_users.append(user)

		# Create notification for each user
		notification_subject = f"Price Updated: {item_name}"

		notification_message = f"""<b>New Price Update Submitted</b><br><br>
<b>Item:</b> {item_name} ({item_code})<br>
<b>New Price:</b> {final_price}<br>
<b>Effective Date:</b> {submission_date}<br>
<b>Submitted By:</b> {submitted_by_name}<br><br>
<a href="/app/item-price-list-history/{self.name}">Click to view Price History Record</a>"""

		# Send notification to each user
		for user in enabled_users:
			# Create notification log record
			notification = frappe.new_doc("Notification Log")
			notification.for_user = user
			notification.type = "Alert"
			notification.document_type = "Item Price List History"
			notification.document_name = self.name
			notification.subject = notification_subject
			notification.email_content = notification_message
			notification.from_user = submitted_by_user
			notification.insert(ignore_permissions=True)

		# Add comment to document for audit trail
		if enabled_users:
			self.add_comment(
				"Info",
				f"Notifications sent to {len(enabled_users)} users: {', '.join(enabled_users[:5])}{'...' if len(enabled_users) > 5 else ''}",
			)


@frappe.whitelist()
def manager_revalidate_price_history(name):
	"""Manager Revalidate & Submit API

	Allows Accounts Manager to revalidate and correct mismatched price history records.

	Args:
		name (str): Document name of the Item Price List History record

	Returns:
		dict: Response with success status, message, and match status
	"""
	if not name:
		frappe.throw("Document name is required")

	# Get current user and roles
	current_user = frappe.session.user
	user_roles = frappe.db.get_all(
		"Has Role", filters={"parent": current_user}, fields=["role"], pluck="role"
	)

	# Only Accounts Manager can revalidate
	is_accountant_manager = "Accounts Manager" in user_roles

	if not is_accountant_manager:
		frappe.throw("Only Accounts Manager can revalidate submissions")

	# Get document - bypass permissions since this is a controlled API
	doc = frappe.get_doc("Item Price List History", name)

	# Check document is in Draft
	if doc.docstatus != 0:
		frappe.throw("Can only revalidate Draft documents")

	# Check if in manager correction mode
	if not doc.needs_manager_correction:
		frappe.throw("Document is not in manager correction mode")

	# Validate that Stock and Accountant have submitted
	if not doc.stock_submitted or not doc.accountant_submitted:
		frappe.throw("Both Stock and Accountant must have submitted")

	# Save document first to capture any manager edits - bypass permissions
	doc.flags.ignore_permissions = True
	doc.save()

	# ===== RUN COMPARISON INLINE =====
	# Compare each field
	match_item_group = doc.stock_item_group == doc.account_item_group
	match_brand = doc.stock_brand == doc.account_brand
	match_price_list = doc.stock_price_list == doc.account_price_list
	match_promo = doc.stock_promo == doc.account_promo
	match_sellout = doc.stock_sellout_promo == doc.account_sellout_promo
	match_rrp = doc.stock_rrp == doc.account_rrp
	match_final = doc.stock_final_price_list == doc.account_final_price_list

	# Update match indicators
	match_item_group_text = "✅ Match" if match_item_group else "❌ Mismatch"
	match_brand_text = "✅ Match" if match_brand else "❌ Mismatch"
	match_price_list_text = "✅ Match" if match_price_list else "❌ Mismatch"
	match_promo_text = "✅ Match" if match_promo else "❌ Mismatch"
	match_sellout_text = "✅ Match" if match_sellout else "❌ Mismatch"
	match_rrp_text = "✅ Match" if match_rrp else "❌ Mismatch"
	match_final_text = "✅ Match" if match_final else "❌ Mismatch"

	# Check if all match
	all_match = all(
		[match_item_group, match_brand, match_price_list, match_promo, match_sellout, match_rrp, match_final]
	)

	# Update status based on match result
	if all_match:
		comparison_status = "Matched (Pending Final)"
		match_status = "Matched"
		needs_correction = 0
		message = "All fields matched! You can now proceed with Final Submit."

		# Record manager correction details
		manager_correction_by = current_user
		manager_correction_date = frappe.utils.now()
	else:
		# Still mismatched - stay in correction mode
		comparison_status = "Pending Account Manager Correction"
		match_status = "Not Matched"
		needs_correction = 1
		message = "Mismatches still detected. Please continue correcting the Accountant fields."

		manager_correction_by = None
		manager_correction_date = None

	# Update database - bypass permissions
	frappe.db.set_value(
		"Item Price List History",
		doc.name,
		{
			"match_item_group": match_item_group_text,
			"match_brand": match_brand_text,
			"match_price_list": match_price_list_text,
			"match_promo": match_promo_text,
			"match_sellout_promo": match_sellout_text,
			"match_rrp": match_rrp_text,
			"match_final_price_list": match_final_text,
			"comparison_status": comparison_status,
			"match_status": match_status,
			"needs_manager_correction": needs_correction,
			"manager_correction_by": manager_correction_by,
			"manager_correction_date": manager_correction_date,
		},
		update_modified=False,
	)

	# Reload to get updated values
	doc.reload()

	# Add comment
	if all_match:
		doc.add_comment("Info", f"Manager ({current_user}) completed correction. All fields now match.")
	else:
		doc.add_comment("Info", f"Manager ({current_user}) revalidated. Mismatches still present.")

	# Return success with match status
	frappe.response["message"] = {
		"success": True,
		"message": message,
		"status": doc.comparison_status,
		"match_status": doc.match_status,
		"all_match": all_match,
	}


@frappe.whitelist()
def refuse_submit_price_history():
	"""Refuse Submit API

	Allows Accounts Manager to refuse a submission and enter manager correction mode.

	Returns:
		dict: Response with success status, message, and status
	"""
	# Get document name from request
	doc_name = frappe.form_dict.get("name")

	if not doc_name:
		frappe.throw("Document name is required")

	# Get document - bypass permissions since this is a controlled API
	doc = frappe.get_doc("Item Price List History", doc_name)

	# Check document is in Draft
	if doc.docstatus != 0:
		frappe.throw("Can only refuse Draft documents")

	# Get current user and roles
	current_user = frappe.session.user
	user_roles = frappe.db.get_all(
		"Has Role", filters={"parent": current_user}, fields=["role"], pluck="role"
	)

	# Only Accounts Manager can refuse (not Accounts User)
	is_accountant_manager = "Accounts Manager" in user_roles

	if not is_accountant_manager:
		frappe.throw("Only Accounts Manager can refuse submissions")

	# Check if both have submitted
	if not doc.stock_submitted or not doc.accountant_submitted:
		frappe.throw("Both Stock and Accountant must submit before refusal")

	# Check if already matched
	if doc.match_status == "Matched":
		frappe.throw("Cannot refuse when data is matched. Use Final Submit instead.")

	# ===== NEW v6.0: Set Manager Correction Mode =====
	# Stock User and Accountant fields STAY LOCKED
	# ONLY Account Manager can edit Accountant fields to fix mismatch

	# Keep soft submit flags LOCKED (roles are done)
	# doc.stock_submitted = 1 (stays locked)
	# doc.accountant_submitted = 1 (stays locked)

	# Set manager correction flag
	doc.needs_manager_correction = 1
	doc.comparison_status = "Pending Account Manager Correction"

	# DO NOT clear match indicators - manager needs to see what's mismatched
	# Keep comparison results visible so manager knows what to fix

	# Save document - bypass permissions since this is a controlled API
	doc.flags.ignore_permissions = True
	doc.save()

	# Add comment
	doc.add_comment(
		"Info",
		f"Accounts Manager ({current_user}) initiated correction mode. Stock and Accountant fields remain locked. Only Account Manager can edit Accountant fields to fix mismatch.",
	)

	# Return success
	frappe.response["message"] = {
		"success": True,
		"message": "Manager correction mode activated. Only Account Manager can edit Accountant fields to fix the mismatch.",
		"status": doc.comparison_status,
	}


@frappe.whitelist()
def stock_submit_price_history():
	"""Stock Submit API

	Allows Stock Users to submit their price data.

	Returns:
		dict: Response with success status, message, match status, and auto-submit flag
	"""
	# Get document name from request
	doc_name = frappe.form_dict.get("name")

	if not doc_name:
		frappe.throw("Document name is required")

	# Get current user and roles
	current_user = frappe.session.user
	user_roles = frappe.db.get_all(
		"Has Role", filters={"parent": current_user}, fields=["role"], pluck="role"
	)

	is_stock_user = "Stock User" in user_roles

	if not is_stock_user:
		frappe.throw("Only Stock Users can perform Stock Submit")

	# Get document - bypass permissions since this is a controlled API
	doc = frappe.get_doc("Item Price List History", doc_name)

	# Check document is in Draft
	if doc.docstatus != 0:
		frappe.throw("Can only submit Draft documents")

	# Check if already submitted
	if doc.stock_submitted:
		frappe.throw("Stock User entry already submitted")

	# Validate required fields
	if not doc.item_code or not doc.date or not doc.stock_price_list:
		frappe.throw("Stock User: Item Code, Date, and Price List are required")

	# Validate auto-fetched fields
	if not doc.stock_item_group or not doc.stock_brand:
		frappe.throw("Stock User: Item Group and Brand should be auto-fetched from Item. Please refresh the form.")

	# Mark as submitted (soft submit)
	doc.stock_submitted = 1
	doc.stock_submitted_by = current_user
	doc.stock_submission_date = frappe.utils.now()
	doc.comparison_status = "Stock Submitted"

	# Save document - bypass permissions
	doc.flags.ignore_permissions = True
	doc.save()

	# Add comment
	doc.add_comment("Info", f"Stock User ({current_user}) completed soft submit")

	# ===== NEW v6.3: AUTO-SUBMIT IF ACCOUNTANT ALREADY SUBMITTED =====
	# Check if Accountant has already submitted (Stock User is submitting SECOND)
	if doc.accountant_submitted:
		# Run comparison inline
		match_item_group = doc.stock_item_group == doc.account_item_group
		match_brand = doc.stock_brand == doc.account_brand
		match_price_list = doc.stock_price_list == doc.account_price_list
		match_promo = doc.stock_promo == doc.account_promo
		match_sellout = doc.stock_sellout_promo == doc.account_sellout_promo
		match_rrp = doc.stock_rrp == doc.account_rrp
		match_final = doc.stock_final_price_list == doc.account_final_price_list

		# Update match indicators
		match_item_group_text = "✅ Match" if match_item_group else "❌ Mismatch"
		match_brand_text = "✅ Match" if match_brand else "❌ Mismatch"
		match_price_list_text = "✅ Match" if match_price_list else "❌ Mismatch"
		match_promo_text = "✅ Match" if match_promo else "❌ Mismatch"
		match_sellout_text = "✅ Match" if match_sellout else "❌ Mismatch"
		match_rrp_text = "✅ Match" if match_rrp else "❌ Mismatch"
		match_final_text = "✅ Match" if match_final else "❌ Mismatch"

		# Check if all match
		all_match = all(
			[match_item_group, match_brand, match_price_list, match_promo, match_sellout, match_rrp, match_final]
		)

		# Update comparison fields
		frappe.db.set_value(
			"Item Price List History",
			doc.name,
			{
				"match_item_group": match_item_group_text,
				"match_brand": match_brand_text,
				"match_price_list": match_price_list_text,
				"match_promo": match_promo_text,
				"match_sellout_promo": match_sellout_text,
				"match_rrp": match_rrp_text,
				"match_final_price_list": match_final_text,
				"match_status": "Matched" if all_match else "Not Matched",
			},
			update_modified=False,
		)

		# Reload document to get updated values
		doc.reload()

		if all_match:
			# AUTO-SUBMIT: All fields match!
			doc.comparison_status = "Final Submitted"
			doc.add_comment(
				"Info", f"Auto-submitted by system: All fields matched when Stock User ({current_user}) submitted second"
			)

			# Submit the document (triggers Before Submit and After Submit events)
			doc.flags.ignore_permissions = True
			doc.submit()

			# Return success with auto-submit message
			frappe.response["message"] = {
				"success": True,
				"auto_submitted": True,
				"message": "Stock data submitted and ALL FIELDS MATCHED! Document automatically submitted permanently.",
				"status": "Final Submitted",
				"doc_name": doc.name,
			}
		else:
			# Mismatch detected - set status for manager correction
			doc.comparison_status = "Unmatched (Needs Correction)"
			doc.flags.ignore_permissions = True
			doc.save()

			doc.add_comment(
				"Info",
				f"Comparison completed: Mismatches detected when Stock User ({current_user}) submitted second",
			)

			# Return success with mismatch warning
			frappe.response["message"] = {
				"success": True,
				"auto_submitted": False,
				"message": "Stock data submitted but MISMATCHES DETECTED. Accounts Manager must use 'Refuse Submit' to correct.",
				"status": doc.comparison_status,
				"match_status": "Not Matched",
			}
	else:
		# Accountant has NOT submitted yet - Stock User is submitting FIRST
		# Just return success, no comparison yet
		frappe.response["message"] = {
			"success": True,
			"auto_submitted": False,
			"message": "Stock data submitted successfully. Fields are now locked. Waiting for Accountant submission.",
			"status": doc.comparison_status,
			"doc_name": doc.name,
		}


@frappe.whitelist()
def accountant_submit_price_history():
	"""Accountant Submit API

	Allows Accountants to submit their price data.

	Returns:
		dict: Response with success status, message, match status, and auto-submit flag
	"""
	# Get document name from request
	doc_name = frappe.form_dict.get("name")

	if not doc_name:
		frappe.throw("Document name is required")

	# Get current user and roles
	current_user = frappe.session.user
	user_roles = frappe.db.get_all(
		"Has Role", filters={"parent": current_user}, fields=["role"], pluck="role"
	)

	is_accountant = "Accounts User" in user_roles or "Accounts Manager" in user_roles

	if not is_accountant:
		frappe.throw("Only Accountants can perform Accountant Submit")

	# Get document - bypass permissions since this is a controlled API
	doc = frappe.get_doc("Item Price List History", doc_name)

	# Check document is in Draft
	if doc.docstatus != 0:
		frappe.throw("Can only submit Draft documents")

	# Check if already submitted
	if doc.accountant_submitted:
		frappe.throw("Accountant entry already submitted")

	# Validate required fields
	if not doc.account_price_list:
		frappe.throw("Accountant: Price List is required")

	# Validate auto-fetched fields
	if not doc.account_item_group or not doc.account_brand:
		frappe.throw(
			"Accountant: Item Group and Brand should be auto-fetched from Item. Please refresh the form."
		)

	# Mark as submitted (soft submit)
	doc.accountant_submitted = 1
	doc.accountant_submitted_by = current_user
	doc.accountant_submission_date = frappe.utils.now()

	# Save document first - bypass permissions
	doc.flags.ignore_permissions = True
	doc.save()

	# Add comment
	doc.add_comment("Info", f"Accountant ({current_user}) completed soft submit")

	# ===== v6.3: RUN COMPARISON IF STOCK USER ALREADY SUBMITTED =====
	if doc.stock_submitted:
		# Run comparison inline (RestrictedPython compatible)
		match_item_group = doc.stock_item_group == doc.account_item_group
		match_brand = doc.stock_brand == doc.account_brand
		match_price_list = doc.stock_price_list == doc.account_price_list
		match_promo = doc.stock_promo == doc.account_promo
		match_sellout = doc.stock_sellout_promo == doc.account_sellout_promo
		match_rrp = doc.stock_rrp == doc.account_rrp
		match_final = doc.stock_final_price_list == doc.account_final_price_list

		# Update match indicators
		match_item_group_text = "✅ Match" if match_item_group else "❌ Mismatch"
		match_brand_text = "✅ Match" if match_brand else "❌ Mismatch"
		match_price_list_text = "✅ Match" if match_price_list else "❌ Mismatch"
		match_promo_text = "✅ Match" if match_promo else "❌ Mismatch"
		match_sellout_text = "✅ Match" if match_sellout else "❌ Mismatch"
		match_rrp_text = "✅ Match" if match_rrp else "❌ Mismatch"
		match_final_text = "✅ Match" if match_final else "❌ Mismatch"

		# Check if all match
		all_match = all(
			[match_item_group, match_brand, match_price_list, match_promo, match_sellout, match_rrp, match_final]
		)

		# Update database - bypass permissions
		frappe.db.set_value(
			"Item Price List History",
			doc.name,
			{
				"match_item_group": match_item_group_text,
				"match_brand": match_brand_text,
				"match_price_list": match_price_list_text,
				"match_promo": match_promo_text,
				"match_sellout_promo": match_sellout_text,
				"match_rrp": match_rrp_text,
				"match_final_price_list": match_final_text,
				"match_status": "Matched" if all_match else "Not Matched",
			},
			update_modified=False,
		)

		# Reload to get updated values
		doc.reload()

		if all_match:
			# ===== NEW v6.3: AUTO-SUBMIT IF ALL FIELDS MATCH =====
			doc.comparison_status = "Final Submitted"
			doc.add_comment(
				"Info",
				f"Auto-submitted by system: All fields matched when Accountant ({current_user}) submitted second",
			)

			# Submit the document (triggers Before Submit and After Submit events)
			doc.flags.ignore_permissions = True
			doc.submit()

			# Return success with auto-submit message
			frappe.response["message"] = {
				"success": True,
				"auto_submitted": True,
				"message": "Accountant data submitted and ALL FIELDS MATCHED! Document automatically submitted permanently.",
				"status": "Final Submitted",
				"match_status": "Matched",
			}
		else:
			# Mismatch detected - set status for manager correction
			doc.comparison_status = "Unmatched (Needs Correction)"
			doc.flags.ignore_permissions = True
			doc.save()

			doc.add_comment(
				"Info",
				f"Comparison completed: Mismatches detected when Accountant ({current_user}) submitted second",
			)

			# Return success with mismatch warning
			frappe.response["message"] = {
				"success": True,
				"auto_submitted": False,
				"message": "Accountant data submitted but MISMATCHES DETECTED. Accounts Manager must use 'Refuse Submit' to correct.",
				"status": doc.comparison_status,
				"match_status": "Not Matched",
			}
	else:
		# Stock User has NOT submitted yet - Accountant is submitting FIRST
		# Just return success, no comparison yet
		doc.comparison_status = "Accountant Submitted"
		doc.flags.ignore_permissions = True
		doc.save()

		frappe.response["message"] = {
			"success": True,
			"auto_submitted": False,
			"message": "Accountant data submitted successfully. Fields are now locked. Waiting for Stock User submission.",
			"status": doc.comparison_status,
			"doc_name": doc.name,
		}
