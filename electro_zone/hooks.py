app_name = "electro_zone"
app_title = "Electro Zone"
app_publisher = "didy1234567@gmail.com"
app_description = "Electro Zone"
app_email = "didy1234567@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "electro_zone",
# 		"logo": "/assets/electro_zone/logo.png",
# 		"title": "Electro Zone",
# 		"route": "/electro_zone",
# 		"has_permission": "electro_zone.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/electro_zone/css/electro_zone.css"
# app_include_js = "/assets/electro_zone/js/electro_zone.js"

# include js, css files in header of web template
# web_include_css = "/assets/electro_zone/css/electro_zone.css"
# web_include_js = "/assets/electro_zone/js/electro_zone.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "electro_zone/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}

doctype_js = {
	"Item": "public/js/item.js",
	"Purchase Receipt": "public/js/purchase_receipt.js",
	"Purchase Order": "public/js/purchase_order.js",
	"Sales Order": "public/js/sales_order.js",
	"Delivery Note": "public/js/delivery_note.js",
	"Customer": "public/js/customer.js"
}

doctype_list_js = {
	"Item": "public/js/item_list.js"
}

# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "electro_zone/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "electro_zone.utils.jinja_methods",
# 	"filters": "electro_zone.utils.jinja_filters"
# }

# Installation
# ------------

# before_install = "electro_zone.install.before_install"
# after_install = "electro_zone.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "electro_zone.uninstall.before_uninstall"
# after_uninstall = "electro_zone.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "electro_zone.utils.before_app_install"
# after_app_install = "electro_zone.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "electro_zone.utils.before_app_uninstall"
# after_app_uninstall = "electro_zone.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "electro_zone.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

# doc_events = {
# 	"*": {
# 		"on_update": "method",
# 		"on_cancel": "method",
# 		"on_trash": "method"
# 	}
# }

doc_events = {
	"Item": {
		"validate": "electro_zone.electro_zone.handlers.item.validate_uniqueness",
		"before_update_after_submit": "electro_zone.electro_zone.handlers.item.auto_assign_supplier_from_brand",
	},
	"Purchase Order": {
		"validate": [
			"electro_zone.electro_zone.handlers.purchase_order.validate_supplier_items",
			"electro_zone.electro_zone.handlers.purchase_order.auto_sync_standard_buying_on_item_add",
			"electro_zone.electro_zone.handlers.purchase_order.sync_price_edit_status",
		]
	},
	"Sales Order": {
		"before_insert": "electro_zone.electro_zone.handlers.sales_order.recalculate_amount",
		"validate": "electro_zone.electro_zone.handlers.sales_order.validate_discount",
		"before_update_after_submit": "electro_zone.electro_zone.handlers.sales_order.force_closed_if_returned",
		"before_cancel": "electro_zone.electro_zone.handlers.sales_order.validate_cancellation",
		"on_submit": [
			"electro_zone.electro_zone.handlers.sales_order.move_to_hold",
			"electro_zone.electro_zone.handlers.sales_order.deduct_balance"
		],
		"on_cancel": "electro_zone.electro_zone.handlers.sales_order.cancel_and_return_stock"
	},
	"Delivery Note": {
		"before_submit": "electro_zone.electro_zone.handlers.delivery_note.validate_sales_order_reference",
		"before_cancel": "electro_zone.electro_zone.handlers.delivery_note.block_cancel_if_delivered",
		"on_submit": [
			"electro_zone.electro_zone.handlers.delivery_note.update_item_stock_fields",
			"electro_zone.electro_zone.handlers.delivery_note.create_reference_ledger_entry",
			"electro_zone.electro_zone.handlers.delivery_note.auto_invoice_on_out_for_delivery",
			"electro_zone.electro_zone.handlers.delivery_note.auto_return_stock_on_delivery_failed",
		],
		"on_cancel": "electro_zone.electro_zone.handlers.delivery_note.auto_close_so_on_cancel",
	},
	"Purchase Receipt": {
		"validate": [
			"electro_zone.electro_zone.handlers.purchase_receipt.auto_populate_rate",
			"electro_zone.electro_zone.handlers.purchase_receipt.validate_received_quantity",
			"electro_zone.electro_zone.handlers.purchase_receipt.strict_po_validation"
		],
		"on_submit": "electro_zone.electro_zone.handlers.purchase_receipt.update_item_stock_fields"
	},
	"Stock Entry": {
		"on_submit": "electro_zone.electro_zone.handlers.stock_entry.update_item_stock_fields"
	},
	"Payment Entry": {
		"validate": "electro_zone.electro_zone.handlers.payment_entry.auto_allocate_outstanding_invoices_fifo",
		"on_submit": [
			"electro_zone.electro_zone.handlers.payment_entry.balance_topup_and_refund_handler",
			"electro_zone.electro_zone.handlers.payment_entry.update_so_on_payment"
		]
	},
	"Sales Invoice": {
		"before_insert": "electro_zone.electro_zone.handlers.sales_invoice.block_credit_note_if_dn_return_not_received",
		"before_submit": "electro_zone.electro_zone.handlers.sales_invoice.auto_allocate_unallocated_payment_entries",
		"on_submit": [
			"electro_zone.electro_zone.handlers.sales_invoice.auto_allocate_balance",
			"electro_zone.electro_zone.handlers.sales_invoice.update_so_billing_status_only",
		],
	},
	"Customer": {
		"validate": "electro_zone.electro_zone.handlers.customer.validate_phone_uniqueness",
	},
	"Customer Quick Create": {
		"before_submit": "electro_zone.electro_zone.handlers.customer_quick_create.validate_phone_uniqueness",
		"on_submit": "electro_zone.electro_zone.handlers.customer_quick_create.auto_create_records",
	},
	"GL Entry": {
		"on_submit": "electro_zone.electro_zone.handlers.gl_entry.sync_customer_balance_on_gl_submit",
		"on_cancel": "electro_zone.electro_zone.handlers.gl_entry.sync_customer_balance_on_gl_cancel",
	},
}

# Scheduled Tasks
# ---------------

# scheduler_events = {
# 	"all": [
# 		"electro_zone.tasks.all"
# 	],
# 	"daily": [
# 		"electro_zone.tasks.daily"
# 	],
# 	"hourly": [
# 		"electro_zone.tasks.hourly"
# 	],
# 	"weekly": [
# 		"electro_zone.tasks.weekly"
# 	],
# 	"monthly": [
# 		"electro_zone.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "electro_zone.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "electro_zone.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "electro_zone.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# ignore_links_on_delete = ["Communication", "ToDo"]

# Request Events
# ----------------
# before_request = ["electro_zone.utils.before_request"]
# after_request = ["electro_zone.utils.after_request"]

# Job Events
# ----------
# before_job = ["electro_zone.utils.before_job"]
# after_job = ["electro_zone.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"electro_zone.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Fixtures
# --------
# Export fixtures for this app

fixtures = [
	{"dt": "Custom Field", "filters": [["name", "in", ["Item-platform_asin_sku"]]]},
	"Client Script",
]

