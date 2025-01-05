import asyncio
import contextlib
import queue
import traceback
from time import perf_counter

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtSerialPort import QSerialPort

import numpy as np
import pyqtgraph as pg
from msgpack import Packer, Unpacker
from pyqtgraph import PlotDataItem, PlotWidget

# import PySide6.QtAsyncio as QtAsyncio     # doesn't support the task used to read the controller yet
from qasync import QApplication, QEventLoop

from gamepad import Gamepad
from simple_msgpack_console import parse_messages, rxQueue, get_data_packet
from pico_interface import (
    ControlPacket,
    MotionVector,
    calc_steer_center,
    calc_motion_vec,
    WrapMsgPack,
    PicoSerial,
)
from pico_interface import RCONST

from typing import Dict, Tuple

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


class MainWindow(QtWidgets.QWidget):
    def __init__(self, controller: Gamepad):
        super().__init__()

        self.pw = PlotWidget(self)
        self.cmap_table = pg.colormap.get("CET-C2").getLookupTable(nPts=6)
        self.legend = self.pw.addLegend()
        self.data = []
        self.lines: PlotDataItem = []
        self.tick: int = 0  # wraps from 0-1000
        self.ticksize: float = 0.05
        self.controller = controller
        self.controller_toggle = QtWidgets.QToolButton()
        self.running = False
        self.console = SerialConsoleWidget()
        self.rdisp = pg.PlotWidget()
        self.arrows: Dict[str : pg.ArrowItem] = None
        self.sc = pg.TargetItem
        self._task_set = set()
        self.ctrlpacket: ControlPacket = ControlPacket()

        lay = QtWidgets.QVBoxLayout(self)
        toolbar = QtWidgets.QToolBar()
        toolbar.addWidget(self.controller_toggle)
        self.controller_toggle.setText("Connect &Gamepad")
        self.controller_toggle.setCheckable(True)
        self.controller_toggle.toggled.connect(self.start)
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

    def _roverdisp_setup(self):
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

    def set_controller_state(self, state: bool):
        if state and self.controller:
            with QtCore.QSignalBlocker(self.controller_toggle):
                self.controller_toggle.setChecked(True)
            self.controller_toggle.setText("Disconnect &Gamepad")
        else:
            with QtCore.QSignalBlocker(self.controller_toggle):
                self.controller_toggle.setChecked(False)
            self.controller_toggle.setText("Connect &Gamepad")

    def update_motion_vector(self, FL: int, FR: int, BL: int, BR: int, sc: Tuple[int, int]):
        offset = 90
        self.arrows["FL"].setStyle(angle=FL + offset)
        self.arrows["FR"].setStyle(angle=FR + offset)
        self.arrows["BL"].setStyle(angle=BL + offset)
        self.arrows["BR"].setStyle(angle=BR + offset)

        self.sc.setPos(*sc)

    def showEvent(self, ev):
        QtCore.QTimer.singleShot(100, self.start)

    def update_plot(
        self,
        *data,
    ):
        for idx, vardata in enumerate(data):
            if idx >= len(self.data):
                self.data.append(np.zeros(1000))
                self.data[idx][self.tick] = vardata
                self.lines.append(
                    self.pw.plot(self.data[idx], pen=self.cmap_table[idx], name=f"Data {idx}")
                )
            self.data[idx][self.tick] = vardata
        self.pw.setXRange(self.tick - 100, self.tick - 0.05 * 100, padding=0.05)

        self.tick += 1
        if self.tick >= 1000:
            self.tick = 0

    def set_names(self, names_list):
        self.legend.clear()
        for idx, name in enumerate(names_list[: len(self.data)]):
            self.legend.addItem(self.lines[idx], name)

    def start(self, state=False):
        if self.running:
            print("Gamepad disabled")
            self.running = False
            self.set_controller_state(False)
            return
        try:
            self.loop = asyncio.get_event_loop()
            print("Starting updates")
            if not self.controller and not self.controller.connect():
                return
            self.running = True

            gamepad_inp = asyncio.create_task(self.controller.read_gamepad_input())
            self._task_set.add(gamepad_inp)
            gamepad_inp.add_done_callback(self.check_exceptions)

            data_update = asyncio.create_task(self.update_data())
            self._task_set.add(data_update)
            data_update.add_done_callback(self.check_exceptions)

            message_transmit = asyncio.create_task(self.send_control_packet())
            self._task_set.add(message_transmit)
            message_transmit.add_done_callback(self.check_exceptions)

            self.set_controller_state(True)

        except Exception as e:
            print(f"Loop Exception! {e}")
            raise e

    # not here
    async def update_data(self):
        self.update_plot(*[0, 0, 0, 0])
        self.set_names(["ljx", "ljy", "rjx", "rt"])
        while self.running and self.controller:
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

            self.ctrlpacket = ControlPacket(
                self.controller.button_a,
                self.controller.button_b,
                int(self.controller.trigger_right * RCONST.TRIGGER_MAX),
                int(self.controller.joystick_left_x * RCONST.JOY_MAX),
                int(self.controller.joystick_left_y * RCONST.JOY_MAX),
            )
            d, h = calc_steer_center(self.ctrlpacket.ljx, self.ctrlpacket.ljy)
            mvec = calc_motion_vec(self.ctrlpacket, d, h)

            angles = mvec.aFL, mvec.aFR, mvec.aBL, mvec.aBR
            # print(angles, f"({d:.2f} {h:.2f})")   #DEBUG
            angles = wheel_angles_to_pg(*angles)
            # self.update_motion_vector(mvec.aFL, mvec.aFR, mvec.aBL, mvec.aBR, (d, h))
            self.update_motion_vector(*angles, (d, h))

            await asyncio.sleep(self.ticksize)
        print("Done running!")

    async def send_control_packet(self):
        last_packet_time = perf_counter()
        last_packet = ControlPacket()
        while self.running and self.controller:
            if self.console.serial.isOpen() and (
                self.ctrlpacket != last_packet or perf_counter() - last_packet_time > 5
            ):
                print(f"TX: {self.ctrlpacket}")
                self.console.send_raw(WrapMsgPack(packer, self.ctrlpacket.to_iter()))
                last_packet_time = perf_counter()
            await asyncio.sleep(5 * self.ticksize)

    async def catch_interrupts(self):
        signals = (signal.SIGHUP, signal.SIGTERM, signal.SIGINT)
        for s in signals:
            sigtask = self._task_set.add(asyncio.create_task(shutdown_signal(s, self.loop)))
            self.loop.add_signal_handler(s, sigtask)

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
        self.button = QtWidgets.QPushButton(
            text="Connect Serial", checkable=True, toggled=self.on_toggled
        )
        lay = QtWidgets.QVBoxLayout(self)
        hlay = QtWidgets.QHBoxLayout()
        hlay.addWidget(self.message_le)
        hlay.addWidget(self.send_btn)
        lay.addLayout(hlay)
        lay.addWidget(self.output_te)
        lay.addWidget(self.button)

        self.serial = QSerialPort(
            # "/dev/ttyACM0",
            PicoSerial.find_pico(),
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
    event_loop = QEventLoop(app)
    asyncio.set_event_loop(event_loop)
    app_close_event = asyncio.Event()
    app.aboutToQuit.connect(app_close_event.set)
    # app = QtWidgets.QApplication(sys.argv)

    # w = SerialConsoleWidget()

    # try:
    g = Gamepad()
    w = MainWindow(g)
    w.show()

    with event_loop:
        event_loop.run_until_complete(app_close_event.wait())
