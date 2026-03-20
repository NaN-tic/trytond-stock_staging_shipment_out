# This file is part of the stock_staging_shipment_out module for Tryton.
# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.i18n import gettext
from trytond.model import ModelSQL, ModelView, Unique, fields
from trytond.model.exceptions import ValidationError
from trytond.pool import Pool, PoolMeta
from trytond.pyson import Eval
from trytond.tools import grouped_slice


class Location(metaclass=PoolMeta):
    __name__ = 'stock.location'

    use_staging_area_for_customer_shipments = fields.Boolean(
        "Use Staging Area for Customer Shipments",
        states={
            'invisible': (
                (Eval('type') != 'warehouse')
                | ~Eval('picking_location')),
            },
        help="Customer shipments will reserve an empty storage location below "
        "the picking location.")
    staging_shipment_out = fields.One2One(
        'stock.shipment.out-staging.location', 'location', 'shipment',
        "Staging Customer Shipment", readonly=True)
    has_stock = fields.Function(
        fields.Boolean("Has Stock"), 'get_has_stock',
        searcher='search_has_stock')
    staging_customer_shipment_location = fields.Function(
        fields.Boolean("Staging Customer Shipment Location"),
        'get_staging_customer_shipment_location',
        searcher='search_staging_customer_shipment_location')

    @classmethod
    def _customer_shipment_staging_location_ids(cls):
        warehouses = cls.search([
                ('type', '=', 'warehouse'),
                ('use_staging_area_for_customer_shipments', '=', True),
                ('picking_location', '!=', None),
                ])
        location_ids = set()
        for warehouse in warehouses:
            picking_location = warehouse.picking_location
            location_ids.add(picking_location.id)
            childs = cls.search([
                    ('type', '=', 'storage'),
                    ('parent', 'child_of', [picking_location.id]),
                    ])
            location_ids.update(location.id for location in childs)
        return list(location_ids)

    @classmethod
    def get_has_stock(cls, locations, name):
        Product = Pool().get('product.product')
        result = {location.id: False for location in locations}
        for sub_locations in grouped_slice(locations):
            quantities = Product.products_by_location(
                [location.id for location in sub_locations],
                with_childs=False)
            for key, quantity in quantities.items():
                if quantity != 0:
                    result[key[0]] = True
        return result

    @classmethod
    def get_staging_customer_shipment_location(cls, locations, name):
        staging_ids = set(cls._customer_shipment_staging_location_ids())
        return {
            location.id: location.id in staging_ids
            for location in locations
            }

    @classmethod
    def search_staging_customer_shipment_location(cls, name, clause):
        _, operator, value = clause
        if operator not in ('=', '!='):
            raise NotImplementedError(
                f"Unsupported operator {operator!r} for {name}")
        location_ids = cls._customer_shipment_staging_location_ids()
        positive = (operator == '=' and bool(value)) or (
            operator == '!=' and not bool(value))
        if positive:
            return [('id', 'in', location_ids or [-1])]
        return [('id', 'not in', location_ids or [-1])]

    @classmethod
    def search_has_stock(cls, name, clause):
        _, operator, value = clause
        if operator not in ('=', '!='):
            raise NotImplementedError(
                f"Unsupported operator {operator!r} for {name}")
        Product = Pool().get('product.product')
        location_ids = set()
        locations = cls.search([
                ('type', '=', 'storage'),
                ], order=[('id', 'ASC')])
        for sub_locations in grouped_slice(locations):
            quantities = Product.products_by_location(
                [location.id for location in sub_locations],
                with_childs=False)
            location_ids.update(
                key[0] for key, quantity in quantities.items()
                if quantity != 0)
        positive = (operator == '=' and bool(value)) or (
            operator == '!=' and not bool(value))
        if positive:
            return [('id', 'in', list(location_ids) or [-1])]
        return [('id', 'not in', list(location_ids) or [-1])]


class ShipmentOutStagingLocation(ModelSQL, ModelView):
    "Shipment Out - Staging Location"
    __name__ = 'stock.shipment.out-staging.location'

    shipment = fields.Many2One(
        'stock.shipment.out', "Shipment", required=True, ondelete='CASCADE')
    location = fields.Many2One(
        'stock.location', "Location", required=True, ondelete='RESTRICT',
        domain=[
            ('type', '=', 'storage'),
            ],
        help="Reserved staging location for the customer shipment.")

    @classmethod
    def __setup__(cls):
        super().__setup__()
        t = cls.__table__()
        cls._sql_constraints += [
            ('shipment_unique', Unique(t, t.shipment),
                'stock_staging_shipment_out.'
                'msg_staging_location_shipment_unique'),
            ('location_unique', Unique(t, t.location),
                'stock_staging_shipment_out.'
                'msg_staging_location_location_unique'),
            ]

    @classmethod
    def validate_fields(cls, records, field_names):
        super().validate_fields(records, field_names)
        if field_names and 'location' not in field_names:
            return
        for record in records:
            record.check_empty_location()

    def check_empty_location(self):
        if self.location and self.location.has_stock:
            raise ValidationError(gettext(
                    'stock_staging_shipment_out.'
                    'msg_staging_location_not_empty',
                    location=self.location.rec_name))
