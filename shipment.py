# This file is part of the stock_staging_shipment_out module for Tryton.
# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from collections import defaultdict

from trytond.exceptions import UserError
from trytond.i18n import gettext
from trytond.model import ModelView, Workflow, fields
from trytond.pool import Pool, PoolMeta
from trytond.transaction import Transaction


class ShipmentOut(metaclass=PoolMeta):
    __name__ = 'stock.shipment.out'

    internal_shipments = fields.One2Many(
        'stock.shipment.internal', 'origin_shipment', "Internal Shipments",
        readonly=True)
    staging_location = fields.One2One(
        'stock.shipment.out-staging.location', 'shipment', 'location',
        "Staging Location", readonly=True)

    @classmethod
    def _find_staging_location(cls, shipment, reserved):
        for location in shipment.warehouse._staging_candidates():
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
            if not shipment.warehouse._use_staging_area():
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


class ShipmentIn(metaclass=PoolMeta):
    __name__ = 'stock.shipment.in'

    @classmethod
    def _staging_inventory_signature(cls, shipment):
        Date = Pool().get('ir.date')
        if not shipment.warehouse._use_staging_area():
            return
        with Transaction().set_context(company=shipment.company.id):
            today = Date.today()
        planned_dates = [
            date for date in [shipment._move_planned_date[1], today] if date]
        return (
            shipment.company.id,
            shipment.warehouse.id,
            shipment.warehouse_storage.id,
            max(planned_dates),
            )

    @classmethod
    def _staging_inventory_locations(cls, shipment):
        if not shipment.warehouse._use_staging_area():
            return []
        return [
            location for location in shipment.warehouse._staging_candidates()
            if (location.type == 'storage'
                and location.provisioning_location == shipment.warehouse_storage)
            ]

    @classmethod
    def _get_staging_inventory_needs(cls, shipments):
        Product = Pool().get('product.product')
        by_signature = defaultdict(list)
        for shipment in shipments:
            if shipment.warehouse_storage == shipment.warehouse_input:
                continue
            signature = cls._staging_inventory_signature(shipment)
            if signature:
                by_signature[signature].append(shipment)

        needs = {}
        for signature, signature_shipments in by_signature.items():
            company_id, _, _, planned_date = signature
            products = sorted({
                    move.product.id
                    for shipment in signature_shipments
                    for move in shipment.incoming_moves
                    if move.quantity > 0
                    })
            locations = []
            seen = set()
            for shipment in signature_shipments:
                for location in cls._staging_inventory_locations(shipment):
                    if location.id in seen:
                        continue
                    seen.add(location.id)
                    locations.append(location)
            if not products or not locations:
                needs[signature] = (locations, {})
                continue
            with Transaction().set_context(
                    company=company_id,
                    forecast=True,
                    stock_date_end=planned_date):
                pbl = Product.products_by_location(
                    [location.id for location in locations],
                    with_childs=False,
                    grouping_filter=(products,))
            needs[signature] = (
                locations,
                {(location.id, product_id): -quantity
                    for location in locations
                    for product_id in products
                    if (quantity := pbl.get((location.id, product_id), 0)) < 0
                    })
        return needs

    def _get_split_inventory_move(
            self, incoming_move, quantity, unit, to_location):
        move = self._get_inventory_move(incoming_move)
        if not move or quantity <= 0:
            return
        move.unit = unit
        move.quantity = quantity
        move.to_location = to_location
        return move

    def _get_staging_inventory_moves(
            self, incoming_move, staging_locations, staging_needs):
        Uom = Pool().get('product.uom')
        move = self._get_inventory_move(incoming_move)
        if not move:
            return []

        product = incoming_move.product
        remaining_quantity = Uom.compute_qty(
            incoming_move.unit, incoming_move.quantity, product.default_uom,
            round=False)
        moves = []
        for location in staging_locations:
            needed_quantity = staging_needs.get((location.id, product.id), 0)
            if needed_quantity <= 0:
                continue
            quantity = min(remaining_quantity, needed_quantity)
            move = self._get_split_inventory_move(
                incoming_move, quantity, product.default_uom, location)
            if not move:
                continue
            moves.append(move)
            staging_needs[(location.id, product.id)] -= quantity
            remaining_quantity -= quantity
            if remaining_quantity <= 0:
                break

        if remaining_quantity > 0:
            move = self._get_split_inventory_move(
                incoming_move, remaining_quantity, product.default_uom,
                self.warehouse_storage)
            if move:
                moves.append(move)
        return moves

    @classmethod
    def create_inventory_moves(cls, shipments):
        staging_needs = cls._get_staging_inventory_needs(shipments)
        for shipment in shipments:
            if shipment.warehouse_storage == shipment.warehouse_input:
                continue
            moves = list(shipment.moves)
            signature = cls._staging_inventory_signature(shipment)
            staging_locations, needs = staging_needs.get(signature, ([], {}))
            for incoming_move in shipment.incoming_moves:
                moves.extend(shipment._get_staging_inventory_moves(
                        incoming_move, staging_locations, needs))
            shipment.moves = moves
        cls.save(shipments)
