""" Node classes used by the Node Server. """
from polyinterface import Node,LOG_HANDLER,LOGGER
from pylutron_caseta import BridgeDisconnectedError

import sys
import asyncio
#import concurrent.futures._base.TimeoutError
import concurrent
from syncer import sync
mainloop = asyncio.get_event_loop()

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
        asyncio.set_event_loop(mainloop)

    def start(self):
        super().start()

    def send_command(self, device, value):
        LOGGER.info("Sending value to Smart Bridge for device {}: {}".format(device, value))
        LOGGER.info("is_connected={}".format(self.controller.is_connected()))
        result = self.set_value(device, value)
        LOGGER.info("send_command result: {}".format(result))

    #@sync
    #async def set_value(self, device,value):
    def set_value(self, device,value):
        try:
            return self.controller.mainloop.run_until_complete(self.sb.set_value(device, value))
            #return await self.sb.set_value(device, value)
        except concurrent.futures._base.TimeoutError:
            LOGGER.error("Timed out...")
        except BridgeDisconnectedError:
            LOGGER.error('Bridge disconnected, should I try to reconnect?',exc_info=True)
            done = True
        except Exception as e:
            LOGGER.error('set_value {}'.format(e),exc_info=True)
            done = True

    def update(self,id,data):
        # Do nothing for now
        pass
        #LOGGER.info("update: {} {}".format(id,data))
        #val = self.sb.is_on(id)
        #LOGGER.info("update: {}".format(val))

class Scene(BaseNode):
    def activate(self, command):
        LOGGER.info("activate: command {}".format(command))
        address = command['address'].replace('scene', '', 1)
        LOGGER.info("activate: address {}".format(address))
        LOGGER.info("is_connected={}".format(self.controller.is_connected()))
        self.sb.activate_scene(address)

    def callback(self):
        LOGGER.info("callback")
        self.update()

    def query(self):
        #self.controller.sb.devices[device_id].update(self.device_id,device)
        #self.update()
        #self.reportDrivers()
        pass

    def update(self):
        #if self.controller.sb.devices[self.device_id]["current_state"] == 0:
        #    self.setDriver('ST', 100)
        #    self.setDriver('OL', 0)
        #else:
        #    self.setDriver('ST', 0)
        #    self.setDriver('OL', self.controller.sb.devices[self.device_id]["current_state"])
        pass

    drivers = []
    id = 'scene'

    commands = {
        'DON': activate,
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
        self.send_command(address, 100)
        #self.setDriver('ST', 0)
        #self.setDriver('OL', 100)

    def setClose(self, command):
        LOGGER.info("setClose: command {}".format(command))
        address = command['address'].replace('device', '', 1)
        self.send_command(address, 0)
        #self.setDriver('ST', 100)
        #self.setDriver('OL', 0)

    def setOpenLevel(self, command):
        LOGGER.info("setOpenLevel: command {}".format(command))
        address = command['address'].replace('device', '', 1)
        if command.get('value'):
            ol = int(command['value'])
        else:
            ol = int(command.get('query', {}).get('OL.uom51'))
        self.send_command(address, ol)
        if ol > 0:
            self.setDriver('ST', 0)
        else:
            self.setDriver('ST', 100)
        self.setDriver('OL', ol)

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
