# Copyright (c) 2025, didy1234567@gmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class MarketplaceListing(Document):
	def before_save(self):
		"""Marketplace Listing - Auto Fill Item Model
		Populate item_model from Item master
		"""
		if self.item_code and not self.get("item_model"):
			item_model = frappe.db.get_value("Item", self.item_code, "custom_item_model")
			if item_model:
				self.item_model = item_model

	def validate(self):
		"""Marketplace Listing - Validate One Row Only
		Ensure exactly one row in listing_details child table
		"""
		if not self.get("listing_details"):
			frappe.throw(
				"At least one Marketplace Listing Detail row is required. "
				"Please add a platform and ASIN.",
				title="Missing Listing Details",
			)

		row_count = len(self.listing_details)

		if row_count > 1:
			frappe.throw(
				f"Only ONE row is allowed in Listing Details (found {row_count} rows). "
				"Each Marketplace Listing must have exactly one platform + one ASIN. "
				"To add multiple platforms, create separate Marketplace Listings.",
				title="Multiple Rows Not Allowed",
			)

		listing_row = self.listing_details[0]

		if not listing_row.get("platform"):
			frappe.throw("Platform is required in Listing Details")

		if not listing_row.get("asin"):
			frappe.throw("ASIN / SKU is required in Listing Details")

		# Validate that this (platform, asin) combination doesn't already exist
		# in another Marketplace Listing
		self.validate_unique_platform_asin_combination(listing_row)

	def validate_unique_platform_asin_combination(self, listing_row):
		"""
		Ensure that this (platform, asin) combination doesn't already exist
		in another Marketplace Listing (approved).

		This prevents multiple items from being assigned the same ASIN.
		"""
		platform = listing_row.get("platform")
		asin = listing_row.get("asin")

		if not platform or not asin:
			return  # Already validated above

		# Check if this (platform, asin) exists in another Marketplace Listing
		existing = frappe.db.sql(
			"""
			SELECT mpl.name, mpl.item_code
			FROM `tabMarketplace Listing` mpl
			INNER JOIN `tabMarketplace Listing Detail` mpld
				ON mpld.parent = mpl.name
			WHERE mpld.platform = %(platform)s
				AND mpld.asin = %(asin)s
				AND mpl.name != %(current_name)s
				AND mpl.docstatus = 1
			LIMIT 1
		""",
			{"platform": platform, "asin": asin, "current_name": self.name or ""},
			as_dict=True,
		)

		if existing:
			frappe.throw(
				f"ASIN '{asin}' for platform '{platform}' is already "
				f"used in Marketplace Listing {existing[0].name} (Item: {existing[0].item_code}). "
				f"Each ASIN can only be linked to one item.",
				title="Duplicate ASIN",
			)


@frappe.whitelist()
def get_latest_marketplace_listings(item_code):
	"""API: Get Latest Marketplace Listings for Item

	Returns latest listing per unique Platform+ASIN combination.
	Uses ROW_NUMBER() to partition by Platform+ASIN and get most recent by date.

	Args:
		item_code (str): The item code to fetch listings for

	Returns:
		dict: Response with success status, listings array, and count
	"""
	if not item_code:
		frappe.response["message"] = {"success": False, "error": "Item Code is required"}
	else:
		# Get latest listing per Platform+ASIN combination
		# Uses ROW_NUMBER() to partition by Platform+ASIN and get most recent by date
		listings = frappe.db.sql(
			"""
			SELECT * FROM (
				SELECT
					mpld.platform,
					mpld.asin,
					mpld.commission,
					mpld.shipping_fee,
					mpld.listing_url,
					mpld.status,
					mpl.effective_date,
					mpl.name as listing_name,
					ROW_NUMBER() OVER (
						PARTITION BY mpld.platform, mpld.asin
						ORDER BY mpl.effective_date DESC, mpl.creation DESC
					) as rn
				FROM `tabMarketplace Listing` mpl
				INNER JOIN `tabMarketplace Listing Detail` mpld
					ON mpld.parent = mpl.name
				WHERE mpl.item_code = %(item_code)s
				  AND mpl.docstatus = 1
			) AS ranked_listings
			WHERE rn = 1
			ORDER BY platform, asin
		""",
			{"item_code": item_code},
			as_dict=1,
		)

		# Remove rn field from results
		for listing in listings:
			listing.pop("rn", None)

		frappe.response["message"] = {"success": True, "listings": listings, "count": len(listings)}
