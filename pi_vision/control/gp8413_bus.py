"""GP8413 writes using explicit I2C transactions, matching i2ctransfer.

The DAC has no voltage feedback. A successful write cannot verify the output.
"""
import time


class GP8413Bus:
    def __init__(self, bus, message_factory):
        self.bus = bus
        self.message_factory = message_factory

    def write_byte_data(self, address, register, value):
        self.bus.i2c_rdwr(self.message_factory(address, [register, value]))
        # Allow range configuration to settle before writing output codes.
        time.sleep(.01)

    def write_i2c_block_data(self, address, register, data):
        self.bus.i2c_rdwr(self.message_factory(address, [register, *data]))

    def close(self):
        self.bus.close()


def open_bus():
    from smbus2 import SMBus, i2c_msg
    return GP8413Bus(SMBus(1), i2c_msg.write)
