#!/usr/bin/env python3

import asyncio
import math
import os
import signal
import subprocess
import sys
import threading
import time
from evdev import InputDevice, ecodes, ff, list_devices

import gamepad

def connect(): # asyncronus read-out of events
        xbox_path = None
        remote_control = None
        devices = [InputDevice(path) for path in list_devices()]
        print('Connecting to xbox controller...')
        for device in devices:
            if str.lower(device.name) == 'xbox wireless controller':
                xbox_path = str(device.path)
                remote_control = gamepad.gamepad(file = xbox_path)
                remote_control.rumble_effect = 2
                return remote_control
        return None


def is_connected(): # asyncronus read-out of events
    path = None
    devices = [InputDevice(path) for path in list_devices()]
    for device in devices:
        if str.lower(device.name) == 'xbox wireless controller':
            path = str(device.path)
    if(path == None):
        print('Xbox controller disconnected!!')
        return False
    return True


async def read_gamepad_inputs():
    global remote_control
    global head_light_flag
    print("Ready to drive!!")
    # turn_sound = SoundPlayer("/home/pi/xbox-raspberrypi-rover/soundfiles/turn-signal.mp3", card)
    # horn_sound = SoundPlayer("/home/pi/xbox-raspberrypi-rover/soundfiles/Horn.mp3", card)        

    while is_connected() and remote_control.button_b == False:
        #print(" trigger_right = ", round(remote_control.trigger_right,2),end="\r")
        x = round(remote_control.joystick_left_x,2)
        y = round(remote_control.joystick_left_y,2)
        angle = get_angle_from_coords(x,y)
        if angle > 180:
            angle = 360 - angle
        #print("x:", x, " y:", y, " angle: ",angle,end="\r")
        # turn_head(angle)
        direction = get_motor_direction(x,y)
        #print("x:", x, " y:", y, " direction: ",direction,end="\r")
        drive_motor(direction,y)

        if round(remote_control.trigger_right,2) > 0.0:
            horn_sound.play(1.0)
            led.blue()
        elif round(remote_control.trigger_left,2) > 0.0:
            led.cyan()
        elif remote_control.bump_left:
            turn_sound.play(1.0)
            led.turn_left(5)
        elif remote_control.bump_right:
            turn_sound.play(1.0)
            led.turn_right(5)
        elif remote_control.dpad_up:
            remote_control.dpad_up = False
        elif remote_control.dpad_left:
            remote_control.dpad_left = False
        elif remote_control.dpad_right:
            remote_control.dpad_right = False
        elif remote_control.button_a:
            remote_control.button_a = False
        elif head_light_flag == False:
            led.both_off()
            led_strip.colorWipe(strip, Color(0,0,0))
            if turn_sound.isPlaying():
                turn_sound.stop()

        await asyncio.sleep(100e-3) #100ms
    return

async def removetasks(loop):
    tasks = [t for t in asyncio.all_tasks() if t is not
             asyncio.current_task()]

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
