import contextlib
import queue
import traceback
from time import perf_counter

from PySide6 import QtCore, QtGui, QtWidgets
from PySide6.QtWidgets import QApplication
from PySide6.QtSerialPort import QSerialPort

import numpy as np
import pyqtgraph as pg
from msgpack import Packer, Unpacker
from pyqtgraph import PlotDataItem, PlotWidget

# import PySide6.QtAsyncio as QtAsyncio     # doesn't support the task used to read the controller yet
# from qasync import QApplication, QEventLoop

import gamepad
from gamepad import GamepadState
from simple_msgpack_console import parse_messages, rxQueue, get_data_packet, WrapMsgPack
from pico_interface import (
    ControlPacket,
    MotionVector,
    calc_steer_center,
    calc_motion_vec,
    PicoSerial,
)
from pico_interface import RCONST

from typing import Dict, Tuple

import pyjoystick
from pyjoystick.sdl2 import Key, Joystick, run_event_loop

unpacker = Unpacker()
packer = Packer()


def wheel_angles_to_pg(*args):
    """Convert wheel-oriented angles to plot axis-oriented angles for graphing"""
    # wheel angles have CCW (+), with 0 degrees in the +Y direction
    # pg angles have CW (+), with 0 degrees in the -X direction
    return [-arg for arg in args]


class ControlWindow(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()

        self.tick: int = 0  # wraps from 0-1000
        self.ticksize: float = 0.01  # rate for controller value & GUI updates
        self.ctrlstate = GamepadState()
        self.controller_toggle = QtWidgets.QToolButton()
        self.ctrlpacket = ControlPacket()
        self.running = False

        self.last_packet: ControlPacket = ControlPacket()
        self.last_packet_time = 0
        #   self.controller = controller

        ## data plot
        self.dataplot = PlotWidget(self)  # data plot

        ## serial console
        self.console = SerialConsoleWidget()

        ## rover motion display
        self.rdisp = pg.PlotWidget()
        self.arrows: Dict[str : pg.ArrowItem] = None
        self.sc = pg.TargetItem

        self.controller_mgr = pyjoystick.ThreadEventManager(
            event_loop=run_event_loop,
            remove_joystick=self.stop,
            handle_key_event=self.ctrlstate.handle_key_event,
            button_repeater=None,
        )
        # TODO: handle controller connection/disconnection

        ## TImers
        self.control_update = QtCore.QTimer(self)
        self.control_update.timeout.connect(self.update_data)
        # interval: ticksize

        self.plot_update = QtCore.QTimer(self)
        self.plot_update.timeout.connect(self.update_ctrlplot_data)
        # interval: ticksize

        self.ctrlpacket_timer = QtCore.QTimer(self)
        self.ctrlpacket_timer.timeout.connect(self.send_ctrlpacket)
        self.packet_interval = 0.025  # 40 Hz

        ## gamepad toggle
        lay = QtWidgets.QVBoxLayout(self)
        toolbar = QtWidgets.QToolBar()
        toolbar.addWidget(self.controller_toggle)
        self.controller_toggle.setText("Connect &Gamepad")
        self.controller_toggle.setCheckable(True)
        self.controller_toggle.toggled.connect(self.startcontrol)
        lay.addWidget(toolbar)

        ## Graphics layout setup
        hlay = QtWidgets.QHBoxLayout()
        hlay.addWidget(self.dataplot)
        self.dataplot.setMinimumWidth(400)

        vlay = QtWidgets.QVBoxLayout()
        vlay.addWidget(self.rdisp)
        vlay.addWidget(self.console)

        hlay.addLayout(vlay)
        hlay.setStretch(0, 4)
        hlay.setStretch(1, 3)
        lay.addLayout(hlay)

        ## first-time setup
        self._dataplot_setup()
        self._roverdisp_setup()
        self.update_plot(*[0, 0, 0, 0])
        self.set_linenames(["ljx", "ljy", "rjx", "rt"])

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

    def _dataplot_setup(self):
        self.data = []
        self.lines: PlotDataItem = []
        self.legend = self.dataplot.addLegend()
        self.cmap_table = pg.colormap.get("CET-C2").getLookupTable(nPts=6)
        self.dataplot.setDownsampling(mode="peak")
        self.dataplot.setClipToView(True)

    def update_motion_vector(self, FL: int, FR: int, BL: int, BR: int, sc: Tuple[int, int]):
        """Sets steering center for rover motion display"""
        offset = 90
        self.arrows["FL"].setStyle(angle=FL + offset)
        self.arrows["FR"].setStyle(angle=FR + offset)
        self.arrows["BL"].setStyle(angle=BL + offset)
        self.arrows["BR"].setStyle(angle=BR + offset)

        self.sc.setPos(*sc)

    def showEvent(self, ev):
        QtCore.QTimer.singleShot(100, self.startcontrol)

    def set_linenames(self, names_list):
        self.legend.clear()
        for idx, name in enumerate(names_list[: len(self.data)]):
            self.legend.addItem(self.lines[idx], name)

    def startcontrol(self, state=False):
        """If a gamepad is connected, start updating the UI and controller objects"""
        if self.running:
            return self.stop()
            # pyjoystick.sdl2.quit()
        # pyjoystick.sdl2.init()

        # TODO: handle adding & removing joystick
        devices = Joystick.get_joysticks()
        if not devices:
            print("No gamepad found")
            return

        print("Devices:")
        for joy in devices:
            print("\t", f"{joy.get_id()}.", joy.get_name())

        # Start update timers
        self.controller_mgr.start()
        self.control_update.start(self.ticksize)
        self.plot_update.start(self.ticksize)
        self.ctrlpacket_timer.start(self.packet_interval)

        with QtCore.QSignalBlocker(self.controller_toggle):
            self.controller_toggle.setChecked(True)
        self.controller_toggle.setText("Disconnect &Gamepad")
        self.running = True
        # self.start_t = time.time()  # DEBUG perf timer

    def stop(self):
        print("Gamepad disabled")
        self.running = False
        with QtCore.QSignalBlocker(self.controller_toggle):
            self.controller_toggle.setChecked(False)
        self.controller_toggle.setText("Connect &Gamepad")

        self.controller_mgr.stop()
        if self.control_update.isActive():
            self.control_update.stop()
        if self.plot_update.isActive():
            self.plot_update.stop()

    def update_data(self):
        self.ctrlpacket = ControlPacket(
            self.ctrlstate.button_a,
            self.ctrlstate.button_b,
            int(self.ctrlstate.trigger_right * RCONST.TRIGGER_MAX),
            int(self.ctrlstate.joystick_left_x * RCONST.JOY_MAX),
            int(self.ctrlstate.joystick_left_y * RCONST.JOY_MAX),
        )
        d, h = calc_steer_center(self.ctrlpacket.ljx, self.ctrlpacket.ljy)
        mvec = calc_motion_vec(self.ctrlpacket, d, h)
        # print(self.ctrlpacket,d,h,mvec)
        angles = mvec.aFL, mvec.aFR, mvec.aBL, mvec.aBR
        # print(angles, f"({d:.2f} {h:.2f})")   #DEBUG wheel data
        angles = wheel_angles_to_pg(*angles)
        # self.update_motion_vector(mvec.aFL, mvec.aFR, mvec.aBL, mvec.aBR, (d, h))
        self.update_motion_vector(*angles, (d, h))

        # await asyncio.sleep(self.ticksize)

    def update_plot(
        self,
        *data,
    ):
        if len(data) > len(self.data):  # FIXME: handle case where only 1 variable is plotted
            for _ in range(len(data) - len(self.data)):
                idx = len(self.data)
                self.data.append(np.zeros(1000))
                self.data[idx][self.tick] = data[idx]
                self.lines.append(
                    self.dataplot.plot(self.data[idx], pen=self.cmap_table[idx], name=f"Data {idx}")
                )
        for idx, vardata in enumerate(data):
            self.data[idx][self.tick] = vardata
        self.dataplot.setXRange(self.tick - 150, self.tick - 0.05 * 150, padding=0.05)

        for idx, datal in enumerate(self.lines):
            datal.setData(self.data[idx])

        self.tick += 1
        if self.tick >= 1000:
            self.tick = 0

    def update_ctrlplot_data(self):
        self.update_plot(
            self.ctrlstate.joystick_left_x,
            self.ctrlstate.joystick_left_y,
            self.ctrlstate.joystick_right_x,
            self.ctrlstate.trigger_right,
        )
        # print("FPS:", 1 / (time.time() - self.start_t))
        # self.start_t = time.time()  # DEBUG perftimer

    def send_ctrlpacket(self):
        if not self.console.serial.isOpen():
            return
        if self.ctrlpacket != self.last_packet or perf_counter() - self.last_packet_time > 5:
            self.console.send_raw(WrapMsgPack(packer, self.ctrlpacket.to_iter()))
            self.last_packet_time = perf_counter()


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
            # elif not self.serial.open(QtCore.QIODevice.OpenModeFlag.ReadWrite):
            else:
                self.button.setChecked(False)
                self.output_te.append(" !-- Can't open device --!")
                print("Can't open Serial device!")
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
    import sys

    # import cProfile           # DEBUG profiling
    # import pstats
    # profiler = cProfile.Profile()
    # profiler.enable()

    app = QApplication(sys.argv)

    # w = SerialConsoleWidget()

    # try:
    #  g = Gamepad()
    #  w = MainWindow(g)
    w = ControlWindow()
    w.show()

    app.exec()

    # profiler.disable()
    # stats = pstats.Stats(profiler)
    # stats.sort_stats("cumulative").print_stats(10)  # Print top 10 functions by cumulative time
