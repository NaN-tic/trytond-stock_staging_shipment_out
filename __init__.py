# This file is part of the stock_staging_shipment_out module for Tryton.
# The COPYRIGHT file at the top level of this repository contains the full
# copyright notices and license terms.
from trytond.pool import Pool

from . import location, shipment


def register():
    Pool.register(
        location.Location,
        location.ShipmentOutStagingLocation,
        shipment.ShipmentIn,
        shipment.ShipmentOut,
        shipment.ShipmentInternal,
        module='stock_staging_shipment_out', type_='model')
