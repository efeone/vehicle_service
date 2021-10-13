# Copyright (c) 2021, Aakvatech Limited and contributors
# For license information, please see license.txt

import frappe
from frappe.website.website_generator import WebsiteGenerator
from frappe import _
from frappe.utils import nowdate, nowtime


class ServiceJobCard(WebsiteGenerator):
    def validate(self):
        self.update_tabels()
        self.set_totals()
        self.vaildate_complete()

    def before_submit(self):
        self.create_stock_entry("before_submit")
        self.create_invoice()

    def on_submit(self):
        if self.status != "Completed":
            frappe.throw(_("It is not allowed to submit if it is not completed"))

    def update_tabels(self):
        for temp in self.services:
            if not temp.check_billable:
                temp.is_billable = frappe.get_value(
                    "Service Template", temp.service, "is_billable"
                )
            if not temp.applied:
                temp_doc = frappe.get_doc("Service Template", temp.service)
                if temp_doc.tasks:
                    for task in temp_doc.tasks:
                        row = self.append("tasks", {})
                        row.task_name = task.task_name
                        row.template = temp_doc.name

                if temp_doc.parts:
                    for part in temp_doc.parts:
                        row = self.append("parts", {})
                        row.item = part.item
                        row.qty = part.qty
                        row.rate = get_item_price(
                            part.item,
                            self.get_price_list(temp_doc.price_list),
                            self.company,
                        )
                        row.is_billable = part.is_billable

                temp.applied = 1

    def set_totals(self):
        self.service_charges = 0
        self.spares_cost = 0
        self.total = 0

        if self.services:
            for el in self.services:
                el.rate = get_item_price(
                    el.item, self.get_price_list(el.price_list), self.company
                )
                if el.is_billable:
                    self.service_charges += el.rate
        if self.parts:
            for el in self.parts:
                if el.is_billable:
                    self.spares_cost += el.rate * el.qty
        if self.supplied_patrs:
            for el in self.supplied_patrs:
                if el.is_billable:
                    self.spares_cost += el.rate * el.qty

        self.total = self.service_charges + self.spares_cost

    @frappe.whitelist()
    def create_stock_entry(self, type):
        if self.parts and len(self.parts) > 0:
            workshop = frappe.get_doc("Service Workshop", self.workshop)
            items = []
            for item in self.parts:
                if item.qty > 0:
                    items.append(
                        {
                            "s_warehouse": workshop.parts_warehouse,
                            "t_warehouse": workshop.workshop_warehouse,
                            "item_code": item.item,
                            "qty": item.qty,
                            "uom": frappe.get_value("Item", item.item, "stock_uom"),
                        }
                    )

            if len(items) == 0:
                return

            doc = frappe.get_doc(
                dict(
                    doctype="Stock Entry",
                    posting_date=nowdate(),
                    posting_time=nowtime(),
                    stock_entry_type="Material Transfer",
                    purpose="Material Transfer",
                    company=self.company,
                    from_warehouse=workshop.parts_warehouse,
                    to_warehouse=workshop.workshop_warehouse,
                    items=items,
                ),
            )
            frappe.flags.ignore_account_permission = True
            doc.insert(ignore_permissions=True)
            doc.submit()
            frappe.msgprint(_("Stock Entry Created {0}").format(doc.name), alert=True)

            left_parts = []
            for row in self.parts:
                if row.qty > 0:
                    new_row = self.append("supplied_patrs", {})
                    new_row.item = row.item
                    new_row.qty = row.qty
                    new_row.rate = row.rate
                    new_row.is_billable = row.is_billable
                    new_row.stock_entry = doc.name
                else:
                    left_parts.append(row)
            self.parts = left_parts
            if type == "call":
                self.save()

    def create_invoice(self):
        if self.status != "Completed":
            return
        items = []
        workshop = frappe.get_doc("Service Workshop", self.workshop)
        if self.services and len(self.services) > 0:
            for item in self.services:
                items.append(
                    {
                        "item_code": item.item,
                        "qty": 1,
                        "uom": frappe.get_value("Item", item.item, "stock_uom"),
                        "warehouse": workshop.workshop_warehouse,
                        "rate": item.rate if item.is_billable else 0,
                    }
                )
        if self.supplied_patrs and len(self.supplied_patrs) > 0:
            for item in self.supplied_patrs:
                items.append(
                    {
                        "item_code": item.item,
                        "qty": item.qty,
                        "uom": frappe.get_value("Item", item.item, "stock_uom"),
                        "warehouse": workshop.workshop_warehouse,
                        "rate": item.rate if item.is_billable else 0,
                    }
                )

            if len(items) == 0:
                return
            date = nowdate()
            doc = frappe.get_doc(
                dict(
                    doctype="Sales Invoice",
                    customer=self.customer,
                    posting_date=date,
                    due_date=date,
                    update_stock=1,
                    company=self.company,
                    ignore_pricing_rule=1,
                    set_warehouse=workshop.workshop_warehouse,
                    items=items,
                ),
            )
            frappe.flags.ignore_account_permission = True
            doc.set_taxes()
            doc.set_missing_values(for_validate=True)
            doc.flags.ignore_mandatory = True
            doc.calculate_taxes_and_totals()
            doc.insert(ignore_permissions=True)
            self.invoice = doc.name
            frappe.msgprint(
                _("Saeles Invoice Created {0}").format(doc.name), alert=True
            )

    def vaildate_complete(self):
        if self.status != "Completed":
            return
        completed = True
        for task in self.tasks:
            if not task.completed:
                completed = False
        if not completed:
            frappe.throw(_("The Tasks is not Completed"))

    def get_price_list(self, temp_price_list=None):
        price_list = frappe.get_value("Customer", self.customer, "default_price_list")
        if not price_list and temp_price_list:
            price_list = temp_price_list
        if not price_list:
            price_list = frappe.get_value(
                "Service Settings", "Service Settings", "price_list"
            )
        return price_list or ""


def get_item_price(item_code, price_list, company):
    price = 0
    company_currency = frappe.get_value("Company", company, "default_currency")
    item_prices_data = frappe.get_all(
        "Item Price",
        fields=["item_code", "price_list_rate", "currency"],
        filters={
            "price_list": price_list,
            "item_code": item_code,
            "currency": company_currency,
        },
        order_by="valid_from desc",
    )
    if len(item_prices_data):
        price = item_prices_data[0].price_list_rate
    return price
