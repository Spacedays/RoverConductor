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

from pico_interface import (
    PACKETDELIM,
    LEN_SEP,
    ControlPacket,
    PacketMsg,
    PicoSerial,
)

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
                    c = click.getchar(echo=self.echo_state)  # echo=False)
                    # terminal control: ANSI escape codes
                    # destructive bksp \x7F (non-std) and \b (AKA'\x08) seem to behave similarly (no erase, just cursor movement).
                    #   --> clear line after cursor
                    if c == "\x7f":
                        # delete previous char and clear the line after the cursor
                        self.val = self.val[:-1]
                        click.echo(
                            "\033\b\033[K", nl=False
                        )  # \b: bksp \033[K : backspace & erase chars after cursor
                        continue
                    self.val += c
                    self.parse_lines()  # Detects newlines (complete commands) and prints them
        except (Exception, KeyboardInterrupt):
            self.exc_info = sys.exc_info()

    def toggle_silence(self):
        # If echo was off, print silenced text
        if not self.echo_state:
            click.echo(self.val, nl=False)
        self.echo_state = not self.echo_state

    def parse_lines(self):
        if not ("\r" in self.val or "\n" in self.val):
            return

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
    click.echo(f"Writing {text} into control packet\r")
    msg = ControlPacket() if base_packet is None else base_packet
    msg.s = text  # bytes(text, "utf-8")
    bytemsg = PacketMsg(packer, msg.to_iter())
    # click.echo(f'Packed {msg} to\r\n\t{packer.pack(msg.to_iter())} as\r\n\t{bytemsg}')    #DEBUG
    txQueue.put(bytemsg)


def msgpack_console():
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
            print("-" * 78)
            click.echo(bytes(traceback.format_exc(), "utf-8"), err=True)
            # print(traceback.format_exc())
            click.echo(
                interactive_parse(
                    e.__cause__.args[0] if e and e.__cause__ and e.__cause__.args else e
                ),
                err=True,
            )
            # click.echo(click.wrap_text(e,initial_indent="",subsequent_indent="  ",preserve_paragraphs=True))
            # click.echo(mbytes, err=True)
            # click.echo("-"*78, err=True)
        # show console indicator if it was replaced with message contents
        print_console = False
        obj = None
        first_print = True
        try:
            for obj in iter(lambda: rxQueue.get(block=True, timeout=0.01), None):
                if obj is None:
                    continue
                if first_print:
                    kthread.toggle_silence()
                    # Clear current line w/ ANSI escape seq: CSI/Control Sequence Introducer (starts w/ "\e[" or "\033"):
                    # print("\033[1A", end="\r")    # 1A  A=Cursor up, 1=1 line
                    # 2K  K=erase in line, 0 or none=cursor->end, 1=start->cursor, 2=whole line.
                    print("\033[2K", end="\r")
                    first_print = False

                click.echo(f"~RX:{obj}\r")
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
    messages = re.split(PACKETDELIM, rx_bytes)  # does not include termseq separator in list
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
                rxQueue.put(
                    mbytes.decode("utf-8", "backslashreplace")
                )  # escapes control chars with backslash
            # Separator -> truncate message and process it later as a string
            elif idx > 0:
                # get first digit and use it as the message length
                m = re.search(b"([0-9]+)", mbytes)
                mlen = int(mbytes[: m.span()[1]])
                mstart = m.span()[1] + len(b"~")
                mbytes = mbytes[mstart : mstart + mlen]
                strmsg = mbytes[mstart + mlen :]
        except Exception as e:
            # click.echo("\r\nException raised while parsing string!",err=True)#\r\n\t{e}", err=True)
            print("\r\nException raised while parsing string!", end="\r\n")
            e.__cause__ = BaseException(mbytes)
            # root.exception("Logging - Failed reading string", e)  #TODO
            raise e

        if idx < 0:
            continue

        unpacker.feed(mbytes)
        rxQueue.put(mbytes)
        rxQueue.put(unpacker.unpack())
        if strmsg:
            rxQueue.put(strmsg.decode("utf-8", "backslashreplace"))


def interactive_parse(packedmsg: bytearray):
    click.echo(f"Parsing: {packedmsg}")
    if not click.confirm(f"Unpacking failed. Try again manually?\r{packedmsg}", default=False):
        return
    while True:
        try:
            ans = click.prompt(
                "What would you like to parse?",
                type=click.Choice(("int", "str", "strsearch", "bytestrsearch", "unpack", "quit")),
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
                click.echo(f"\t{packedmsg[i:i+j].decode("utf-8","backslashreplace")}")

            elif ans == "bytestrsearch":
                s = click.prompt("Enter the bytestring to eval & search: ")
                b = ast.literal_eval(s)
                click.echo(f"\t{packedmsg.find(b)}")

            elif ans == "unpack":
                click.echo()
                i = int(click.prompt("Enter the starting index of the packed message", type=int))
                j = int(click.prompt("Enter the length of the packed message", type=int))
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
            obj = txQueue.get(block=True, timeout=0.01)
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


def escchar_test():
    while True:
        click.echo("\r\n> ", nl=False)
        c = click.getchar()
        if c == "~":
            c = " "
            while c[-1] != "\n" and c[-1] != "\r":
                c += click.getchar(echo=True)
            click.echo()
        click.echo(str(bytes(c, "utf-8")))
        click.echo(f"\nchar: {c}\n")


if __name__ == "__main__":
    # serial_test_msgpack_orig()
    msgpack_console()
    # console_test()
    # advanced_console_test()
    # escchar_test()
