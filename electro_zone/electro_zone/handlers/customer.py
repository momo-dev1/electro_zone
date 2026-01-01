"""
Customer event handlers for electro_zone app
"""

import frappe


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
