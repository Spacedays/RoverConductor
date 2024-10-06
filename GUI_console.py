import asyncio
import contextlib
import queue
import traceback

from msgpack import Packer, Unpacker
from PySide6 import QtCore, QtWidgets, QtGui
from PySide6.QtSerialPort import QSerialPort

# import PySide6.QtAsyncio as QtAsyncio
from qasync import QEventLoop, QApplication
import pyqtgraph as pg
from pyqtgraph import PlotWidget, PlotDataItem
import numpy as np

from simple_msgpack_console import parse_messages, send_data_packet, rxQueue
from gamepad import Gamepad, JOY_MID


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
        self._task_set = set()

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
        hlay.addWidget(self.console)
        hlay.setStretch(0, 4)
        hlay.setStretch(1, 3)
        lay.addLayout(hlay)
        # conn_controller = QtGui.QAction(self, "Connect &Gamepad",)
        # conn_controller.triggered.connect(self.controller.connect)
        # toolbar.addAction(conn_controller)
        # self.start()
        QtCore.QTimer.singleShot(100, self.start)

    # def showEvent(self, ev):
    #     self.start()

    def update(
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
            # print(idx, self.tick, vardata)
            self.data[idx][self.tick] = vardata
        self.pw.setXRange(self.tick - 100, self.tick - 0.05 * 100, padding=0.05)
        # self.pw.autoRange()
        # print(self.tick)
        self.tick += 1
        if self.tick >= 1000:
            self.tick = 0

    def set_names(self, names_list):
        self.legend.clear()
        for idx, name in enumerate(names_list[: len(self.data)]):
            self.legend.addItem(self.lines[idx], name)

    def start(self, state):
        if self.running:
            print("Gamepad disabled")
            self.running = False
            self.controller_toggle.setChecked(False)
            self.controller_toggle.setText("Connect &Gamepad")
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

            self.controller_toggle.setChecked(True)
            self.controller_toggle.setText("Disconnect &Gamepad")

            # res = asyncio.gather(self.controller.read_gamepad_input(), self.update_data())
            # await res
            # print(res)
        except Exception as e:
            print(f"Loop Exception! {e}")
            raise e

    # not here
    async def update_data(self):
        self.update(*[0, 0, 0, 0])
        self.set_names(["ljx", "ljy", "rjx", "rjy"])
        while self.running and self.controller:
            vals = [
                self.controller.joystick_left_x,
                self.controller.joystick_left_y,
                self.controller.joystick_right_x,
                self.controller.joystick_right_y,
            ]
            # vals = [val / JOY_MID for val in vals]
            # print(vals)
            self.update(*vals)
            for idx, datal in enumerate(self.lines):
                datal.setData(self.data[idx])
            self.console.send_raw(
                send_data_packet(
                    packer,
                    self.controller.button_a,
                    self.controller.button_b,
                    self.controller.trigger_right,
                    self.controller.joystick_left_x,
                    self.controller.joystick_left_y,
                )
            )
            await asyncio.sleep(self.ticksize)
        print("Done running!")

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
            print("T~", self.serial.write(self.message_le.text().encode()))

    def send_raw(self, rawdata):
        if self.serial.isOpen():
            print("T~", self.serial.write(rawdata))

    # @QtCore.pyqtSlot(bool)
    def on_toggled(self, checked):
        self.button.setText("Disconnect Serial" if checked else "Connect Serial")
        if checked:
            if self.serial.isOpen():
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

    # sys.exit(app.exec())
    # QtAsyncio.run(w.start())
    # except Exception as e:
    #     print(f"Exception! {e}")
    with event_loop:
        # f = w.start()
        # event_loop.call_later(w.start())
        # event_loop.run_forever()
        # event_loop.create_task(w.start())
        event_loop.run_until_complete(app_close_event.wait())
