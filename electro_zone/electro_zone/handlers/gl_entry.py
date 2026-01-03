"""
GL Entry event handlers for automatic customer balance sync
"""

import frappe
from electro_zone.electro_zone.handlers.customer import sync_balance_from_gl


def sync_customer_balance_on_gl_submit(doc, _method=None):
	"""Auto-sync customer balance when GL Entry is submitted.

	This hook triggers after a GL Entry is created, syncing the customer's
	custom_current_balance field from the General Ledger using get_customer_outstanding().

	Args:
		doc: GL Entry document
		_method: Event method name (unused, required by Frappe hook signature)
	"""
	# Only process Customer party type
	if doc.party_type == "Customer" and doc.party:
		try:
			# Get company from GL Entry
			company = doc.company

			# Sync balance from GL
			sync_balance_from_gl(customer=doc.party, company=company)

			frappe.logger().info(f"Auto-synced balance for customer {doc.party} via GL Entry {doc.name}")

		except Exception as e:
			# Log error but don't block GL Entry submission
			frappe.log_error(
				f"Failed to auto-sync balance for customer {doc.party}: {str(e)}", "GL Balance Sync Error"
			)


def sync_customer_balance_on_gl_cancel(doc, _method=None):
	"""Auto-sync customer balance when GL Entry is cancelled.

	This hook triggers after a GL Entry is cancelled, re-syncing the customer's
	balance to reflect the updated General Ledger state.

	Args:
		doc: GL Entry document
		_method: Event method name (unused, required by Frappe hook signature)
	"""
	# Only process Customer party type
	if doc.party_type == "Customer" and doc.party:
		try:
			# Get company from GL Entry
			company = doc.company

			# Sync balance from GL
			sync_balance_from_gl(customer=doc.party, company=company)

			frappe.logger().info(
				f"Auto-synced balance for customer {doc.party} after GL Entry {doc.name} cancelled"
			)

		except Exception as e:
			# Log error but don't block GL Entry cancellation
			frappe.log_error(
				f"Failed to auto-sync balance after GL cancel for {doc.party}: {str(e)}",
				"GL Balance Sync Error",
			)
