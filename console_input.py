import contextlib
import sys
import threading

import click


class ThreadedKeyboardInput(threading.Thread):
    def __init__(self, newline_cbk=None, name="keyboard-input-thread"):
        self.newline_cbk = newline_cbk
        super(ThreadedKeyboardInput, self).__init__(name=name, daemon=True)
        self.val = ""
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
                        ThreadedKeyboardInput.bksp()
                        continue
                    self.val += c
                    self.parse_lines()  # Detects newlines (complete commands) and prints them
        except (Exception, KeyboardInterrupt):
            self.exc_info = sys.exc_info()

    def toggle_silence(self):
        # If echo was on, clear line. If echo was off, print silenced text with another console prompt
        if self.echo_state:
            # Clear current line w/ ANSI escape seq: CSI/Control Sequence Introducer (starts w/ "\e[" or "\033"):
            # print("\033[1A", end="\r")    # 1A  A=Cursor up, 1=1 line
            # 2K  K=erase in line, 0 or none=cursor->end, 1=start->cursor, 2=whole line.
            print("\033[2K", end="\r")
        else:
            click.echo(f"> {self.val}", nl=False)
        self.echo_state = not self.echo_state

    def parse_lines(self):
        if "\r" not in self.val and "\n" not in self.val:
            return
        click.echo("\r")
        lines = self.val.splitlines()  # strip terminating characters
        for line in lines:
            if self.newline_cbk is not None:
                self.newline_cbk(line)
            else:
                click.echo(f"Entered: {line}")
        click.echo("> ", nl=False)
        self.val = ""

    def bksp():
        # \b: bksp \033[K : backspace & erase chars after cursor
        click.echo("\033\b\033[K", nl=False)

    def join(self):
        threading.Thread.join(self)
        if self.exc_info:
            msg = f"Thread '{self.getName()}' threw an exception: {self.exc[1]}"
            click.echo(f"exception: \r{msg}")  # (msg)
            new_exc = Exception(msg)
            raise new_exc.with_traceback(self.exc[2])


def threaded_console_example():
    import queue
    from struct import pack, unpack

    rxQueue = queue.Queue()
    txQueue = queue.Queue()

    def pack_string(txt):
        data = bytes(txt, "utf-8")
        txQueue.put(pack(f"{len(data)}s", data))

    def unpack_string(txtdata):
        unpacked = unpack(f"{len(txtdata)}s", txtdata)
        return unpacked[0].decode("utf-8","backslashreplace")


    kthread = ThreadedKeyboardInput(pack_string)
    while True:
        obj = 0
        first_print = True  # silence input while printing
        with contextlib.suppress(queue.Empty):  # and PSer is not None:
            for obj in iter(lambda: txQueue.get(block=True, timeout=0.01), None):
                if obj is None:
                    continue
                if first_print:
                    kthread.toggle_silence()
                    first_print = False
                rxQueue.put(obj)
                click.echo(f">TX: {obj}\r")

        # reset console input
        if not first_print:
            kthread.toggle_silence()

        obj = None
        first_print = True
        with contextlib.suppress(queue.Empty):
            for obj in iter(lambda: rxQueue.get(block=True, timeout=0.01), None):
                if obj is None:
                    continue
                if first_print:
                    kthread.toggle_silence()
                    first_print = False
                click.echo(f"~RX:{obj} -> {unpack_string(obj)}\r")

        if not first_print:
            kthread.toggle_silence()
        if kthread.exc_info:
            if isinstance(kthread.exc_info[1], KeyboardInterrupt):
                print("Exiting Console.")
                break
            raise kthread.exc_info[1].with_traceback(kthread.exc_info[2])


if __name__ == "__main__":
    threaded_console_example()
