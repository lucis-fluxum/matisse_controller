import threading
import time
from queue import Queue

from matisse.control_loops_on import ControlLoopsOn


class LockCorrectionThread(threading.Thread):
    """
    Runs while the fast piezo attempts to obtain a lock. Exits if the laser cannot lock within a certain timeout, or if
    a component reaches its limit while trying to lock. If the laser locks within the timeout, but a component has
    reached its limit, makes an automatic correction to the slow piezo, piezo etalon, and RefCell.

    TODO: Set recommended setpoint for fast piezo
    """

    PIEZO_ETALON_CORRECTION_POS = 0
    SLOW_PIEZO_CORRECTION_POS = 0.35
    REFCELL_CORRECTION_POS = 0.35

    def __init__(self, matisse, timeout: float, messages: Queue, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.matisse = matisse
        self.messages = messages
        self.timeout = timeout

    def run(self):
        timer = threading.Timer(self.timeout, lambda: self.messages.put('stop'))
        with ControlLoopsOn(self.matisse):
            timer.start()
            while True:
                if self.messages.qsize() == 0:
                    if self.matisse.fast_piezo_locked():
                        timer.cancel()
                        if self.matisse.is_any_limit_reached():
                            print('WARNING: A component has hit a limit while the laser is locked. '
                                  'Attempting automatic corrections.')
                            self.attempt_piezo_corrections()
                    else:
                        if self.matisse.is_any_limit_reached():
                            print('WARNING: A component has hit a limit before the laser could lock. '
                                  'Stopping control loops.')
                            timer.cancel()
                            break

                    time.sleep(1)
                else:
                    timer.cancel()
                    break

    def attempt_piezo_corrections(self):
        self.matisse.query(f"PIEZOETALON:BASELINE {LockCorrectionThread.PIEZO_ETALON_CORRECTION_POS}")
        self.matisse.query(f"SLOWPIEZO:NOW {LockCorrectionThread.SLOW_PIEZO_CORRECTION_POS}")
        self.matisse.set_refcell_pos(LockCorrectionThread.REFCELL_CORRECTION_POS)
