""" Node classes used by the Node Server. """
from polyinterface import Node,LOG_HANDLER,LOGGER
from pylutron_caseta import BridgeDisconnectedError

import sys
import asyncio
#import concurrent.futures._base.TimeoutError
import concurrent
from syncer import sync
#mainloop = asyncio.get_event_loop()

class BaseNode(Node):
    def __init__(self,
                 controller,
                 primary,
                 address,
                 name,
                 sb):
        # Each device should be it's own primary
        super().__init__(controller, address, address, name)
        self.sb = sb
        self.name = name
        self.address = address
        #asyncio.set_event_loop(mainloop)

    def start(self):
        super().start()

    def set_value(self, device, value):
        LOGGER.info("Sending value to Smart Bridge for device {}: {}".format(device, value))
        LOGGER.info("is_connected={}".format(self.controller.is_connected()))
        try:
            result = asyncio.run_coroutine_threadsafe(self.sb.set_value(device, value), self.controller.mainloop)
        except Exception as e:
            LOGGER.error('set_value {}'.format(e),exc_info=True)
            result = False
        LOGGER.info("set_value result: {}".format(result))

    def update(self,id,data):
        # Do nothing for now
        pass

class Scene(BaseNode):
    def activate(self, command):
        LOGGER.info("activate: command {}".format(command))
        address = command['address'].replace('scene', '', 1)
        LOGGER.info("activate: address {}".format(address))
        LOGGER.info("is_connected={}".format(self.controller.is_connected()))
        asyncio.run(self.sb.activate_scene(address))

    def callback(self):
        LOGGER.info("callback")
        self.update()

    def query(self):
        pass

    def update(self):
        pass

    drivers = []
    id = 'scene'

    commands = {
        'DON': activate,
        'DOF': activate
    }


class SerenaHoneycombShade(BaseNode):
    def __init__(self,
                 controller,
                 primary,
                 address,
                 name,
                 sb,
                 device_id,
                 type,
                 zone,
                 current_state):
        super().__init__(controller, primary, address, name, sb)
        self.sb = sb
        self.name = name
        self.address = address
        self.device_id = device_id
        self.type = type
        self.zone = zone
        self.current_state = current_state

    def start(self):
        super().start()
        self.sb.add_subscriber(self.device_id,self.callback)
        self.set_drivers()

    def callback(self):
        LOGGER.info("callback")
        self.set_drivers()

    def query(self):
        self.update()
        self.reportDrivers()

    def update(self):
        self.controller.sb.devices[self.device_id].update()
        self.set_drivers()

    def set_drivers(self):
        if self.controller.sb.devices[self.device_id]["current_state"] == 0:
            self.setDriver('ST', 100)
            self.setDriver('OL', 0)
        else:
            self.setDriver('ST', 0)
            self.setDriver('OL', self.controller.sb.devices[self.device_id]["current_state"])

    def setOpen(self, command):
        LOGGER.info("setOpen: command {}".format(command))
        address = command['address'].replace('device', '', 1)
        self.set_value(address, 100)


    def setClose(self, command):
        LOGGER.info("setClose: command {}".format(command))
        address = command['address'].replace('device', '', 1)
        self.set_value(address, 0)

    def setOpenLevel(self, command):
        LOGGER.info("setOpenLevel: command {}".format(command))
        address = command['address'].replace('device', '', 1)
        if command.get('value'):
            ol = int(command['value'])
        else:
            ol = int(command.get('query', {}).get('OL.uom51'))
        self.set_value(address, ol)

    drivers = [
        {'driver': 'ST', 'value': 0, 'uom': 79},
        {'driver': 'OL', 'value': 0, 'uom': 51}
    ]
    id = 'serenashade'

    commands = {
        'DON': setOpen,
        'DOF': setClose,
        'OL': setOpenLevel,
    }

class QsWirelessShade(SerenaHoneycombShade):

    id = 'qswirelessshade'
