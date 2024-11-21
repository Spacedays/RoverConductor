import asyncio
import contextlib
import queue
import traceback

import numpy as np
import pyqtgraph as pg
from msgpack import Packer, Unpacker
from pyqtgraph import PlotDataItem, PlotWidget
from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtSerialPort import QSerialPort

# import PySide6.QtAsyncio as QtAsyncio     # doesn't support the task used to read the controller yet
from qasync import QApplication, QEventLoop

from gamepad import Gamepad, GamepadState
from simple_msgpack_console import parse_messages, rxQueue, get_data_packet
from pico_interface import ControlPacket, MotionVector, calc_steer_center, calc_motion_vec
from pico_interface import RCONST

from typing import Dict, Tuple

import pyjoystick
from pyjoystick.sdl2 import Key, Joystick, run_event_loop

unpacker = Unpacker()
packer = Packer()


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


def wheel_angles_to_pg(*args):
    # wheel angles have CCW (+), with 0 degrees in the +Y direction
    # pg angles have CW (+), with 0 degrees in the -X direction
    return [-arg for arg in args]


class ControlWindow(QtWidgets.QWidget):
    def __init__(self, controller: Gamepad|GamepadState=None):
        super().__init__()

        self.pw = PlotWidget(self)
        self.cmap_table = pg.colormap.get("CET-C2").getLookupTable(nPts=6)
        self.legend = self.pw.addLegend()
        self.data = []
        self.lines: PlotDataItem = []
        self.tick: int = 0  # wraps from 0-1000
        self.ticksize: float = 0.05
      #   self.controller = controller
        self.controller = GamepadState()
        self.controller_toggle = QtWidgets.QToolButton()
        self.running = False
        self.console = SerialConsoleWidget()
        self.rdisp = pg.PlotWidget()
        self.arrows: Dict[str : pg.ArrowItem] = None
        self.sc = pg.TargetItem
        self._task_set = set()

        self.controller_mgr = pyjoystick.ThreadEventManager(event_loop=run_event_loop,
                                     remove_joystick=self.stop,
                                     handle_key_event=self.controller.handle_key_event,
                                     button_repeater=None)

			#TODO: handle controller connection
        
        self.control_update = QtCore.QTimer(self)
        self.control_update.timeout.connect(self.update_data)

        lay = QtWidgets.QVBoxLayout(self)
        toolbar = QtWidgets.QToolBar()
        toolbar.addWidget(self.controller_toggle)
        self.controller_toggle.setText("Connect &Gamepad")
        self.controller_toggle.setCheckable(True)
        self.controller_toggle.toggled.connect(self.startcontrol)
        lay.addWidget(toolbar)

        hlay = QtWidgets.QHBoxLayout()
        hlay.addWidget(self.pw)
        self.pw.setMinimumWidth(400)

        vlay = QtWidgets.QVBoxLayout()
        vlay.addWidget(self.rdisp)
        vlay.addWidget(self.console)

        hlay.addLayout(vlay)
        hlay.setStretch(0, 4)
        hlay.setStretch(1, 3)
        lay.addLayout(hlay)

        self._roverdisp_setup()
        self.update_plot(*[0, 0, 0, 0])
        self.set_names(["ljx", "ljy", "rjx", "rt"])

    def _roverdisp_setup(self):
        """Displays arrows for rover wheel directions"""
        self.arrows = {"FL": None, "FR": None, "BL": None, "BR": None}
        pos = {
            "FL": (-RCONST.SCDX, RCONST.SCDY),
            "FR": (RCONST.SCDX, RCONST.SCDY),
            "BL": (-RCONST.SCDX, -RCONST.SCDY),
            "BR": (RCONST.SCDX, -RCONST.SCDY),
        }

        for key in self.arrows:
            arrow = pg.ArrowItem(angle=90)
            self.arrows[key] = arrow
            self.rdisp.addItem(arrow)
            arrow.setPos(*pos[key])

        self.sc = pg.TargetItem()
        self.sc.setPos(0, 0)
        self.rdisp.addItem(self.sc)
        # self.rdisp.getPlotItem().enableAutoRange()
        self.rdisp.setXRange(min=RCONST.SCDX * -15, max=RCONST.SCDX * 15)
        self.rdisp.setYRange(min=RCONST.SCDY * -15, max=RCONST.SCDY * 15)

    def update_motion_vector(self, FL: int, FR: int, BL: int, BR: int, sc: Tuple[int, int]):
        offset = 90
        self.arrows["FL"].setStyle(angle=FL + offset)
        self.arrows["FR"].setStyle(angle=FR + offset)
        self.arrows["BL"].setStyle(angle=BL + offset)
        self.arrows["BR"].setStyle(angle=BR + offset)

        self.sc.setPos(*sc)

    def showEvent(self, ev):
        QtCore.QTimer.singleShot(100, self.startcontrol)

    def update_plot(
        self,
        *data,
    ):
        for idx, vardata in enumerate(data):
            if idx >= len(self.data):
                self.data.append(np.zeros(1000))
                self.data[idx][self.tick] = vardata
                self.lines.append(self.pw.plot(self.data[idx], pen=self.cmap_table[idx], name=f"Data {idx}"))
            self.data[idx][self.tick] = vardata
        self.pw.setXRange(self.tick - 100, self.tick - 0.05 * 100, padding=0.05)

        self.tick += 1
        if self.tick >= 1000:
            self.tick = 0

    def set_names(self, names_list):
        self.legend.clear()
        for idx, name in enumerate(names_list[: len(self.data)]):
            self.legend.addItem(self.lines[idx], name)

    def startcontrol(self, state=False):
        """If a gamepad is connected, start updating the UI and controller objects"""
        if self.running:
            return self.stop()
            # pyjoystick.sdl2.quit()
        # pyjoystick.sdl2.init()

        #TODO: handle adding & removing joystick
        devices = Joystick.get_joysticks()
        if not devices:
            print("No gamepad found")
            return

        print("Devices:")
        for joy in devices:
            print('\t', f'{joy.get_id()}.', joy.get_name())
        self.controller_mgr.start()
        self.control_update.start(self.ticksize)

        with QtCore.QSignalBlocker(self.controller_toggle):
            self.controller_toggle.setChecked(True)
        self.controller_toggle.setText("Disconnect &Gamepad")
        self.running = True

    def stop(self):
        print("Gamepad disabled")
        self.running = False
        with QtCore.QSignalBlocker(self.controller_toggle):
            self.controller_toggle.setChecked(False)
        self.controller_toggle.setText("Connect &Gamepad")

        self.controller_mgr.stop()
        if self.control_update.isActive():
            self.control_update.stop()

    def update_data(self):
        vals = [
            self.controller.joystick_left_x,
            self.controller.joystick_left_y,
            self.controller.joystick_right_x,
            self.controller.trigger_right,
        ]
        # vals = [val / JOY_MID for val in vals]
        # print(vals)
        self.update_plot(*vals)
        for idx, datal in enumerate(self.lines):
            datal.setData(self.data[idx])

        # self.console.send_raw(
        #     get_data_packet(
        #         packer,
        #         self.controller.button_a,
        #         self.controller.button_b,
        #         self.controller.trigger_right,
        #         self.controller.joystick_left_x,
        #         self.controller.joystick_left_y,
        #     )
        # )

        d, h = calc_steer_center(self.controller.joystick_left_x, self.controller.joystick_left_y)
        # d = self.controller.joystick_left_x/160
        # h = self.controller.joystick_left_y/160
        mvec = calc_motion_vec(
            ControlPacket(
                self.controller.button_a,
                self.controller.button_b,
                self.controller.trigger_right,
                self.controller.joystick_left_x,
                self.controller.joystick_left_y,
            ),
            d,
            h,
        )
        angles = mvec.aFL, mvec.aFR, mvec.aBL, mvec.aBR
        print(angles, f"({d:.2f} {h:.2f})")
        angles = wheel_angles_to_pg(*angles)
        # self.update_motion_vector(mvec.aFL, mvec.aFR, mvec.aBL, mvec.aBR, (d, h))
        self.update_motion_vector(*angles, (d, h))

        # await asyncio.sleep(self.ticksize)

    def check_exceptions(self, task):
        print(f"Closed {task}")
        self._task_set.remove(task)  # prefer a set than a list -  'remove' gets much better

        try:
            _ = task.result()
        except NotImplementedError:
            print(f"NotImplementedError: {traceback.format_exc()}")
        except Exception as e:
            print(f"Exception!\n{traceback.format_exc()}\nquitting...")
            self.running = False


class SerialConsoleWidget(QtWidgets.QWidget):
    def __init__(self, parent=None):
        super(SerialConsoleWidget, self).__init__(parent)
        self.message_le = QtWidgets.QLineEdit()
        self.send_btn = QtWidgets.QPushButton(text="Send", clicked=self.send)
        self.output_te = QtWidgets.QTextEdit(readOnly=True)
        self.button = QtWidgets.QPushButton(text="Connect Serial", checkable=True, toggled=self.on_toggled)
        lay = QtWidgets.QVBoxLayout(self)
        hlay = QtWidgets.QHBoxLayout()
        hlay.addWidget(self.message_le)
        hlay.addWidget(self.send_btn)
        lay.addLayout(hlay)
        lay.addWidget(self.output_te)
        lay.addWidget(self.button)

        self.serial = QSerialPort(
            "/dev/ttyACM0",
            baudRate=115200,
            readyRead=self.receive,
            flowControl=QSerialPort.FlowControl.NoFlowControl,
        )
        self.serial.errorOccurred.connect(self.on_error)

    # @QtCore.pyqtSlot()
    def receive(self):
        while self.serial.canReadLine():
            parse_messages(unpacker, self.serial.readAll().data())
            # text = self.serial.readLine().data().decode()
            # text = text.rstrip('\r\n')
            # self.output_te.append(text)
        with contextlib.suppress(queue.Empty):
            for obj in iter(lambda: rxQueue.get(block=True, timeout=0.01), None):
                if obj is None:
                    break
                self.output_te.append(f"~RX({rxQueue.qsize()}):{obj}")

    # @QtCore.pyqtSlot()
    def send(self):
        if self.serial.isOpen():
            self.serial.write(self.message_le.text().encode())

    def send_raw(self, rawdata):
        if self.serial.isOpen():
            self.serial.write(rawdata)

    # @QtCore.pyqtSlot(bool)
    def on_toggled(self, checked):
        self.button.setText("Disconnect Serial" if checked else "Connect Serial")
        if checked:
            if self.serial.isOpen():
                self.serial.clear()
                self.serial.setDataTerminalReady(True)
                self.output_te.append(" -- Connected to device --")
            elif not self.serial.open(QtCore.QIODevice.OpenModeFlag.ReadWrite):
                self.button.setChecked(False)
                self.output_te.append(" !-- Can't open device --!")
                print("Can't open device!")
        else:
            self.serial.close()
            self.button.setText("Connect Serial")

    def on_error(self, error: QSerialPort.SerialPortError):
        # self.output_te.append(f"!ERR! {error}")
        if error == QSerialPort.SerialPortError.NoError:
            return
        print(f"Error: {error}")
        # print(f"Error: {self.serial.errorString(), self.serial.error()}")
        if self.serial.isOpen():
            self.serial.close()
        self.serial.clearError()
        self.button.setText("Connect Serial")


if __name__ == "__main__":
    import signal
    import sys

    app = QApplication(sys.argv)

    # w = SerialConsoleWidget()

    # try:
   #  g = Gamepad()
   #  w = MainWindow(g)
    w = ControlWindow()
    w.show()
    
    app.exec()
