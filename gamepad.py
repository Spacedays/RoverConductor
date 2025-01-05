#!/usr/bin/python3

import asyncio
from pyjoystick import Key, Joystick

import sys

# if evdev Gamepad is used: (Linux only)
if sys.platform == "linux":
    from evdev import InputDevice, ecodes, ff, list_devices
    # from evdev import InputDevice, categorize, ecodes

from pico_interface import ControlPacket


TRIGGER_MAX = 1023  # LATER: configure this
JOY_MAX = 0x7FFF #0xFFFF / 2
# JOY_MID = 0xFFFF / 2
JOY_DEADZONE = 0

#The gamepad values are expected to be a -1 to 1 or 0 to 1 float. They should be mapped to integers before transmitting.
class GamepadState:
    def __init__(self, file=None):
        self.joystick_left_y = 0
        self.joystick_left_x = 0
        self.joystick_right_x = 0
        self.joystick_right_y = 0
        self.trigger_right = 0
        self.trigger_left = 0
        self.button_x = False
        self.button_y = False
        self.button_b = False
        self.button_a = False
        self.dpad_up = False
        self.dpad_down = False
        self.dpad_left = False
        self.dpad_right = False
        self.bump_left = False
        self.bump_right = False

    def connect(self):
        pass

    def is_connected(self):
        # FIXME - determine connectivity or catch elsewhere
        return True

    def __bool__(self):
        return self.is_connected()

    def handle_key_event(self, key: Key):
        # print("KEY")
        if key.keytype == Key.AXIS:
            match key.number:
                case 0:
                    self.joystick_left_x = key.value
                case 1:
                    self.joystick_left_y = key.value
                case 2:
                    self.trigger_left = key.value
                case 5:
                    self.trigger_right = key.value
            # print(self.joystick_left_x, self.joystick_left_y, self.trigger_left, self.trigger_right)
        elif key.keytype == Key.BUTTON:
            # return
            match key.number:
                case 0:
                    self.button_a = key.value
                case 1:
                    self.button_b = key.value
                case 2:
                    self.button_x = key.value
                case 3:
                    self.button_y = key.value
                case 4:
                    self.bump_left = key.value
                case 5:
                    self.bump_right = key.value

    def make_control_packet(self) -> ControlPacket:
        return ControlPacket(
            self.button_a,
            # self.button_x,
            # self.button_y,
            self.button_b,
            # self.bump_left,
            # self.bump_right,
            # self.trigger_left,
            self.trigger_right,
            self.joystick_left_x,  # * 32,767,
            self.joystick_left_y,  # * 32,767,
            # self.joystick_right_x,
            # self.joystick_right_y,
        )


# Evdev gamepad (Linux only)
if sys.platform == "linux": # and False:

    class Gamepad(GamepadState):
        def __init__(self, file=None):
            super().__init__()
            # self.event_value = 0
            self.listening = False
            self.device_file = InputDevice(file) if file else None
            self.rumble_effect = 0
            self.effect1_id = 0  # light rumble, played continuously
            self.effect2_id = 0  # strong rumble, played once
            self.load_effects()

            self.connect()

        def connect(self):  # asyncronus read-out of events
            if self.device_file:
                print("Controller connected.")
                return
            devices = [InputDevice(path) for path in list_devices()]
            print("Connecting to xbox controller...")
            for device in devices:
                if "x-box" in str.lower(device.name) or "xbox" in str.lower(device.name):
                    # xbox_path = str(device.path)
                    self.device_file = device
                    self.listening = True
                    self.rumble_effect = 2
                    print("Controller Connected.")
                    return True
                else:
                    print(device.name)
            print("No controller found.")
            return False

        def is_connected(self):  # asyncronus read-out of events
            path = None
            devices = [InputDevice(path) for path in list_devices()]
            for device in devices:
                if "x-box" in str.lower(device.name) or "xbox" in str.lower(device.name):
                    path = str(device.path)
            if path is None:
                print("Xbox controller disconnected!!")
                self.listening = False
                return False
            self.listening = True
            return True

        def __bool__(self):
            return self.listening and self.is_connected()

        def load_effects(self):
            if not self.listening:
                return
            # effect 1, light rumble
            rumble = ff.Rumble(strong_magnitude=0x0000, weak_magnitude=0x500)
            duration_ms = 300
            effect = ff.Effect(
                ecodes.FF_RUMBLE,
                -1,
                0,
                ff.Trigger(0, 0),
                ff.Replay(duration_ms, 0),
                ff.EffectType(ff_rumble_effect=rumble),
            )
            self.effect1_id = self.device_file.upload_effect(effect)
            # effect 2, strong rumble
            rumble = ff.Rumble(strong_magnitude=0xC000, weak_magnitude=0x0000)
            duration_ms = 200
            effect = ff.Effect(
                ecodes.FF_RUMBLE,
                -1,
                0,
                ff.Trigger(0, 0),
                ff.Replay(duration_ms, 0),
                ff.EffectType(ff_rumble_effect=rumble),
            )
            self.effect2_id = self.device_file.upload_effect(effect)

        # TODO: handle deadzones, (?) calibration
        async def read_gamepad_input(self):  # asyncronus read-out of events
            print("input loop")  # DEBUG
            print(self.device_file)  # DEBUG
            if not self.device_file:
                return
            try:
                async for event in self.device_file.async_read_loop():
                    # if event.type == ecodes.EV_KEY:
                    #     print(categorize(event))
                    # print("areadloop")
                    if not (self.listening):  # stop reading device when power_on = false
                        print("power off")
                        break
                    # print(f'{event.type:<8} {event.code:<8} {event.value:<8}')
                    # print(f'{event.type:<8b} {event.code:<8b} {event.value:<8b}\n')

                    if event.type == 1:  # type is button
                        match event.code:
                            case 304:  # button "A"
                                self.button_a = bool(event.value)
                            case 307:  # button "X"
                                self.button_x = bool(event.value)
                            case 308:  # button "Y"
                                self.button_y = bool(event.value)
                            case 305:  # button "B"
                                self.button_b = bool(event.value)
                            case 311:  # bumper "right"
                                self.bump_right = bool(event.value)
                            case 310:  # bumper "left"
                                self.bump_left = bool(event.value)
                            case 172:  # home key
                                pass
                            case 317:  # left stick pressed
                                pass
                            case 318:  # right stick pressed
                                pass

                    elif event.type == 3:  # type is analog trigger or joystick
                        match event.code:
                            # left joystick x-axis
                            case 0:
                                # print(f"Case 0: {event.value}")
                                self.joystick_left_x = event.value / JOY_MAX
                            # left joystick y-axis - inverted so -y is down
                            case 1:
                                # print(f"Case 1: {event.value}")
                                self.joystick_left_y = event.value / JOY_MAX
                            # elif event.code == 2:  # left trigger
                            case 2:
                                self.trigger_left = event.value / TRIGGER_MAX
                            case 3:
                                self.joystick_right_x = event.value / JOY_MAX
                            # right joystick y-axis
                            case 4:
                                self.joystick_right_y = event.value / JOY_MAX
                            # right joystick x-axis
                            # elif event.code == 5:  # right trigger
                            case 5:
                                self.trigger_right = event.value / TRIGGER_MAX
                            # elif event.code == 16:  # right trigger
                            case 16:
                                if event.value == -1:
                                    self.dpad_left = True
                                    self.dpad_right = False
                                elif event.value == 1:
                                    self.dpad_left = False
                                    self.dpad_right = True
                                else:
                                    self.dpad_left = False
                                    self.dpad_right = False
                            # elif event.code == 17:  # left trigger
                            case 17:
                                if event.value == -1:
                                    self.dpad_up = True
                                    self.dpad_down = False
                                elif event.value == 1:
                                    self.dpad_up = False
                                    self.dpad_down = True
                                else:
                                    self.dpad_up = False
                                    self.dpad_down = False
                            case _:
                                ...
                                # print(event.code, event.value)
            except OSError as e:
                self.device_file = None
                raise e

        async def rumble(self):  # asyncronus control of force feed back effects
            repeat_count = 1
            while self.listening:
                if self.rumble_effect == 1:
                    self.device_file.write(ecodes.EV_FF, self.effect1_id, repeat_count)
                elif self.rumble_effect == 2:
                    self.device_file.write(ecodes.EV_FF, self.effect2_id, repeat_count)
                    self.rumble_effect = 0  # turn of effect in order to play effect2 only once
                await asyncio.sleep(0.2)

        def erase_rumble(self):
            self.device_file.erase_effect(self.effect1_id)


    async def controller_test(gamepad: Gamepad):
        while gamepad.is_connected() and not gamepad.button_b:
            print()
            print(
                f"ljx:{gamepad.joystick_left_x:4.2f} ljy:{gamepad.joystick_left_y:4.2f} rjx:{gamepad.joystick_right_x:4.2f} rjy:{gamepad.joystick_right_y:4.2f} lt:{gamepad.trigger_left:4.2f} rt:{gamepad.trigger_right:4.2f}",
                end="\r",
            )
            # print("\033[A", end='') # move up 1 line
            await asyncio.sleep(100e-3)  # 100ms
        print("\ndone")
        return


    if __name__ == "__main__":
        import signal
        import sys

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
            for s in signals:
                loop.add_signal_handler(s, lambda s=s: asyncio.create_task(shutdown_signal(s, loop)))

            remote_control = None
            try:
                # setup()
                remote_control = Gamepad()
                if not remote_control:
                    print("Please connect an Xbox controller then restart the program!")
                    sys.exit()
                print("Connected!")

                # asyncio.run(remote_control.read_gamepad_input())
                await asyncio.gather(remote_control.read_gamepad_input(), controller_test(remote_control))
            except Exception as e:
                print(f"Error occured {e}")
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

        asyncio.run(main())
