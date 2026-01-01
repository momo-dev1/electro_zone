"""
Item event handlers for electro_zone app
"""

import frappe


def auto_assign_supplier_from_brand(doc, method=None):
	"""Auto-assign default supplier from Brand to Item if not already set.

	Event: Before Save (Submitted Document)

	Args:
		doc: Item document
		method: Event method name (unused, required by Frappe hook signature)
	"""
	if doc.brand and not doc.get("default_supplier"):
		supplier = frappe.db.get_value("Brand", doc.brand, "default_supplier")
		if supplier:
			doc.default_supplier = supplier
			frappe.msgprint(f"Supplier auto-assigned: {supplier}")


def validate_uniqueness(doc, method=None):
	"""Validate uniqueness of (Brand, Item Group, Item Model) combination.

	# Moved from electro_zone.electro_zone.validations (consolidated into handlers)

	Ensures that no two Items share the same combination of Brand, Item Group,
	and custom_item_model fields. This prevents duplicate product entries.

	Event: Before Save

	Args:
		doc: Item document being validated
		method: Event method name (unused, required by Frappe hook signature)

	Raises:
		frappe.ValidationError: If an Item with the same Brand, Item Group,
			and Model combination already exists

	Example:
		If Item "ITEM-001" has Brand="Samsung", Item Group="Smartphones",
		and custom_item_model="Galaxy S21", attempting to create another Item
		with the same combination will raise:
		"Item with Brand 'Samsung', Item Group 'Smartphones', and Model 'Galaxy S21'
		already exists: ITEM-001"
	"""
	# Skip validation if any required field is missing
	if not (doc.brand and doc.item_group and doc.get("custom_item_model")):
		return

	# Build filter for uniqueness check
	filters = {
		"brand": doc.brand,
		"item_group": doc.item_group,
		"custom_item_model": doc.get("custom_item_model"),
	}

	# Exclude current document when updating (not creating new)
	if not doc.is_new():
		filters["name"] = ["!=", doc.name]

	# Check if duplicate exists
	existing = frappe.db.exists("Item", filters)

	if existing:
		frappe.throw(
			f"Item with Brand '{doc.brand}', Item Group '{doc.item_group}', "
			f"and Model '{doc.get('custom_item_model')}' already exists: {existing}"
		)
