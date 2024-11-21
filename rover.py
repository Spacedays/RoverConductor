#!/usr/bin/env python3

import asyncio
from dataclasses import dataclass
import math
import signal
import sys
import queue
import logging
from logging.handlers import QueueHandler, QueueListener

# import RPi.GPIO as GPIO
from evdev import InputDevice, ecodes, ff, list_devices

import msgpack
from io import BytesIO

from gamepad import Gamepad
from pico_interface import PicoSerial, ControlPacket

# import led
# import led_strip
# import motor
# import turn
# from soundplayer import SoundPlayer

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


# status = 1  # Motor rotation
# forward = 1  # Motor forward
# backward = 0  # Motor backward

# left_spd = 100  # Speed of the car
# right_spd = 100  # Speed of the car

head_light_flag = False


# def setup():
#     # TODO:logging setup
#     PSer = PicoSerial(logQue)

#     pass
#     # motor.setup()
#     # led.setup()
#     # turn.turn_middle()
#     # led.green()


# TODO: Refactor to move gamepad functions into gamepad.py


async def read_gamepad_inputs(remote_control):
    print("Ready to go!")

    while remote_control.is_connected() and not remote_control.button_b:
        packet = remote_control.make_control_packet()
        print(packet)  # DEBUG
        await asyncio.sleep(50e-3)  # 50ms
    print("\ndone")
    return


async def removetasks(loop):
    tasks = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]

    for task in tasks:
        # skipping over shielded coro still does not help
        if task._coro.__name__ == "cant_stop_me":
            continue
        task.cancel()

    print("Cancelling outstanding tasks")
    await asyncio.gather(*tasks, return_exceptions=True)
    loop.stop()


async def shutdown_signal(signal, loop):
    print(f"Received exit signal {signal.name}...")
    await removetasks(loop)


async def main():
    # if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
    except Exception:
        loop = asyncio.new_event_loop()

    signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)

    remote_control = None

    for s in signals:
        loop.add_signal_handler(s, lambda s=s: asyncio.create_task(shutdown_signal(s, loop)))
    try:
        # setup()
        remote_control = Gamepad()
        if not remote_control:
            print("Please connect an Xbox controller then restart the program!")
            sys.exit()
        print("Connected!")

        # tasks = set()#(pad,vibe,readpad))
        # tasks.add(asyncio.create_task(remote_control.read_gamepad_input()))
        # tasks.add(asyncio.create_task(remote_control.rumble()))
        # tasks.add(asyncio.create_task(read_gamepad_inputs()))

        # asyncio.run(remote_control.read_gamepad_input())
        await asyncio.gather(
            remote_control.read_gamepad_input(), read_gamepad_inputs(remote_control)
        )
        # FIXME - shutdown after read input exit

        # for task in tasks:
        #     task.add_done_callback(tasks.discard)
        # try:
        #     async with asyncio.TaskGroup() as tg:
        #         t1 = tg.create_task(asyncio.create_task(remote_control.read_gamepad_input()))
        #         # t2 = tg.create_task(asyncio.create_task(remote_control.rumble()))
        #         t3 = tg.create_task(asyncio.create_task(read_gamepad_inputs()))
        #     print('done with tasks')
        # except Exception as e:
        #     print('~caught exception:')
        #     raise e

        # tasks = [remote_control.read_gamepad_input(), remote_control.rumble(), read_gamepad_inputs()]

    except Exception as e:
        print(f"Error occured: {e}")
    finally:
        if remote_control:
            remote_control.listening = False
            remote_control.erase_rumble()

        print("Closing async loop..")

        try:
            pending = asyncio.all_tasks()
            loop.run_until_complete(asyncio.gather(*pending))
        except Exception:
            print("No tasks to close")
        print("Done..")


if __name__ == "__main__":
    asyncio.run(main())
    # serial_test()
    # serial_test_msgpack()
