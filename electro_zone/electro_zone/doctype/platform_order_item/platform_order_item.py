# Copyright (c) 2026, Electro Zone and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class PlatformOrderItem(Document):
    def validate(self):
        """Calculate commission_value and order_value for each item"""
        self.calculate_commission_value()
        self.calculate_order_value()

    def calculate_commission_value(self):
        """Calculate commission_value from commission_percent and unit_price"""
        if self.commission_percent and self.unit_price:
            self.commission_value = (self.commission_percent / 100) * self.unit_price
        else:
            self.commission_value = 0

    def calculate_order_value(self):
        """
        Calculate order_value per item
        Formula: unit_price + shipping_collection - commission_value + cod_collection
                 - cod_fees - shipping_fees + subsidy + adjustment
        """
        self.order_value = (
            (self.unit_price or 0)
            + (self.shipping_collection or 0)
            - (self.commission_value or 0)
            + (self.cod_collection or 0)
            - (self.cod_fees or 0)
            - (self.shipping_fees or 0)
            + (self.subsidy or 0)
            + (self.adjustment or 0)
        )
