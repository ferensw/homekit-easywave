"""This is a representation of a Homekit window cover working with the Easywave protocol.
"""

import logging
import signal
import random
import time

from pyhap.accessory import Accessory, Bridge
from pyhap.accessory_driver import AccessoryDriver
import pyhap.loader as loader
from pyhap.const import CATEGORY_WINDOW_COVERING
from pyhap.characteristic import CharacteristicError

import asyncio
import logging
import sys
import json
import os
from typing import Dict, Optional, Sequence, Type  # noqa: unused-import

import pkg_resources
from docopt import docopt

from datetime import timedelta
import concurrent

from easywave.protocol import EasywaveProtocol, create_easywave_connection
from serial.tools import list_ports


logging.basicConfig(level=logging.INFO, format="[%(module)s] %(message)s")


class Cover(Accessory):
    """Easywave window cover."""

    category = CATEGORY_WINDOW_COVERING

    _protocol = None

    @classmethod
    def set_easywave_protocol(cls, protocol):
        """Set the easywave asyncio protocol as a class variable."""
        cls._protocol = protocol
        logging.info("Protocol set")


    def __init__(self, *args, **kwargs):
        self.channel_id = kwargs.pop('channel_id')
        self.remote_id = kwargs.pop('remote_id')
        self.time_up = kwargs.pop('time_up')
        self.time_down = kwargs.pop('time_down')
        super().__init__(*args, **kwargs)
        self._stop_command = asyncio.Event(loop=self.driver.loop)
        self._ready_to_send = asyncio.Lock(loop=self.driver.loop)
        self._ready_to_handle = asyncio.Lock(loop=self.driver.loop)

        serv_cover = self.add_preload_service('WindowCovering')
        self.char_current_position = serv_cover.configure_char('CurrentPosition')
        self.char_target_position = serv_cover.configure_char('TargetPosition', setter_callback=self.set_target_position)
        self.char_position_state = serv_cover.configure_char('PositionState')

    def add_info_service(self):
        serv_info = self.driver.loader.get_service('AccessoryInformation')
        serv_info.configure_char('Name', value=self.display_name)
        serv_info.configure_char("Manufacturer", value="Easywave")
        serv_info.configure_char("Model", value="Cover")
        serv_info.configure_char('SerialNumber', value='default')
        self.add_service(serv_info)

    def set_target_position(self, value):
        if not self._ready_to_send.locked():
            self.driver.add_job(self.dispatch_send_command, value) #self.ack optie?
            logging.debug("Start setting target position: {}".format(value))
        else:
            logging.debug("Setting target position already in progress")


    async def dispatch_send_command(self, value):
        await self._ready_to_send.acquire()
        current_position = self.char_current_position.value
        if value < (current_position - 10) or value == 0:
            cmd = 'A'
            time_travel = (current_position - value) / 100 * self.time_up
        elif value > (current_position + 10) or value == 100:
            cmd = 'B'
            time_travel = (value - current_position) / 100 * self.time_down
        else:
            logging.debug("Position change to small to handle. Not changing to target value: {}".format(value))
            self.char_target_position.set_value(current_position)
            self._ready_to_send.release()
            return
        logging.debug('Sending event: channel:{}, command:{}'.format(self.channel_id, cmd))
        if not (await self._protocol.send_command_ack(self.channel_id, cmd)):
            logging.debug('Send command failed')
            self.char_target_position.set_value(current_position)
            self._ready_to_send.release()
            return
        await asyncio.sleep(time_travel)
        if value != 100 and value != 0:
            # Target position changed during position change?
            while value != self.char_target_position.value:
                target_value = self.char_target_position.value
                logging.debug('Target position changed to: {}'.format(target_value))
                if cmd == 'A':
                    extra_time_travel = (value - target_value) / 100 * self.time_up
                else:
                    extra_time_travel = (target_value - value) / 100 * self.time_down
                await asyncio.sleep(extra_time_travel)
                value = target_value
            # Don't send stop command if value is near endpoint
            if value < 5:
                value = 0
                self.char_target_position.set_value(value)
            elif value > 95:
                value = 100
                self.char_target_position.set_value(value)
            else:
                cmd = 'C'
                logging.debug('Stop target position change')
                logging.debug('Sending event: channel:{}, command:{}'.format(self.channel_id, cmd))
                if not (await self._protocol.send_command_ack(self.channel_id, cmd)):
                    logging.debug('Send command failed. Not able to stop')
                    if value < current_position:
                        value = 0
                    else:
                        value = 100
                    self.char_target_position.set_value(value)
        self.char_current_position.set_value(value)
        self._ready_to_send.release()

    def receive_command(self, command):
        if command == 'A':
            value = 0
        elif command == 'B':
            value = 100
        elif command == 'C':
            self._stop_command.set()
            value = None
        else:
            value = None
        if isinstance(value, int):
            self.driver.add_job(self.async_dispatch_command, value)


    async def async_dispatch_command(self, value):
        await self._ready_to_handle.acquire()
        try:
            self._stop_command.clear()
            start_time = self.driver.loop.time()
            self.char_target_position.set_value(value)
            current_position = self.char_current_position.value
            if value > current_position:
                time_travel = (value - current_position) / 100 * self.time_down
            else:
                time_travel = (current_position - value) / 100 * self.time_up
    
            try:
                await asyncio.wait_for(self._stop_command.wait(), time_travel, loop=self.driver.loop)
                logging.debug('Stop command received')
            except asyncio.TimeoutError:
                self.char_current_position.set_value(value)
                logging.debug('End reached')
            else:
                end_time = self.driver.loop.time()
                travelled_time = end_time - start_time
                if value > current_position:
                    new_value = travelled_time / self.time_up * 100 + current_position
                else:
                    new_value = current_position - travelled_time / self.time_down * 100
                self.char_target_position.set_value(int(new_value))
                self.char_current_position.set_value(int(new_value))
        finally:
            self._ready_to_handle.release()

class EasywaveBridge(Bridge):
    def __init__(self, driver, display_name, **kwargs):
        self.usb_port = list(list_ports.grep("Easywave"))[0]
        super().__init__(driver, display_name, **kwargs)
        self._stop_command = asyncio.Event(loop=self.driver.loop)
        self._ready_to_handle = asyncio.Lock(loop=self.driver.loop)


    def add_info_service(self):
        info_service = self.driver.loader.get_service("AccessoryInformation")
        info_service.configure_char("Name", value='Easywave Bridge')
        info_service.configure_char("Manufacturer", value=self.usb_port.manufacturer)
        info_service.configure_char("Model", value=self.usb_port.product)
        info_service.configure_char("SerialNumber", value=self.usb_port.serial_number)
        self.add_service(info_service)

    def config_changed(self):
        self.driver.config_changed()


    def get_accessory(self, remote_id):
        for accessory in self.accessories.values():
            if accessory.remote_id == remote_id:
                return accessory


    async def run(self):
        self.loop = self.driver.loop
        conn = create_easywave_connection(loop=self.loop, protocol=EasywaveProtocol, packet_callback=self.packet_callback)
        transport, protocol = await conn
        Cover.set_easywave_protocol(protocol)
        await super().run()


    async def stop(self):
        await super().stop()
        # Make sure we write our current data.
        self.driver.persist()


    def packet_callback(self, event):
        """Handle incoming Easywave events.
        """

        logging.debug('event received: {}'.format(event))
        # Lookup remote_id who registered with device
        remote_id = event.get('id', None)
        command = event.get('command', None)
        accessory = self.get_accessory(remote_id)
        accessory.receive_command(command)


def get_bridge(driver):
    with open(os.path.join(os.path.dirname(__file__),'config.json')) as f:
        config = json.load(f)
    bridge = EasywaveBridge(driver, 'Bridge')
    for cover in config['covers']:
        acc_cover = Cover(driver, cover['name'], channel_id=cover['channel_id'], remote_id=cover['remote_id'], time_up=cover['time_up'], time_down=cover['time_down'])
        bridge.add_accessory(acc_cover)
    return bridge


driver = AccessoryDriver(port=51826, persist_file='~/homekit-easywave/accessory.state')
driver.add_accessory(accessory=get_bridge(driver))
signal.signal(signal.SIGTERM, driver.signal_handler)
driver.start()
