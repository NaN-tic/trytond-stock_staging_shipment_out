# This file is part of the stock_staging_shipment_out module for Tryton.
# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.modules.company.tests import CompanyTestMixin
from trytond.tests.test_tryton import ModuleTestCase


class StockStagingShipmentOutTestCase(CompanyTestMixin, ModuleTestCase):
    'Test StockStagingShipmentOut module'
    module = 'stock_staging_shipment_out'


del ModuleTestCase
