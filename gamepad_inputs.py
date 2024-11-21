from inputs import get_gamepad
import math
import threading
import time

from pico_interface import ControlPacket

class XboxController(object):
    MAX_TRIG_VAL = math.pow(2, 8)
    MAX_JOY_VAL = math.pow(2, 15)

    def __init__(self, delay_start=False):

        self.LeftJoystickY = 0
        self.LeftJoystickX = 0
        self.RightJoystickY = 0
        self.RightJoystickX = 0
        self.LeftTrigger = 0
        self.RightTrigger = 0
        self.LeftBumper = 0
        self.RightBumper = 0
        self.A = 0
        self.X = 0
        self.Y = 0
        self.B = 0
        self.LeftThumb = 0
        self.RightThumb = 0
        self.Back = 0
        self.Start = 0
        self.LeftDPad = 0
        self.RightDPad = 0
        self.UpDPad = 0
        self.DownDPad = 0

        self.active = not delay_start
        self._monitor_thread = threading.Thread(target=self._monitor_controller, args=())
        self._monitor_thread.daemon = True
        if not delay_start:
            self._monitor_thread.start()


    def read(self): # return the buttons/triggers that you care about in this methode
        # x = self.LeftJoystickX
        # y = self.LeftJoystickY
        # a = self.A
        # b = self.X # b=1, x=2
        # rb = self.RightBumper
        return [self.LeftJoystickX, self.LeftJoystickY, self.A, self.X, self.RightBumper]


    def _monitor_controller(self):
        #TODO: handle disconnect
        while self.active:
            events = get_gamepad()
            for event in events:
                match event.code:
                    case 'ABS_Y':
                        self.LeftJoystickY = event.state / XboxController.MAX_JOY_VAL # normalize between -1 and 1
                    case 'ABS_X':
                        self.LeftJoystickX = event.state / XboxController.MAX_JOY_VAL # normalize between -1 and 1
                    case 'ABS_RY':
                        self.RightJoystickY = event.state / XboxController.MAX_JOY_VAL # normalize between -1 and 1
                    case 'ABS_RX':
                        self.RightJoystickX = event.state / XboxController.MAX_JOY_VAL # normalize between -1 and 1
                    case 'ABS_Z':
                        self.LeftTrigger = event.state / XboxController.MAX_TRIG_VAL # normalize between 0 and 1
                    case 'ABS_RZ':
                        self.RightTrigger = event.state / XboxController.MAX_TRIG_VAL # normalize between 0 and 1
                    case 'BTN_TL':
                        self.LeftBumper = event.state
                    case 'BTN_TR':
                        self.RightBumper = event.state
                    case 'BTN_SOUTH':
                        self.A = event.state
                    case 'BTN_NORTH':
                        self.Y = event.state #previously switched with X
                    case 'BTN_WEST':
                        self.X = event.state #previously switched with Y
                    case 'BTN_EAST':
                        self.B = event.state
                    case 'BTN_THUMBL':
                        self.LeftThumb = event.state
                    case 'BTN_THUMBR':
                        self.RightThumb = event.state
                    case 'BTN_SELECT':
                        self.Back = event.state
                    case 'BTN_START':
                        self.Start = event.state
                    case 'BTN_TRIGGER_HAPPY1':
                        self.LeftDPad = event.state
                    case 'BTN_TRIGGER_HAPPY2':
                        self.RightDPad = event.state
                    case 'BTN_TRIGGER_HAPPY3':
                        self.UpDPad = event.state
                    case 'BTN_TRIGGER_HAPPY4':
                        self.DownDPad = event.state
                    case _:
                        print(f"Unknown code {event.code}: {event.state}")
            time.sleep(0.005)

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


if __name__ == '__main__':
    joy = XboxController()
    while True:
        print(joy.read())
        time.sleep(.001)