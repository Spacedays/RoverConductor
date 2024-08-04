#!/usr/bin/env python3

import asyncio
import math
import os
import signal
import subprocess
import sys
import threading
import time
import queue
import logging
from logging.handlers import QueueHandler, QueueListener

# import RPi.GPIO as GPIO
from evdev import InputDevice, ecodes, ff, list_devices

import msgpack
from io import BytesIO

from gamepad import Gamepad
from pico_interface import PicoSerial, ControlPacket, MPZPacket, PICO_RX_INDEX

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


def setup():
    # TODO:logging setup
    PSer = PicoSerial(logQue)

    pass
    # motor.setup()
    # led.setup()
    # turn.turn_middle()
    # led.green()


global remote_control


# TODO: Refactor to move gamepad functions into gamepad.py
def connect():  # asyncronus read-out of events
    global remote_control
    xbox_path = None
    remote_control = None
    devices = [InputDevice(path) for path in list_devices()]
    print("Connecting to xbox controller...")
    for device in devices:
        if str.lower(device.name) == "xbox wireless controller":
            xbox_path = str(device.path)
            remote_control = Gamepad(file=xbox_path)
            remote_control.rumble_effect = 2
            return remote_control
    return None


def is_connected():  # asyncronus read-out of events
    path = None
    devices = [InputDevice(path) for path in list_devices()]
    for device in devices:
        if str.lower(device.name) == "xbox wireless controller":
            path = str(device.path)
    if path is None:
        print("Xbox controller disconnected!!")
        return False
    return True


async def read_gamepad_inputs():
    print("Ready to go!")

    while is_connected() and not remote_control.button_b:
        packet = remote_control.make_control_packet()
        print(packet)
        # # print(" trigger_right = ", round(remote_control.trigger_right,2),end="\r")
        # x = round(remote_control.joystick_left_x, 2)
        # y = round(remote_control.joystick_left_y, 2)
        # angle = get_angle_from_coords(x, y)
        # if angle > 180:
        #     angle = 360 - angle
        # print(f"x: {x} y: {y} angle: {angle:.2f}", end="\r")

        # if round(remote_control.trigger_right, 2) > 0.0:
        #     # print(f'Rt {remote_control.trigger_right}')
        #     pass
        #     # led.blue()
        # elif round(remote_control.trigger_left, 2) > 0.0:
        #     # print(f'Lt {remote_control.trigger_left}')
        #     pass
        #     # led.cyan()
        # elif remote_control.bump_left:
        #     print("Lb\n")
        # elif remote_control.bump_right:
        #     print("Rb\n")
        # elif remote_control.dpad_up:
        #     remote_control.dpad_up = False
        # elif remote_control.dpad_left:
        #     remote_control.dpad_left = False
        # elif remote_control.dpad_right:
        #     remote_control.dpad_right = False
        # elif remote_control.button_a:
        #     remote_control.button_a = False

        await asyncio.sleep(100e-3)  # 100ms
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

    card = 1  # (default)
    strip = None
    signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)

    for s in signals:
        loop.add_signal_handler(s, lambda s=s: asyncio.create_task(shutdown_signal(s, loop)))
    try:
        setup()
        remote_control = connect()
        if remote_control is None:
            print("Please connect an Xbox controller then restart the program!")
            sys.exit()
        print("Connected!")

        # strip = led_strip.setup_led()

        # led_threading=threading.Thread(target=led_thread)     #Define a thread for ws_2812 leds
        # led_threading.setDaemon(True)                         #'True' means it is a front thread,it would close when the mainloop() closes
        # led_threading.start()                                 #Thread starts

        # tasks = set()#(pad,vibe,readpad))
        # tasks.add(asyncio.create_task(remote_control.read_gamepad_input()))
        # tasks.add(asyncio.create_task(remote_control.rumble()))
        # tasks.add(asyncio.create_task(read_gamepad_inputs()))

        # asyncio.run(remote_control.read_gamepad_input())
        await asyncio.gather(remote_control.read_gamepad_input(), read_gamepad_inputs())
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
        # led.red()

    except Exception as e:
        print("Error occured " + str(e))
    finally:
        if remote_control is not None:
            remote_control.power_on = False
            remote_control.erase_rumble()

        # if(strip != None):
        #     led_strip.colorWipe(strip, Color(0,0,0))

        print("Closing async loop..")
        # led.both_off()
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

# if __name__ == "__main__":
#     try:
#         loop = asyncio.get_event_loop()
#     except:
#         loop = asyncio.new_event_loop()

#     card = 1 #(default)
#     strip = None
#     signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)


#     for s in signals:
#         loop.add_signal_handler(
#             s, lambda s=s: asyncio.create_task(shutdown_signal(s, loop)))
#     try:
#         setup()
#         remote_control = connect()
#         print('Connected!')
#         if(remote_control == None):
#             print('Please connect an Xbox controller then restart the program!')
#             sys.exit()

#         # strip = led_strip.setup_led()

#         # led_threading=threading.Thread(target=led_thread)     #Define a thread for ws_2812 leds
#         # led_threading.setDaemon(True)                         #'True' means it is a front thread,it would close when the mainloop() closes
#         # led_threading.start()                                 #Thread starts

#         tasks = set()#(pad,vibe,readpad))
#         pad = asyncio.create_task(remote_control.read_gamepad_input())
#         tasks.add(pad)
#         vibe = asyncio.create_task(remote_control.rumble())
#         tasks.add(vibe)
#         readpad = asyncio.create_task(read_gamepad_inputs())
#         tasks.add(readpad)
#         for task in tasks:
#             task.add_done_callback(tasks.discard)

#         # tasks = [remote_control.read_gamepad_input(), remote_control.rumble(), read_gamepad_inputs()]
#         loop.run_until_complete(asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED))
#         # led.red()
#         loop.run_until_complete(removetasks(loop))  #TODO: replace?

#         # print('gamepad')
#         # print('rumble')
#         # print("Starting")

#         # motor.destroy()
#     except Exception as e:
#         print("Error occured " + str(e))
#     finally:
#         if remote_control != None:
#             remote_control.power_on = False
#             remote_control.erase_rumble()

#         # if(strip != None):
#         #     led_strip.colorWipe(strip, Color(0,0,0))

#         print("Closing async loop..")
#         # led.both_off()
#         try:
#             pending = asyncio.all_tasks()
#             loop.run_until_complete(asyncio.gather(*pending))
#         except:
#             print('No tasks to close')
#         print("Done..")


# # if __name__ == "__main__":
# #     # loop = asyncio.get_event_loop()
# #     # loop.run_until_complete(main())
# #     asyncio.run(main())
