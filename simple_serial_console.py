#!/usr/bin/env -S /bin/sh -c '"$(dirname "$0")/.venv/bin/python3" "$0" "$@"'
#### A simple keyboard interface to the rover


import ast
import logging
import queue
import sys
import threading
from logging.handlers import QueueHandler  # LATER -- use & test logs
from logging.handlers import QueueListener
import traceback
import re

import click
import msgpack
from msgpack import Packer, Unpacker

from pico_interface import TERMSEQ, LEN_SEP, ControlPacket, PacketMsg, PicoSerial

logQue = queue.Queue(-1)  # no max size; if max size, prep for queue full exception
log_queue_handler = QueueHandler(
    logQue
)  # accepts logging messages to allow seperate threads
handler = logging.StreamHandler()  # TODO - wat dis?
listener = QueueListener(logQue, handler)

root = logging.getLogger()
root.addHandler(log_queue_handler)
formatter = logging.Formatter("%(threadName)s: %(message)s")  # LATER
handler.setFormatter(formatter)
listener.start()  # starts background logger thread   #TESTME   #TODO: use logQue somewhere

txQueue = queue.Queue(-1)
rxQueue = queue.Queue(-1)


class KeyboardThreadChar(threading.Thread):
    def __init__(self, newline_cbk=None, name="keyboard-input-thread"):
        self.newline_cbk = newline_cbk
        super(KeyboardThreadChar, self).__init__(name=name, daemon=True)
        self.val = ""
        self.silent_val = ""  # characters typed while echo_state is false
        self.echo_state = True
        self.exc_info = None  # Pass keyboard interrupt to be re-raised
        self.running = True
        self.pause = False
        click.echo("> ", nl=False)
        self.start()

    def run(self):
        try:
            while self.running:
                if not self.pause:
                    c = click.getchar(echo=False)
                    self.val += c
                    if self.echo_state:
                        click.echo(c, nl=False)
                    else:
                        self.silent_val += c
                    self.update_inp()  # Detects newlines (complete commands) and prints them
        except (Exception, KeyboardInterrupt):
            self.exc_info = sys.exc_info()

    def update_inp(self):
        if "\r" in self.val or "\n" in self.val:
            # click.echo('~rn')   #DEBUG
            # sys.stdout.write(
            #     "\033[2K\033[1G"
            # )  # clear line and reset to start #TESTME - how should this behave?
            self.parse_lines()

    def toggle_silence(self):
        # Print silenced text
        if not self.echo_state:
            click.echo(self.silent_val, nl=False)
            self.silent_val = ""
        self.echo_state = not self.echo_state

    def parse_lines(self):
        # if not ("\r" in self.val or "\n" in self.val):
        #     return
        # sys.stdout.write("\033[2K\033[1G")  # clear line and reset to start #TESTME - how should this behave?

        lines = self.val.splitlines()  # strip terminating characters
        for line in lines:
            if self.newline_cbk is not None:
                self.newline_cbk(line)
            else:
                click.echo(f"Entered: {line}")
        click.echo("> ", nl=False)
        self.val = ""

    def join(self):
        threading.Thread.join(self)
        if self.exc_info:
            msg = "Thread '%s' threw an exception: %s" % (self.getName(), self.exc[1])
            click.echo(f"exception: \r{msg}")  # (msg)
            new_exc = Exception(msg)
            raise new_exc.with_traceback(self.exc[2])


def string_to_packet(packer: Packer, text: str, base_packet: ControlPacket = None):
    click.echo(f"Writing {text} into control packet")
    msg = ControlPacket() if base_packet is None else base_packet
    msg.s = bytes(text, 'utf-8')
    # bytemsg = MsgPacketize(packer, msg.to_iter())
    # click.echo("".join(hex(byte)[1:] for byte in bytemsg))  # DEBUG
    bytemsg = PacketMsg(packer, msg.to_iter())
    txQueue.put(bytemsg)
    # txQueue.put(msgpack.packb(MPZPacket(PICO_RX_INDEX, base_packet)))
    # txQueue.put(packer.pack(msg.to_iter()))
    # return base_packet


def serial_test_msgpack():
    # rx_bytes = bytearray(128)
    unpacker = Unpacker()
    packer = Packer()
    PSer = PicoSerial(logQue)
    kthread = KeyboardThreadChar(lambda txt: string_to_packet(packer, txt))
    while True:
        obj = 0
        while not txQueue.empty() and obj is not None:  # and PSer is not None:
            obj = txQueue.get(block=True, timeout=0.01)
            if obj is None:
                continue
            click.echo(f">TX: {obj}")
            PSer.port.write(obj)

        # kthread.toggle_silence()
        for o in unpacker:
            rxQueue.put(o)

        try:
            parse_messages(unpacker, PSer.port.read(PSer.port.in_waiting))
        except Exception as e:
            kthread.pause = True
            click.echo(traceback.format_exc())
            click.echo(
                interactive_parse(
                    e.__cause__.args[0] if e and e.__cause__ and e.__cause__.args else e
                )
            )
        # show console indicator if it was replaced with message contents
        print_console = False
        obj = None
        first_print = True
        try:
            for obj in iter(lambda: rxQueue.get(block=True, timeout=0.01), None):
                # while not rxQueue.empty():
                # obj = rxQueue.get_nowait()
                if obj is None:
                    continue
                if first_print:
                    kthread.toggle_silence()
                    sys.stdout.write("\033[2K\033[1G")
                    first_print = False

                click.echo(f"\r~RX:{obj}")  # DEBUG
                print_console = True
        except queue.Empty:
            pass
        if print_console:
            click.echo("> ", nl=False)
            kthread.toggle_silence()
            print_console = False
        if kthread.exc_info:
            if isinstance(kthread.exc_info[1], KeyboardInterrupt):
                print("Exiting Console.")
                break
            raise kthread.exc_info[1].with_traceback(kthread.exc_info[2])


# HACK - extra characters at the end??
def parse_messages(unpacker: Unpacker, rx_bytes: bytearray):
    messages = re.split(TERMSEQ, rx_bytes)  # does not include termseq separator in list
    if len(messages) == 0:
        return

    if messages[0] == b"":
        del messages[0]
        if len(messages) == 0:
            return

    for mbytes in messages:
        idx = -1
        strmsg = None
        try:
            idx = mbytes.find(LEN_SEP)
            # No separator -> string data
            if idx < 0:
                # click.echo(f"Str:{mbytes.decode(encoding='utf-8')}\n")    #DEBUG
                rxQueue.put(f"Str:{mbytes.decode(encoding='utf-8')}")
            # Separator -> truncate message and process it later as a string
            elif idx > 0:
                # get first digit and use it as the message length
                m = re.search(b"([0-9]+)", mbytes)
                mlen = int(mbytes[: m.span()[1]])  # //2  #TODO: why /2? /x delimeter?
                mstart = m.span()[1] + len(b"~")
                strmsg = mbytes[mstart + mlen :]
                mbytes = mbytes[mstart : mstart + mlen]
        except Exception as e:
            click.echo(f"Exception raised while parsing string!\r\t{e}\r")
            click.echo(mbytes)
            e.__cause__ = BaseException(mbytes)
            raise e

        if idx < 0:
            continue

        unpacker.feed(mbytes)
        rxQueue.put(unpacker.unpack())
        if strmsg:
            startidx = strmsg.find(b"-\n")
            if startidx < 0:
                continue
            strmsg = strmsg[strmsg.find(b"-\n") + 2 :]
            # click.echo(f"Str:{strmsg.decode(encoding='utf-8')}\n")
            rxQueue.put(strmsg.decode(encoding="utf-8"))


def interactive_parse(packedmsg: bytearray):
    click.echo(f"Parsing: {packedmsg}")
    if not click.confirm(
        f"Unpacking failed. Try again manually?\r{packedmsg}", default=True
    ):
        return
    while True:
        try:
            ans = click.prompt(
                "What would you like to parse?",
                type=click.Choice(
                    ("int", "str", "strsearch", "bytestrsearch", "unpack", "quit")
                ),
            )
            if ans == "strsearch":
                s: str = click.prompt("Enter the string to search for")
                click.echo(f"\t{packedmsg.find(bytes(s, encoding="utf-8"))}")

            elif ans == "int":
                i = int(click.prompt("Enter the starting index of the int"))
                j = int(click.prompt("Enter the length of the int"))
                click.echo(f"\t{int.from_bytes(packedmsg[i:i+j])}")

            elif ans == "str":
                i = int(click.prompt("Enter the starting index of the string"))
                j = int(click.prompt("Enter the length of the string"))
                click.echo(f"\t{packedmsg[i:i+j].decode()}")

            elif ans == "bytestrsearch":
                s = click.prompt("Enter the bytestring to eval & search: ")
                b = ast.literal_eval(s)
                click.echo(f"\t{packedmsg.find(b)}")

            elif ans == "unpack":
                click.echo()
                i = int(
                    click.prompt(
                        "Enter the starting index of the packed message", type=int
                    )
                )
                j = int(
                    click.prompt("Enter the length of the packed message", type=int)
                )
                obj = msgpack.unpackb(packedmsg[i : i + j])
                click.echo(f"\t{obj}")

            elif ans == "quit":
                break

        except KeyboardInterrupt as e:
            raise e
        except Exception as e:
            click.echo(f"Command failed: {e}")


def console_test():
    click.echo("Starting!")
    msg = ""
    try:
        while True:
            msg += click.getchar()
            # click.echo(msg)
            if "\r" in msg:
                cmd, msg = msg.split("\r")
                click.echo(f"Entered: {cmd}")
    finally:
        print("Done.")


def advanced_console_test():
    packer = Packer()
    kthread = KeyboardThreadChar(lambda txt: string_to_packet(packer, txt))
    while True:
        obj = None
        while not txQueue.empty() and obj is not None:  # and PSer is not None:
            obj = txQueue.get_nowait()
            click.echo(f">TX: {obj}")

        # show console indicator if it was replaced with message contents
        print_console = obj is not None
        obj = None
        try:
            for obj in iter(lambda: rxQueue.get(block=True, timeout=0.01), None):
                if obj is None:
                    break
                click.echo(f"~RX:{obj}")  # DEBUG
                print_console = True
        except queue.Empty:
            pass
        if print_console:
            click.echo("> ", nl=False)
            print_console = False
        if kthread.exc_info:
            if isinstance(kthread.exc_info[1], KeyboardInterrupt):
                print("Exiting Console.")
                break
            raise kthread.exc_info[1].with_traceback(kthread.exc_info[2])


if __name__ == "__main__":
    # serial_test_msgpack_orig()
    serial_test_msgpack()
    # console_test()
    # advanced_console_test()
