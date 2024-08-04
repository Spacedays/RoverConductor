## Context
import queue
import logging
from logging.handlers import QueueHandler, QueueListener    #LATER -- use & test logs
import msgpack
from pico_interface import PicoSerial, ControlPacket, MPZPacket, PICO_RX_INDEX

## Console libs
import sys
import select
import tty
import termios
import threading
import click


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


#### serial_test_msgpack_orig: a partially working serial terminal that should be able to receive MsgPack messages
class KeyboardThreadChar(threading.Thread):
    def __init__(self, newline_cbk=None, name="keyboard-input-thread"):
        self.newline_cbk = newline_cbk
        super(KeyboardThreadChar, self).__init__(name=name, daemon=True)
        self.start()
        self.val = ""
        self.silent_val = ""
        self.echo_state = True
        self.prompting = True
        click.echo('> ', nl=False)

    def run(self):
        while True:
            # self.input_cbk(self, readchar.readchar())  # waits to get single character
            c = click.getchar()
            self.val += c
            if self.echo_state: click.echo(c,nl=False)
            else: self.silent_val += c
            self.update_inp()   # gets a character and delivers it to input callback

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
        click.echo('> ', nl=False)
        self.val = ""

def send_message(msg):
    click.echo(msg)

def serial_test_msgpack_orig():
    unpacker = msgpack.Unpacker()
    inp = ""
    # PSer = PicoSerial(logQue)
    kthread = KeyboardThreadChar()  # start keyboard input thread

    tmp = None
    term_fd = sys.stdin.fileno()
    orig_term = termios.tcgetattr(term_fd)
    silent_term = termios.tcgetattr(term_fd)
    while True:
        for o in unpacker:
            rxQueue.put(o)
        if unpacker.tell() > 0:
            print('Tell')
            tmp = kthread.val  # TODO lock while looping?
            sys.stdout.write("\033[2K\033[1G")  # clear line and reset to start

            # silence terminal input while prining
            silent_term[3] = silent_term[3] & ~termios.ECHO  # lflags
            try:
                termios.tcsetattr(term_fd, termios.TCSADRAIN, silent_term)
            finally:
                for unpacked in rxQueue:
                    print(unpacked)
                termios.tcsetattr(term_fd, termios.TCSADRAIN, orig_term)

            # if newline was entered while printing messages
            if tmp not in kthread.val:
                print(tmp)
            print(kthread.val)

        # if len(PSer.port.in_waiting) > 0:
        #     buf = PSer.port.read_all()
        #     unpacker.feed(buf)

        obj = None
        while not txQueue.empty() and obj is not None:
            obj = txQueue.get_nowait()
            PicoSerial.write(obj)
            

        obj = None
        print_console = obj is not None
        while not rxQueue.empty() and obj is not None:
            obj = rxQueue.get_nowait()
            print(obj)
        if print_console:
            print(">")

        kthread.val = ""


#### basic_serial_test: input() & threading-based console. The console part works, the pico part kind of works.

class KeyboardThread(threading.Thread):
    def __init__(self, input_cbk=None, name="keyboard-input-thread"):
        self.input_cbk = input_cbk
        super(KeyboardThread, self).__init__(name=name, daemon=True)
        self.start()
        self.val = ""

    def run(self):
        while True:
            self.input_cbk(self, input())  # waits to get input + Return

def update_inp(kthread, val):
    kthread.val = val
    print(f"Entered: {val}")

def basic_serial_test():
    kthread = KeyboardThread(update_inp)  # start keyboard input thread

    inp = ""
    PSer = PicoSerial(logQue)
    print("Getting input...")
    while inp != "stop":
        if inp.strip() != "":
            PSer.write(inp.strip().encode())
            # while PSer.port.in_waiting > 0:
            #     lines = bytes.decode(PSer.readlines())
            #     print(lines)
            #     time.sleep(0.05)
            print(PSer.read_all())  # TESTME - does this repalce .in_waiting?
        else:
            # if not prevFromSerial:
            print("> ", end="")
        inp = kthread.val
        kthread.val = ""
    print("Done")


### Select-based console: Untested. No idea if it works.
# class NonBlockingConsole(object):
#     def __init__(self):
#         self.secretdata = ""
#         self.line = None

#     def __enter__(self):
#         self.old_settings = termios.tcgetattr(sys.stdin)
#         tty.setcbreak(sys.stdin.fileno())
#         return self

#     def __exit__(self, type, value, traceback):
#         termios.tcsetattr(sys.stdin, termios.TCSADRAIN, self.old_settings)


#     def get_data(self):
#         if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
#             return sys.stdin.read(1)
#         return False
    
#     def get_data(self):
#         if select.select([sys.stdin], [], [], 0) == ([sys.stdin], [], []):
#             return sys.stdin.read(1)
#         return False
    
#     def toggle_secret(self):
#         pass