import logging
from asyncio import Queue
from dataclasses import dataclass
from math import atan

import msgpack
import numpy as np
import serial
import serial.tools.list_ports

# import crc8

serlog = logging.getLogger("pico_serial")  # TESTME - does this log from another thread once setup?

PACKETDELIM = b"\n~"
LEN_SEP = b"~"
PICO_RX_INDEX = 0x21
# hash = crc8.crc8()


@dataclass
class Rover_Constants:
    SCDX: int = 114
    SCDY: int = 141
    # JOY_MAX: int = 32767
    # RT_MAX: int = 1024
    STEERCTR_D_MIN: int = 254
    STEERCTR_SCALING: int = 250
    STEERANG_MAX_RAD: float = np.pi / 4
    STEER_RATIO: int = 2
    RAD2DEG = 180 / np.pi


RCONST = Rover_Constants()

# FIXME - replace; encloses msgpack inbetween index & CRC
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


def WrapMsgPack(packer: msgpack.Packer, data):
    """Wrap the bytes with a start character and length"""
    bytedata = packer.pack(data)
    return b"".join((PACKETDELIM, bytes(str(len(bytedata)), "utf-8"), LEN_SEP, bytedata))


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
    rt: int = 0
    ljx: int = 0  # -32,767 to 32,767
    ljy: int = 0
    # rjx: int = 0
    # rjy: int = 0
    s: str = ""

    def to_iter(self):
        return (self.a, self.b, self.rt, self.ljx, self.ljy, self.s)


@dataclass
class MotionVector:
    vFL: int = 0
    vFR: int = 0
    vBL: int = 0
    vBR: int = 0
    aFL: int = 0
    aFR: int = 0
    aBL: int = 0
    aBR: int = 0

    def to_iter(self):
        return (self.vFL, self.vFR, self.vBL, self.vBR, self.aFL, self.aFR, self.aBL, self.aBR)


class PicoSerial:
    def __init__(self, queue: Queue, portname: str = None, baudrate: int = 115200) -> None:
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
                pico_desc.append(f"{desc} ; {hwid}")

        if not pico_ports:
            raise FileNotFoundError("No pico serial ports found!")
        elif len(pico_ports) > 1:
            serlog.warn(f"Other picos found! Returning first device {pico_ports[0]} ; {pico_desc[0]}")
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


# def calc_steer_center(joyx, joyy):
#     d = np.sign(joyx) * RCONST.STEERCTR_D_MIN + RCONST.STEERCTR_SCALING * np.tan(
#         joyx * np.pi / (2 * RCONST.JOY_MAX) + np.pi / 2
#     )
#     h = joyy / RCONST.JOY_MAX * (abs(d) - RCONST.STEERCTR_D_MIN) * np.tan(RCONST.STEERANGLE_MAX_RAD)
#     return (d, h)


def calc_steer_center(joyx, joyy):
    # d = np.sign(joyx) * RCONST.STEERCTR_D_MIN + (joyx/10)
    d = np.sign(joyx) * (
        abs(RCONST.STEERCTR_D_MIN)
        + abs(RCONST.STEERCTR_SCALING * np.tan(abs(joyx) * np.pi / 2 - np.pi / 2))
        # + RCONST.STEERCTR_SCALING * abs(np.tan(joyx * np.pi / (-2 * RCONST.JOY_MAX) + np.pi / 2))
    )
    h = -joyy * (abs(d) - RCONST.STEERCTR_D_MIN) * np.tan(RCONST.STEERANG_MAX_RAD)
    return (d, h)


def calc_motion_vec(cmd: ControlPacket, d=None, h=None):
    if d is None or h is None:
        d, h = calc_steer_center(cmd.ljx, cmd.ljy)
    

    mvec = MotionVector()
    if abs(d) < RCONST.STEERCTR_D_MIN:
        SCdist = (1,1,1,1)

        mvec.aFL = 0
        mvec.aFR = 0
        mvec.aBL = 0
        mvec.aBR = 0
    else:
        SCdist = (
            ((RCONST.SCDY - h) ** 2 + (-RCONST.SCDX - d) ** 2) ** 0.5,
            ((RCONST.SCDY - h) ** 2 + (RCONST.SCDX - d) ** 2) ** 0.5,
            ((-RCONST.SCDY - h) ** 2 + (-RCONST.SCDX - d) ** 2) ** 0.5,
            ((-RCONST.SCDY - h) ** 2 + (RCONST.SCDX - d) ** 2) ** 0.5,
        )

        mvec.aFL = int(atan((RCONST.SCDY - h) / (-RCONST.SCDX - d)) * RCONST.RAD2DEG)
        mvec.aFR = int(atan((RCONST.SCDY - h) / (RCONST.SCDX - d)) * RCONST.RAD2DEG)
        mvec.aBL = int(atan((-RCONST.SCDY - h) / (-RCONST.SCDX - d)) * RCONST.RAD2DEG)
        mvec.aBR = int(atan((-RCONST.SCDY - h) / (RCONST.SCDX - d)) * RCONST.RAD2DEG)
    m = max(max(SCdist[0], SCdist[1]), max(SCdist[2], SCdist[3]))

    throttle = cmd.rt

    mvec.vFL = SCdist[0] / m * throttle
    mvec.vFR = SCdist[1] / m * throttle
    mvec.vBL = SCdist[2] / m * throttle
    mvec.vBR = SCdist[3] / m * throttle

    return mvec
