import datetime
import unittest
from decimal import Decimal

from proteus import Model, Wizard
from trytond.modules.company.tests.tools import create_company, get_company
from trytond.modules.stock.exceptions import MoveOriginWarning
from trytond.tests.test_tryton import drop_db
from trytond.tests.tools import activate_modules


class Test(unittest.TestCase):

    def setUp(self):
        drop_db()
        super().setUp()

    def tearDown(self):
        drop_db()
        super().tearDown()

    def test(self):
        config = activate_modules('stock_staging_shipment_out')

        _ = create_company()
        company = get_company()

        User = Model.get('res.user')
        Warning = Model.get('res.user.warning')
        config._context = User.get_preferences(True, config.context)

        ProductUom = Model.get('product.uom')
        ProductTemplate = Model.get('product.template')
        Location = Model.get('stock.location')
        Move = Model.get('stock.move')
        OrderPoint = Model.get('stock.order_point')
        ShipmentIn = Model.get('stock.shipment.in')
        ShipmentOut = Model.get('stock.shipment.out')
        ShipmentInternal = Model.get('stock.shipment.internal')
        Party = Model.get('party.party')

        def click_skip_origin_warning(record, button):
            try:
                record.click(button)
            except MoveOriginWarning as warning:
                _, (key, *_) = warning.args
                Warning(user=config.user, name=key).save()
                record.click(button)

        customer = Party(name='Customer')
        customer.save()
        supplier = Party(name='Supplier')
        supplier.save()

        unit, = ProductUom.find([('name', '=', 'Unit')])
        template = ProductTemplate()
        template.name = 'Product'
        template.default_uom = unit
        template.type = 'goods'
        template.list_price = Decimal('20')
        template.save()
        product, = template.products

        warehouse, = Location.find([('code', '=', 'WH')])
        supplier_loc, = Location.find([('code', '=', 'SUP')])
        customer_loc, = Location.find([('code', '=', 'CUS')])
        input_loc, = Location.find([('code', '=', 'IN')])
        output_loc, = Location.find([('code', '=', 'OUT')])
        storage_loc, = Location.find([('code', '=', 'STO')])
        lost_loc, = Location.find([('type', '=', 'lost_found')], limit=1)

        staging_view = Location(name='Staging', code='STGV', type='view')
        staging_view.parent = storage_loc
        staging_view.save()

        picking_loc = Location(name='Picking', code='PICK', type='storage')
        picking_loc.parent = staging_view
        picking_loc.save()

        full_loc = Location(name='Stage 0', code='STG0', type='storage')
        full_loc.parent = picking_loc
        full_loc.provisioning_location = storage_loc
        full_loc.save()

        staging_loc = Location(name='Stage 1', code='STG1', type='storage')
        staging_loc.parent = picking_loc
        staging_loc.provisioning_location = storage_loc
        staging_loc.save()

        orphan_loc = Location(name='Stage 2', code='STG2', type='storage')
        orphan_loc.parent = picking_loc
        orphan_loc.provisioning_location = storage_loc
        orphan_loc.save()

        spare_loc = Location(name='Stage 3', code='STG3', type='storage')
        spare_loc.parent = picking_loc
        spare_loc.provisioning_location = storage_loc
        spare_loc.save()

        warehouse.picking_location = picking_loc
        warehouse.use_staging_area_for_customer_shipments = True
        warehouse.save()

        order_point = OrderPoint()
        order_point.product = product
        order_point.location = orphan_loc
        order_point.provisioning_location = storage_loc
        order_point.type = 'internal'
        order_point.min_quantity = 1
        order_point.target_quantity = 2
        order_point.company = company
        order_point.save()

        shipment_in = ShipmentIn()
        shipment_in.supplier = supplier
        move = shipment_in.incoming_moves.new()
        move.product = product
        move.quantity = 10
        move.from_location = supplier_loc
        move.to_location = input_loc
        move.unit_price = Decimal('5')
        move.currency = company.currency
        shipment_in.save()
        click_skip_origin_warning(shipment_in, 'receive')
        click_skip_origin_warning(shipment_in, 'do')

        move = Move()
        move.product = product
        move.quantity = 1
        move.unit = unit
        move.from_location = storage_loc
        move.to_location = full_loc
        move.company = company
        click_skip_origin_warning(move, 'do')

        shipment_out = ShipmentOut()
        shipment_out.planned_date = datetime.date.today()
        shipment_out.effective_date = datetime.date.today()
        shipment_out.customer = customer
        shipment_out.warehouse = warehouse
        shipment_out.company = company
        move = shipment_out.outgoing_moves.new()
        move.product = product
        move.unit = unit
        move.quantity = 3
        move.from_location = output_loc
        move.to_location = customer_loc
        move.company = company
        move.unit_price = Decimal('0')
        move.currency = company.currency
        shipment_out.click('wait')

        shipment_out.reload()
        self.assertEqual(shipment_out.state, 'waiting')
        self.assertEqual(shipment_out.staging_location.code, 'STG1')
        self.assertEqual(shipment_out.warehouse_storage.code, 'STG1')

        move = Move()
        move.product = product
        move.quantity = 1
        move.unit = unit
        move.from_location = full_loc
        move.to_location = lost_loc
        move.company = company
        click_skip_origin_warning(move, 'do')

        Wizard('stock.supply').execute('create_')
        internal, = ShipmentInternal.find([
                ('to_location', '=', staging_loc.id),
                ('state', '=', 'request'),
                ])
        self.assertEqual(internal.origin_shipment_out.id, shipment_out.id)

        internal.click('draft')
        internal.click('wait')
        internal.click('assign_try')
        internal.reload()
        self.assertEqual(internal.state, 'assigned')
        internal.click('do')

        move = Move()
        move.product = product
        move.quantity = 2
        move.unit = unit
        move.from_location = orphan_loc
        move.to_location = lost_loc
        move.company = company
        click_skip_origin_warning(move, 'do')

        Wizard('stock.supply').execute('create_')
        orphan_internal, = ShipmentInternal.find([
                ('to_location', '=', orphan_loc.id),
                ('state', '=', 'request'),
                ])
        self.assertIsNone(orphan_internal.origin_shipment_out)
        orphan_internal.click('draft')
        orphan_internal.click('wait')
        orphan_internal.click('assign_try')
        orphan_internal.reload()
        self.assertEqual(orphan_internal.state, 'assigned')
        click_skip_origin_warning(orphan_internal, 'do')

        shipment_out.click('assign_try')
        shipment_out.reload()
        self.assertEqual(shipment_out.state, 'assigned')
        shipment_out.click('pick')

        shipment_out.reload()
        self.assertEqual(shipment_out.state, 'picked')
        self.assertIsNone(shipment_out.staging_location)

        orphan_locations = Location.find([
                ('staging_customer_shipment_location', '=', True),
                ('staging_shipment_out', '=', None),
                ('has_stock', '=', True),
                ])
        self.assertEqual([location.code for location in orphan_locations], ['STG2'])
