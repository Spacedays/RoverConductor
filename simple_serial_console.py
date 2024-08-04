#### A simple keyboard interface to the rover


import logging
import queue
import sys
import threading
from logging.handlers import (
    QueueHandler,  # LATER -- use & test logs
    QueueListener,
)

import click
import msgpack

from pico_interface import MsgPacketize, ControlPacket, PicoSerial

logQue = queue.Queue(-1)  # no max size; if max size, prep for queue full exception
log_queue_handler = QueueHandler(logQue)  # accepts logging messages to allow seperate threads
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
        self.start()
        self.val = ""
        self.silent_val = ""
        self.echo_state = True
        self.prompting = True
        click.echo("> ", nl=False)

    def run(self):
        while True:
            # self.input_cbk(self, readchar.readchar())  # waits to get single character
            c = click.getchar()
            self.val += c
            if self.echo_state:
                click.echo(c, nl=False)
            else:
                self.silent_val += c
            self.update_inp()  # gets a character and delivers it to input callback

    def update_inp(self):
        if "\r" in self.val or "\n" in self.val:
            sys.stdout.write("\033[2K\033[1G")  # clear line and reset to start
            self.printlines()

    def toggle_silence(self):
        click.echo(self.silent_val)
        self.silent_val = ""
        self.echo_state = ~self.echo_state

    def printlines(self):
        lines = self.val.splitlines()
        for line in lines:
            if self.newline_cbk:
                self.newline_cbk(line)
            else:
                click.echo(f"Entered: {line}")
        self.prompting = True
        click.echo("> ", nl=False)
        self.val = ""


def string_to_packet(packer:msgpack.Packer, text: str, base_packet: ControlPacket = None):
    click.echo(f"Writing {text} into control packet")
    msg = ControlPacket() if base_packet is None else base_packet
    msg.s = text
    bytemsg = MsgPacketize(packer, msg.to_iter())
    click.echo(''.join(oct(byte)[1:] for byte in bytemsg))
    # txQueue.put(MsgPacketize(packer, msg.to_iter()))
    # txQueue.put(msgpack.packb(MPZPacket(PICO_RX_INDEX, base_packet)))
    # return base_packet



def serial_test_msgpack():
    unpacker = msgpack.Unpacker()
    packer = msgpack.Packer()
    # PSer = PicoSerial(logQue)
    # kthread = KeyboardThreadChar(lambda txt: PSer.write(string_to_packet(txt)))  # start keyboard input thread
    kthread = KeyboardThreadChar(lambda txt: string_to_packet(packer,txt))
    PSer = None
    while True:
        for o in unpacker:
            rxQueue.put(o)
        if unpacker.tell() > 0:
            kthread.toggle_silence()
            click.echo("\n")
            # sys.stdout.write("\033[2K\033[1G")  # clear line and reset to start
            click.echo("Tell")

            for unpacked in rxQueue:
                print(unpacked)

            kthread.toggle_silence()

        # if len(PSer.port.in_waiting) > 0:
        #     buf = PSer.port.read_all()
        #     unpacker.feed(buf)

        obj = None
        while not txQueue.empty() and obj is not None:  # and PSer is not None:
            obj = txQueue.get_nowait()
            click.echo(obj)
            # PSer.write(obj)

        obj = None
        print_console = obj is not None
        while not rxQueue.empty() and obj is not None and PSer is not None:
            obj = rxQueue.get_nowait()
            click.echo(obj)
        if print_console:
            click.echo("> ", nl=False)


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


if __name__ == "__main__":
    # serial_test_msgpack_orig()
    serial_test_msgpack()
    # console_test()
