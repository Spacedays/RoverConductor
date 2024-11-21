#!/usr/bin/env -S /bin/sh -c '"$(dirname "$0")/.venv/bin/python3" "$0" "$@"'
# ^ Run using local .venv

#### A simple keyboard interface to the rover

# import ast
import contextlib
import logging
import queue
import re
import traceback
from logging.handlers import (
    QueueHandler,  # LATER -- use & test logs
    QueueListener,
)

import click
import msgpack
from msgpack import OutOfData, Packer, Unpacker

from console_input import ThreadedKeyboardInput
from pico_interface import LEN_SEP, PACKETDELIM, ControlPacket, WrapMsgPack, PicoSerial

logQue = queue.Queue(-1)  # no max size; if max size, prep for queue full exception
log_queue_handler = QueueHandler(logQue)  # accepts logging messages to allow seperate threads
handler = logging.StreamHandler()  # TODO - wat dis?
listener = QueueListener(logQue, handler)

root = logging.getLogger()
root.addHandler(log_queue_handler)
formatter = logging.Formatter("%(threadName)s: %(message)s")  # LATER
handler.setFormatter(formatter)

# def logexception(self, msg, /, *args, **kwargs):
#     """
#     Delegate an exception call to the underlying logger, after adding
#     contextual information from this adapter instance.
#     """
#     msg, kwargs = self.process(msg, kwargs)
#     self.logger.debug(msg, *args, **kwargs)

listener.start()  # starts background logger thread   #TESTME   #TODO: use logQue somewhere

txQueue = queue.Queue(-1)
rxQueue = queue.Queue(-1)


def get_data_packet(
    packer: Packer, a: bool = False, b: bool = False, rt: int = 0, ljx: int = 0, ljy: int = 0
):
    msg = ControlPacket(a, b, rt, ljx, ljy)
    return WrapMsgPack(packer, msg.to_iter())
    # bytemsg = WrapMsgPack(packer, msg.to_iter())
    # click.echo(f'Packed {msg} to\r\n\t{packer.pack(msg.to_iter())} as\r\n\t{bytemsg}')    #DEBUG
    # txQueue.put(bytemsg)


def send_string_packet(packer: Packer, text: str, base_packet: ControlPacket = None):
    click.echo(f"  Writing {text} into control packet\r")
    msg = ControlPacket() if base_packet is None else base_packet
    msg.s = text  # bytes(text, "utf-8")
    return WrapMsgPack(packer, msg.to_iter())
    # bytemsg = WrapMsgPack(packer, msg.to_iter())
    # click.echo(f'Packed {msg} to\r\n\t{packer.pack(msg.to_iter())} as\r\n\t{bytemsg}')    #DEBUG
    # txQueue.put(bytemsg)


base_packet = ControlPacket(True, True, 0, 125, 126)


def msgpack_console():
    # rx_bytes = bytearray(128)
    unpacker = Unpacker()
    packer = Packer()
    PSer = PicoSerial(logQue)
    kthread = ThreadedKeyboardInput(
        lambda txt: txQueue.put(send_string_packet(packer, txt, base_packet))
    )
    while True:
        first_print = True
        with contextlib.suppress(queue.Empty):  # and PSer is not None:
            for obj in iter(lambda: txQueue.get(block=True, timeout=0.01), None):
                if obj is None:
                    continue
                if first_print:
                    kthread.toggle_silence()
                    first_print = False
                PSer.port.write(obj)
                click.echo(f">TX: {obj}\r")
        if not first_print:
            kthread.toggle_silence()

        # for o in unpacker:
        #     rxQueue.put(o)

        try:
            parse_messages(unpacker, PSer.port.read(PSer.port.in_waiting))
        except Exception as e:
            kthread.pause = True
            print("-" * 78)
            click.echo(bytes(traceback.format_exc(), "utf-8"), err=True)
            # print(traceback.format_exc())
            # click.echo(
            #     interactive_parse(
            #         e.__cause__.args[0] if e and e.__cause__ and e.__cause__.args else e
            #     ),
            #     err=True,
            # )
            # click.echo(click.wrap_text(e,initial_indent="",subsequent_indent="  ",preserve_paragraphs=True))
            # click.echo(mbytes, err=True)
            # click.echo("-"*78, err=True)

        obj = None
        first_print = True  # silence input while printing
        if rxQueue.qsize() != 0:
            with contextlib.suppress(queue.Empty):
                for obj in iter(lambda: rxQueue.get(block=True, timeout=0.01), None):
                    if obj is None:
                        break
                    if first_print:
                        kthread.toggle_silence()
                        first_print = False
                    click.echo(f"~RX({rxQueue.qsize()}):{obj}\r")
        if not first_print:
            kthread.toggle_silence()
        if kthread.exc_info:
            if isinstance(kthread.exc_info[1], KeyboardInterrupt):
                print("Exiting Console.")
                break
            raise kthread.exc_info[1].with_traceback(kthread.exc_info[2])


def isolate_msgpacket(mbytes: bytearray) -> tuple[bytearray, object, int]:
    """Extract a single message packet from data.

    Returns data prior to the message, the object (if any), and the index of any remaining data (-1 if none)"""

    start_idx = mbytes.find(PACKETDELIM)
    if start_idx == -1:
        return (mbytes, None, len(mbytes))

    prefix_data = mbytes[:start_idx]
    obj = None

    # if there's another message, get its start idx in case of an extraction error.
    # Otherwise, set fin_idx to end
    fin_idx = mbytes.find(PACKETDELIM, start_idx + len(PACKETDELIM))
    if fin_idx == -1:
        fin_idx = len(mbytes)

    # if LEN_SEP cannot be found, received an invalid packet
    if mbytes.find(LEN_SEP) < 0:
        return (mbytes[:fin_idx], None, fin_idx)

    # grab packet length & extract packet
    len_match = re.search(b"([0-9]+)", mbytes)
    try:
        packlen = int(mbytes[len(PACKETDELIM) : len_match.span()[1]])
    except Exception:
        # could not cast packet length to int; received invalid packet
        prefix_data = mbytes[:fin_idx]
        fin_idx = fin_idx
    else:
        mstart = len_match.span()[1] + len(LEN_SEP)
        obj = mbytes[mstart : mstart + packlen]

    return (prefix_data, obj, fin_idx)


def parse_messages(unpacker: Unpacker, mbytes: bytearray):
    """Isolate string and messagepack messages. Assumes messagepack messages are prepended by TERMSEQ, lengthm and LEN_SEP"""
    more_packed = True
    while more_packed:
        prefix_data, obj, fin_idx = isolate_msgpacket(mbytes)

        if prefix_data:
            rxQueue.put(prefix_data.decode("utf-8", "backslashreplace"))
        if obj:
            unpacker.feed(obj)
            rxQueue.put(obj)  # DEBUG - put message bytes in RX Queue prior to unpacking
            obj2 = unpacker.unpack()
            # rxQueue.put(unpacker.unpack())
            try:
                rxQueue.put(obj2)
            except Exception as e:
                print(f"Exception while unpacking RX message: {obj}")
                print(e)
                with contextlib.suppress(OutOfData):
                    unpacker.skip()
                rxQueue.put(obj)

        if fin_idx != len(mbytes):
            mbytes = mbytes[fin_idx:]
        else:
            return

if __name__ == "__main__":
    msgpack_console()
