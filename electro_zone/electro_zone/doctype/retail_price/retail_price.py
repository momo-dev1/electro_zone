# Copyright (c) 2025, didy1234567@gmail.com and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import get_datetime, now, now_datetime


class RetailPrice(Document):
	def validate(self):
		"""Validate retail price before save"""
		self.validate_retail_price()
		self.validate_item_code()
		self.set_tracking_fields()
		self.validate_submission_date()
		self.validate_minimum_markup()

	def on_submit(self):
		"""Update Standard Selling price after submit"""
		self.update_standard_selling_price()

	def validate_retail_price(self):
		"""Validate that retail price is greater than zero"""
		if not self.retail_price or self.retail_price <= 0:
			frappe.throw(_("Retail Price must be greater than zero"))

	def validate_item_code(self):
		"""Ensure item_code exists"""
		if not self.item_code:
			frappe.throw(_("Item Code is required"))

	def set_tracking_fields(self):
		"""Set tracking fields if not already set"""
		if not self.submitted_by:
			self.submitted_by = frappe.session.user

		if not self.submission_date:
			self.submission_date = now()

	def validate_submission_date(self):
		"""Validate submission_date is not in the future"""
		if get_datetime(self.submission_date) > now_datetime():
			frappe.throw(_("Submission Date cannot be in the future"))

	def validate_minimum_markup(self):
		"""Validate retail price meets minimum 1.5% markup above valuation_rate"""
		# Get Item's valuation_rate from master
		valuation_rate = frappe.db.get_value("Item", self.item_code, "valuation_rate") or 0

		# Calculate minimum required retail price (valuation_rate + 1.5%)
		minimum_retail_price = valuation_rate * 1.015

		# Validate: retail_price must be > minimum_retail_price
		if self.retail_price <= minimum_retail_price:
			# Calculate actual markup percentage
			if valuation_rate > 0:
				actual_markup_pct = ((self.retail_price - valuation_rate) / valuation_rate) * 100
			else:
				actual_markup_pct = 0

			# Throw formatted error message
			frappe.throw(
				f"""
<div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; line-height: 1.6;">
	<p style="font-size: 15px; margin-bottom: 16px;">
		<strong style="color: #d73a49;">Item:</strong>
		<code style="background: #f6f8fa; padding: 2px 6px; border-radius: 3px; font-size: 13px;">{self.item_code}</code>
	</p>

	<div style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 12px; margin-bottom: 16px; border-radius: 4px;">
		<p style="margin: 0; font-weight: 600; color: #856404;">
			⚠️ Retail Price is below minimum required markup
		</p>
	</div>

	<table style="width: 100%; border-collapse: collapse; margin-bottom: 16px; font-size: 14px;">
		<tr style="background: #f8f9fa;">
			<td style="padding: 10px; border: 1px solid #dee2e6; font-weight: 600;">Valuation Rate (Cost)</td>
			<td style="padding: 10px; border: 1px solid #dee2e6; text-align: right; font-family: monospace;">{valuation_rate:,.2f}</td>
		</tr>
		<tr>
			<td style="padding: 10px; border: 1px solid #dee2e6; font-weight: 600;">Your Retail Price</td>
			<td style="padding: 10px; border: 1px solid #dee2e6; text-align: right; font-family: monospace; color: #d73a49; font-weight: 600;">{self.retail_price:,.2f}</td>
		</tr>
		<tr style="background: #d4edda;">
			<td style="padding: 10px; border: 1px solid #c3e6cb; font-weight: 600;">Minimum Required (1.5% markup)</td>
			<td style="padding: 10px; border: 1px solid #c3e6cb; text-align: right; font-family: monospace; font-weight: 600; color: #155724;">{minimum_retail_price:,.2f}</td>
		</tr>
	</table>

	<div style="background: #f8d7da; border-left: 4px solid #dc3545; padding: 12px; margin-bottom: 12px; border-radius: 4px;">
		<p style="margin: 0 0 8px 0; font-weight: 600; color: #721c24;">
			❌ Current Markup: {actual_markup_pct:.2f}%
		</p>
		<p style="margin: 0; font-size: 12px; color: #721c24;">
			Minimum required: 1.50%
		</p>
	</div>

	<div style="background: #d1ecf1; border-left: 4px solid #17a2b8; padding: 12px; border-radius: 4px;">
		<p style="margin: 0 0 8px 0; font-weight: 600; color: #0c5460;">
			💡 Action Required
		</p>
		<p style="margin: 0; font-size: 13px; color: #0c5460;">
			Please increase the retail price to at least <strong>{minimum_retail_price:,.2f}</strong> to maintain minimum 1.5% markup.
		</p>
	</div>
</div>
				""",
				title=_("Retail Price Validation Failed"),
			)

	def update_standard_selling_price(self):
		"""Update Item Price for Standard Selling with 'last date wins' logic"""
		# Step 1: Check if this is the latest record for this item
		all_records = frappe.db.get_all(
			"Retail Price",
			filters={"item_code": self.item_code, "docstatus": 1},
			fields=["name", "submission_date", "creation"],
			order_by="submission_date DESC, creation DESC",
		)

		latest_record = all_records[0] if all_records else None

		# Step 2: Only update Item Price if THIS record is the latest
		if latest_record and latest_record.name == self.name:
			# Step 3: Find or create Item Price for Standard Selling
			existing_price = frappe.db.exists(
				"Item Price", {"item_code": self.item_code, "price_list": "Standard Selling"}
			)

			if existing_price:
				# UPDATE existing Item Price
				frappe.db.set_value(
					"Item Price",
					existing_price,
					{"price_list_rate": self.retail_price, "valid_from": self.submission_date},
				)

				self.add_comment("Info", f"Standard Selling price updated to {self.retail_price} EGP")
				frappe.msgprint(
					f"Item Price updated successfully for {self.item_code}",
					alert=True,
					indicator="green",
				)
			else:
				# CREATE new Item Price record
				default_currency = frappe.db.get_single_value("Global Defaults", "default_currency") or "EGP"

				item_price = frappe.new_doc("Item Price")
				item_price.item_code = self.item_code
				item_price.price_list = "Standard Selling"
				item_price.price_list_rate = self.retail_price
				item_price.currency = default_currency
				item_price.valid_from = self.submission_date
				item_price.flags.ignore_permissions = True
				item_price.insert()

				self.add_comment("Info", f"New Standard Selling price created: {self.retail_price} EGP")
				frappe.msgprint(
					f"Item Price created successfully for {self.item_code}",
					alert=True,
					indicator="green",
				)
		else:
			# NOT the latest - skip Item Price update
			self.add_comment("Info", "Price NOT updated - A newer Retail Price record exists for this item")
			frappe.msgprint(
				f"Warning: A newer Retail Price record exists for {self.item_code}. Item Price was NOT updated.",
				alert=True,
				indicator="orange",
			)
