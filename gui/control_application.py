import os
import queue
import subprocess
import sys
import threading
import traceback
from contextlib import redirect_stdout

from PyQt5.QtCore import pyqtSlot
from PyQt5.QtWidgets import QVBoxLayout, QMainWindow, QWidget, QInputDialog, QMessageBox, QApplication

from gui import utils
from gui.dialogs import ConfigurationDialog
from gui.logging_stream import LoggingStream
from gui.utils import handled_function, handled_slot
from gui.widgets import LoggingArea, StatusMonitor
from matisse import Matisse


# TODO: UpdateGuiState thread to make sure menus are checked correctly
# TODO: Use concurrent.futures to execute scans in parallel so you can catch errors in those threads
class ControlApplication(QApplication):
    EXIT_CODE_RESTART = 42  # Answer to the Ultimate Question of Life, the Universe, and Everything
    CONFIRM_WAVELENGTH_CHANGE_THRESHOLD = 10

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Non-handled functions only here
        self.setup_window()
        self.setup_logging()
        self.setup_menus()
        self.setup_slots()

        # Handled functions can go here
        self.setup_matisse()
        self.setup_widgets()

        # Other setup
        self.aboutToQuit.connect(self.clean_up)

        container = QWidget()
        container.setLayout(self.layout)
        self.window.setCentralWidget(container)
        self.window.show()

    def setup_window(self):
        self.window = window = QMainWindow()
        self.layout = QVBoxLayout()
        window.setWindowTitle('Matisse Controller')
        window.resize(600, 300)

    def setup_logging(self):
        self.log_queue = queue.Queue()
        self.log_area = LoggingArea(self.log_queue)
        self.log_area.setReadOnly(True)
        self.layout.addWidget(self.log_area)

        # Set up a context manager to redirect stdout to the log window
        self.log_redirector = redirect_stdout(LoggingStream(self.log_queue))
        self.log_redirector.__enter__()

    def setup_menus(self):
        menu_bar = self.window.menuBar()

        console_menu = menu_bar.addMenu('Console')
        self.clear_log_area_action = console_menu.addAction('Clear Log')
        self.configuration_action = console_menu.addAction('Configuration')
        self.open_idle_action = console_menu.addAction('Open Python Shell...')
        self.restart_action = console_menu.addAction('Restart')

        set_menu = menu_bar.addMenu('Set')
        self.set_wavelength_action = set_menu.addAction('Wavelength')
        self.set_bifi_approx_wavelength_action = set_menu.addAction('BiFi Approx. Wavelength')
        self.set_bifi_motor_pos_action = set_menu.addAction('BiFi Motor Position')
        self.set_thin_eta_motor_pos_action = set_menu.addAction('Thin Etalon Motor Position')
        self.set_piezo_eta_pos_action = set_menu.addAction('Piezo Etalon Position')
        self.set_slow_piezo_pos_action = set_menu.addAction('Slow Piezo Position')
        self.set_refcell_pos_action = set_menu.addAction('RefCell Position')

        scan_menu = menu_bar.addMenu('Scan')
        self.bifi_scan_action = scan_menu.addAction('Birefringent Filter')
        self.thin_eta_scan_action = scan_menu.addAction('Thin Etalon')

        stabilization_menu = menu_bar.addMenu('Stabilization')
        toggle_control_loop_menu = stabilization_menu.addMenu('Toggle Control Loop')
        self.slow_pz_control_action = toggle_control_loop_menu.addAction('Slow Piezo')
        self.slow_pz_control_action.setCheckable(True)
        self.thin_eta_control_action = toggle_control_loop_menu.addAction('Thin Etalon')
        self.thin_eta_control_action.setCheckable(True)
        self.piezo_eta_control_action = toggle_control_loop_menu.addAction('Piezo Etalon')
        self.piezo_eta_control_action.setCheckable(True)
        self.fast_pz_control_action = toggle_control_loop_menu.addAction('Fast Piezo')
        self.fast_pz_control_action.setCheckable(True)
        self.lock_laser_action = stabilization_menu.addAction('Lock Laser')
        self.lock_laser_action.setCheckable(True)
        self.auto_stabilize_action = stabilization_menu.addAction('Automatic Stabilization')
        self.auto_stabilize_action.setCheckable(True)

        self.control_loop_actions = [self.slow_pz_control_action, self.thin_eta_control_action,
                                     self.piezo_eta_control_action, self.fast_pz_control_action]

    def setup_slots(self):
        # Console
        self.clear_log_area_action.triggered.connect(self.clear_log_area)
        self.configuration_action.triggered.connect(self.open_configuration)
        self.open_idle_action.triggered.connect(self.open_idle)
        self.restart_action.triggered.connect(self.restart)

        # Set
        self.set_wavelength_action.triggered.connect(self.set_wavelength_dialog)
        self.set_bifi_approx_wavelength_action.triggered.connect(self.set_bifi_approx_wavelength_dialog)
        self.set_bifi_motor_pos_action.triggered.connect(self.set_bifi_motor_pos_dialog)
        self.set_thin_eta_motor_pos_action.triggered.connect(self.set_thin_eta_motor_pos_dialog)
        self.set_piezo_eta_pos_action.triggered.connect(self.set_piezo_eta_pos_dialog)
        self.set_slow_piezo_pos_action.triggered.connect(self.set_slow_piezo_pos_dialog)
        self.set_refcell_pos_action.triggered.connect(self.set_refcell_pos_dialog)

        # Scan
        self.bifi_scan_action.triggered.connect(self.start_bifi_scan)
        self.thin_eta_scan_action.triggered.connect(self.start_thin_etalon_scan)

        # Stabilization
        self.lock_laser_action.triggered.connect(self.toggle_lock_laser)
        self.slow_pz_control_action.triggered.connect(self.toggle_slow_piezo_control)
        self.thin_eta_control_action.triggered.connect(self.toggle_thin_etalon_control)
        self.piezo_eta_control_action.triggered.connect(self.toggle_piezo_etalon_control)
        self.fast_pz_control_action.triggered.connect(self.toggle_fast_piezo_control)
        self.auto_stabilize_action.triggered.connect(self.toggle_auto_stabilization)

    @handled_function
    def setup_widgets(self):
        self.status_monitor_queue = queue.Queue()
        self.status_monitor = StatusMonitor(self.matisse, self.status_monitor_queue)
        self.layout.addWidget(self.status_monitor)

    @handled_function
    def setup_matisse(self):
        try:
            self.matisse: Matisse = Matisse(device_id=sys.argv[1], wavemeter_port=sys.argv[2])
        except Exception as err:
            self.matisse: Matisse = None
            raise err

    @pyqtSlot()
    def clean_up(self):
        # Clean up widgets with running threads.
        self.status_monitor.clean_up()
        self.log_area.clean_up()

        # Reset Matisse to a 'good' default state
        if self.matisse is not None:
            self.matisse.stabilize_off()
            self.matisse.stop_laser_lock_correction()

        self.log_redirector.__exit__(None, None, None)

    def error_dialog(self):
        stack = list(traceback.format_exception(*sys.exc_info()))
        # Pick length of longest line in stack, with a cutoff at 185
        desired_width = min(max([len(line) for line in stack]), 185)
        description = stack.pop()
        print(utils.red_text(description), end='')
        # Remove entries for handled_function decorator, for clarity
        stack = filter(lambda item: os.path.join('gui', 'utils.py') not in item, stack)
        dialog = QMessageBox(icon=QMessageBox.Critical)
        dialog.setWindowTitle('Error')
        # Adding the underscores is a hack to resize the QMessageBox because it's not normally resizable.
        # This looks good in Windows, haven't tested other platforms. Sorry :(
        dialog.setText(f"{description + '_' * desired_width}\n\n{''.join(stack)}")
        dialog.exec()

    @handled_slot(bool)
    def clear_log_area(self, checked):
        self.log_area.clear()

    @handled_slot(bool)
    def open_configuration(self, checked):
        dialog = ConfigurationDialog()
        dialog.exec()

    @handled_slot(bool)
    def open_idle(self, checked):
        print('Opening IDLE.')
        # TODO: Can't open wavemeter, access is denied
        subprocess.Popen('python -m idlelib -t "Matisse Controller - Python Shell" -c "from matisse import Matisse; ' +
                         f"matisse = Matisse(device_id='{sys.argv[1]}', wavemeter_port='{sys.argv[2]}'); " +
                         f"print(\\\"Access the Matisse using 'matisse.[method]'\\\")\"")

    @handled_slot(bool)
    def restart(self, checked):
        # TODO: Check if clean_up is called here
        self.exit(ControlApplication.EXIT_CODE_RESTART)

    @handled_slot(bool)
    def set_wavelength_dialog(self, checked):
        current_wavelength = self.matisse.target_wavelength
        if current_wavelength is None:
            current_wavelength = self.matisse.wavemeter_wavelength()
        # TODO: Set min and max to reasonable values
        target_wavelength, success = QInputDialog.getDouble(self.window, 'Set Wavelength', 'Wavelength (nm): ',
                                                            current_wavelength, decimals=3)
        if success:
            if abs(current_wavelength - target_wavelength) >= ControlApplication.CONFIRM_WAVELENGTH_CHANGE_THRESHOLD:
                answer = QMessageBox.warning(self.window, 'Large Wavelength Change',
                                             f"The desired wavelength, {target_wavelength} nm, is more than "
                                             f"{ControlApplication.CONFIRM_WAVELENGTH_CHANGE_THRESHOLD} nm "
                                             'away from the current wavelength. Are you sure?',
                                             QMessageBox.Yes | QMessageBox.No, defaultButton=QMessageBox.No)
                if answer == QMessageBox.No:
                    return

            print(f"Setting wavelength to {target_wavelength} nm...")
            self.set_wavelength_thread = threading.Thread(target=self.matisse.set_wavelength, args=[target_wavelength],
                                                          daemon=True)
            self.set_wavelength_thread.start()

    @handled_slot(bool)
    def set_bifi_approx_wavelength_dialog(self, checked):
        target_wavelength, success = QInputDialog.getDouble(self.window, 'Set Approx. Wavelength', 'Wavelength (nm): ',
                                                            self.matisse.query('MOTBI:WL?', numeric_result=True))
        if success:
            print(f"Setting BiFi approximate wavelength to {target_wavelength} nm...")
            self.matisse.set_bifi_wavelength(target_wavelength)

    @handled_slot(bool)
    def set_bifi_motor_pos_dialog(self, checked):
        target_pos, success = QInputDialog.getInt(self.window, 'Set BiFi Motor Position', 'Absolute Position:',
                                                  self.matisse.query('MOTBI:POS?', numeric_result=True))
        if success:
            print(f"Setting BiFi motor position to {target_pos}.")
            self.matisse.set_bifi_motor_pos(target_pos)

    @handled_slot(bool)
    def set_thin_eta_motor_pos_dialog(self, checked):
        target_pos, success = QInputDialog.getInt(self.window, 'Set Thin Etalon Motor Position', 'Absolute Position:',
                                                  self.matisse.query('MOTTE:POS?', numeric_result=True))
        if success:
            print(f"Setting thin etalon motor position to {target_pos}.")
            self.matisse.set_thin_etalon_motor_pos(target_pos)

    @handled_slot(bool)
    def set_piezo_eta_pos_dialog(self, checked):
        target_pos, success = QInputDialog.getDouble(self.window, 'Set Piezo Etalon Position', 'Position: ',
                                                     self.matisse.query('PZETL:BASE?', numeric_result=True))
        if success:
            self.matisse.query(f"PZETL:BASE {target_pos}")

    @handled_slot(bool)
    def set_slow_piezo_pos_dialog(self, checked):
        target_pos, success = QInputDialog.getDouble(self.window, 'Set Slow Piezo Position', 'Position: ',
                                                     self.matisse.query('SPZT:NOW?', numeric_result=True))
        if success:
            self.matisse.query(f"SPZT:NOW {target_pos}")

    @handled_slot(bool)
    def set_refcell_pos_dialog(self, checked):
        target_pos, success = QInputDialog.getDouble(self.window, 'Set RefCell Position', 'Position: ',
                                                     self.matisse.query('SCAN:NOW?', numeric_result=True))
        if success:
            self.matisse.query(f"SCAN:NOW {target_pos}")

    @handled_slot(bool)
    def start_bifi_scan(self, checked):
        self.bifi_scan_thread = threading.Thread(target=self.matisse.birefringent_filter_scan, daemon=True)
        self.bifi_scan_thread.start()

    @handled_slot(bool)
    def start_thin_etalon_scan(self, checked):
        self.thin_etalon_scan_thread = threading.Thread(target=self.matisse.thin_etalon_scan, daemon=True)
        self.thin_etalon_scan_thread.start()

    @handled_slot(bool)
    def toggle_lock_laser(self, checked):
        self.lock_laser_action.setChecked(not checked)
        if checked:
            self.matisse.start_laser_lock_correction()
            [action.setEnabled(False) for action in self.control_loop_actions]
            [action.setChecked(True) for action in self.control_loop_actions]
        else:
            self.matisse.stop_laser_lock_correction()
            [action.setEnabled(True) for action in self.control_loop_actions]
            [action.setChecked(False) for action in self.control_loop_actions]
        self.lock_laser_action.setChecked(checked)

    @handled_slot(bool)
    def toggle_slow_piezo_control(self, checked):
        print(f"{'Locking' if checked else 'Unlocking'} slow piezo.")
        self.slow_pz_control_action.setChecked(not checked)
        self.matisse.set_slow_piezo_control(checked)
        self.slow_pz_control_action.setChecked(checked)

    @handled_slot(bool)
    def toggle_thin_etalon_control(self, checked):
        print(f"{'Locking' if checked else 'Unlocking'} thin etalon.")
        self.thin_eta_control_action.setChecked(not checked)
        self.matisse.set_thin_etalon_control(checked)
        self.thin_eta_control_action.setChecked(checked)

    @handled_slot(bool)
    def toggle_piezo_etalon_control(self, checked):
        print(f"{'Locking' if checked else 'Unlocking'} piezo etalon.")
        self.piezo_eta_control_action.setChecked(not checked)
        self.matisse.set_piezo_etalon_control(checked)
        self.piezo_eta_control_action.setChecked(checked)

    @handled_slot(bool)
    def toggle_fast_piezo_control(self, checked):
        print(f"{'Locking' if checked else 'Unlocking'} fast piezo.")
        self.fast_pz_control_action.setChecked(not checked)
        self.matisse.set_piezo_etalon_control(checked)
        self.fast_pz_control_action.setChecked(checked)

    @handled_slot(bool)
    def toggle_auto_stabilization(self, checked):
        self.auto_stabilize_action.setChecked(not checked)
        if checked:
            self.matisse.stabilize_on()
        else:
            self.matisse.stabilize_off()
        self.auto_stabilize_action.setChecked(checked)
