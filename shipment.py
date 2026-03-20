# This file is part of the stock_staging_shipment_out module for Tryton.
# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.exceptions import UserError
from trytond.i18n import gettext
from trytond.model import ModelView, Workflow, fields
from trytond.pool import Pool, PoolMeta


class ShipmentOut(metaclass=PoolMeta):
    __name__ = 'stock.shipment.out'

    internal_shipments = fields.One2Many(
        'stock.shipment.internal', 'origin_shipment', "Internal Shipments",
        readonly=True)
    staging_location = fields.One2One(
        'stock.shipment.out-staging.location', 'shipment', 'location',
        "Staging Location", readonly=True)

    @classmethod
    def _use_staging_area(cls, shipment):
        return bool(
            shipment.warehouse
            and shipment.warehouse.use_staging_area_for_customer_shipments
            and shipment.warehouse.picking_location)

    @classmethod
    def _staging_candidates(cls, warehouse):
        Location = Pool().get('stock.location')
        picking_location = warehouse.picking_location
        candidates = list(Location.search([
                    ('type', '=', 'storage'),
                    ('left', '>', picking_location.left),
                    ('right', '<', picking_location.right),
                    ], order=[('left', 'ASC')]))
        candidates.append(picking_location)
        seen = set()
        ordered = []
        for location in candidates:
            if location.id in seen:
                continue
            seen.add(location.id)
            ordered.append(location)
        return ordered

    @classmethod
    def _find_staging_location(cls, shipment, reserved):
        for location in cls._staging_candidates(shipment.warehouse):
            if location.id in reserved:
                continue
            if location.staging_shipment_out:
                continue
            if location.has_stock:
                continue
            return location

    @classmethod
    def _allocate_staging_locations(cls, shipments):
        Relation = Pool().get('stock.shipment.out-staging.location')
        to_save = []
        relations = []
        reserved = set()

        for shipment in shipments:
            if not cls._use_staging_area(shipment):
                continue
            if shipment.staging_location:
                shipment.warehouse_storage = shipment.staging_location
                to_save.append(shipment)
                reserved.add(shipment.staging_location.id)
                continue

            location = cls._find_staging_location(shipment, reserved)
            if not location:
                raise UserError(gettext(
                        'stock_staging_shipment_out.'
                        'msg_missing_staging_location',
                        shipment=shipment.rec_name))
            shipment.warehouse_storage = location
            to_save.append(shipment)
            relations.append(Relation(shipment=shipment, location=location))
            reserved.add(location.id)

        if to_save:
            cls.save(to_save)
        if relations:
            Relation.save(relations)

    @classmethod
    def _release_staging_locations(cls, shipments):
        Relation = Pool().get('stock.shipment.out-staging.location')
        relations = Relation.search([
                ('shipment', 'in', [shipment.id for shipment in shipments]),
                ])
        if relations:
            Relation.delete(relations)

    @classmethod
    @ModelView.button
    @Workflow.transition('waiting')
    def wait(cls, shipments, moves=None):
        cls._allocate_staging_locations(shipments)
        super().wait(shipments, moves=moves)

    @classmethod
    @ModelView.button
    @Workflow.transition('draft')
    def draft(cls, shipments):
        super().draft(shipments)
        cls._release_staging_locations(shipments)

    @classmethod
    @ModelView.button
    @Workflow.transition('picked')
    def pick(cls, shipments):
        super().pick(shipments)
        cls._release_staging_locations(shipments)

    @classmethod
    @ModelView.button
    @Workflow.transition('cancelled')
    def cancel(cls, shipments):
        super().cancel(shipments)
        cls._release_staging_locations(shipments)

    @classmethod
    def copy(cls, shipments, default=None):
        if default is None:
            default = {}
        else:
            default = default.copy()
        default.setdefault('internal_shipments', None)
        return super().copy(shipments, default=default)


class ShipmentInternal(metaclass=PoolMeta):
    __name__ = 'stock.shipment.internal'

    origin_shipment = fields.Many2One(
        'stock.shipment.out', "Origin", readonly=True)
    origin_shipment_out = fields.Function(fields.Many2One(
            'stock.shipment.out', "Origin"),
        'get_origin_shipment_out', setter='set_origin_shipment_out')

    def get_origin_shipment_out(self, name):
        return self.origin_shipment.id if self.origin_shipment else None

    @classmethod
    def set_origin_shipment_out(cls, shipments, name, value):
        cls.write(shipments, {'origin_shipment': value})

    @classmethod
    def generate_internal_shipment(cls, clean=True, warehouses=None):
        shipments = super().generate_internal_shipment(
            clean=clean, warehouses=warehouses)
        to_save = []
        for shipment in shipments or []:
            if shipment.origin_shipment:
                continue
            if not shipment.to_location.staging_shipment_out:
                continue
            shipment.origin_shipment = shipment.to_location.staging_shipment_out
            to_save.append(shipment)
        if to_save:
            cls.save(to_save)
        return shipments

    @classmethod
    def copy(cls, shipments, default=None):
        if default is None:
            default = {}
        else:
            default = default.copy()
        default.setdefault('origin_shipment', None)
        return super().copy(shipments, default=default)
