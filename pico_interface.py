from asyncio import Queue
import serial
import serial.tools.list_ports
import logging
from dataclasses import dataclass
import msgpack
import struct
import crc8

serlog = logging.getLogger(
    "pico_serial"
)  # TESTME - does this log from another thread once setup?

TERMSEQ = b"\n~"
LEN_SEP = b"~"
STR_DELIM = b"-\n"  # Located @ end of packed messages if there is text following them 
PICO_RX_INDEX = 0x21
hash = crc8.crc8()

# FIXME - replace
# def UnPacketize(code, data):
#     if code == PICO_RX_INDEX:
#         obj = struct.unpack(data)
#         return obj
#     return msgpack.ExtType(code, data)


# def MsgPacketize(packer: msgpack.Packer, data):
#     """Wrap the bytes with an index and crc"""
#     bytedata = packer.pack(data)
#     hash.update(bytedata)
#     hash.reset()
#     return b"".join([PICO_RX_INDEX.to_bytes(), bytedata, hash.digest()])


def PacketMsg(packer: msgpack.Packer, data):
    """Wrap the bytes with a start character and length"""
    bytedata = packer.pack(data)
    return b"".join((TERMSEQ, bytes(str(len(bytedata)), "utf-8"), LEN_SEP, bytedata))


# @dataclass
# class MPZPacket:
#     index: int = 0 # 1 byte
#     data: bytes = 0
#     crc: int = 0


@dataclass
class ControlPacket:
    """Control Packet for msgpack, encoded as an array"""

    a: bool = False
    # x: bool = False
    # y: bool = False
    b: bool = False
    # lb: bool = False
    # rb: bool = False
    # lt: int = 0
    # rt: int = 0
    ljx: int = 0
    ljy: int = 0
    # rjx: int = 0
    # rjy: int = 0
    s: str = ""

    def to_iter(self):
        return (self.a, self.b, self.ljx, self.ljy, self.s)


class PicoSerial:
    def __init__(
        self, queue: Queue, portname: str = None, baudrate: int = 115200
    ) -> None:
        self.q = queue  # TODO read q
        self.port = None
        # self.baudrate = baudrate

        if portname is None:
            portname = PicoSerial.find_pico()

        self.port = serial.Serial(portname, baudrate, timeout=1)

    @classmethod
    def find_pico(cls, searchstr="pico"):
        pico_ports = []
        pico_desc = []
        for portname, desc, hwid in serial.tools.list_ports.comports():
            if searchstr in (desc.lower() + hwid):
                pico_ports.append(portname)
                pico_desc.append(desc + f" ; {hwid}")

        if len(pico_ports) == 0:
            raise FileNotFoundError("No pico serial ports found!")
        elif len(pico_ports) > 1:
            serlog.warn(
                f"Other picos found! Returning first device {pico_ports[0]} ; {pico_desc[0]}"
            )
        return pico_ports[0]

    # TODO: disambiguate
    def write(self, data):
        print("Writing ", data)
        self.port.write(data)

    def readline(self, *args):
        return self.port.readline(*args)

    def read(self, *args):
        return self.port.read(*args)

    def send_control_packet(self, packet: ControlPacket):
        pass
