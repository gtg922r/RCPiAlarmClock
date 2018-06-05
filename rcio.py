import rcpy
import logging
import RPi.GPIO as GPIO
import threading
import queue


class RotaryEncoderGPIO:
    '''Read rotary encoder and loop for increments'''
    encD = "^"  # In Detent
    encL = "L"  # Left of Detent
    encR = "R"  # Right of Detent
    encM = "_"  # In-between Detents
    encE = "?"  # Unknown

    def __init__(self,
                 pinA,
                 pinB,
                 increment_callback=None,
                 decrement_callback=None,
                 change_callback=None,
                 loggingLevel=logging.WARNING):

        self.log = rcpy.setupQueueLogger("RotaryEncoderGPIO", loggingLevel)

        self.pinA = pinA
        self.pinB = pinB
        self.increment_callback = increment_callback
        self.decrement_callback = decrement_callback
        self.change_callback = change_callback

        self.invalid_transitions = 0
        self.detent_without_cycle = 0

        GPIO.setmode(GPIO.BCM)
        GPIO.setup(self.pinA, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        GPIO.setup(self.pinB, GPIO.IN, pull_up_down=GPIO.PUD_UP)

        self.processQ = queue.Queue(1000)
        self.workerThread = threading.Thread(
            name='Encoder Worker', target=self._workerFunction)
        self.workerThread.start()

        GPIO.add_event_detect(pinA, GPIO.BOTH, callback=self.processChange)
        GPIO.add_event_detect(pinB, GPIO.BOTH, callback=self.processChange)

        (A, B) = self.readEncoder()
        self.lastState = self.encoder_state(A, B)
        self.encCount = 0
        self.invalid = False

        self.log.debug("Encoder setup complete")

    def cleanup(self):
        GPIO.cleanup()
        self.log.debug("GPIO cleanup complete")

    def readEncoder(self):
        A = GPIO.input(self.pinA)
        B = GPIO.input(self.pinB)
        return (A, B)

#    def processChange(self, channel):
#        #TODO GET RID OF TIMER
#        (A, B) = self.readEncoder()
#        self.processQ.put((A,B), block=False)

    def processChange(self, channel):
        val = GPIO.input(channel)
        self.processQ.put((channel, val), block=False)


#    def processFallingChange(self, channel):
#        self.processQ.put((channel, 0), block=False)

    def encoder_state(self, A, B):
        state = None
        if (A == 1 and B == 1):
            state = self.encD
        elif (A == 1 and B == 0):
            state = self.encL
        elif (A == 0 and B == 1):
            state = self.encR
        else:
            state = self.encM

        return state

    def _workerFunction(self):
        try:
            (A, B) = self.readEncoder()
            while True:
                try:
                    (channel, val) = self.processQ.get(timeout=0.5)
                    if channel == self.pinA:
                        A = val
                    elif channel == self.pinB:
                        B = val
                    else:
                        self.log.error("Invalid channel from queue")

                    self.processState(A, B)
                    self.processQ.task_done()
                except queue.Empty:
                    pass
        except (KeyboardInterrupt, SystemExit):
            pass

    def processState(self, A, B):
        # IDEA: # for invalid state, see which channel fired the interupt and
        #  assume that one went first, and that we just missed the transision!!
        state = self.encoder_state(A, B)
        lastState = self.lastState

        if state == lastState:
            pass
        elif ((lastState == self.encD and state == self.encR)
              or (lastState == self.encR and state == self.encM)
              or (lastState == self.encM and state == self.encL)
              or (lastState == self.encL and state == self.encD)):
            self.encCount += 1
        elif ((lastState == self.encD and state == self.encL)
              or (lastState == self.encL and state == self.encM)
              or (lastState == self.encM and state == self.encR)
              or (lastState == self.encR and state == self.encD)):
            self.encCount -= 1
        else:
            self.log.info(
                "Invalid Transition! Last State: {} Current State: {}".format(
                    lastState, state))
            self.invalid = True
            self.invalid_transitions += 1

        if state == self.encD:
            if self.invalid:  # Invalid Transition was seen
                self.log.debug("Resetting after seeing an invalid transition")
            elif self.encCount == 4:  # Full increment cycle observed
                self.log.info("Incrementing!")
                if self.increment_callback:
                    threading.Thread(target=self.increment_callback).start()
                if self.change_callback:
                    threading.Thread(
                        target=self.change_callback, args=(1, )).start()
            elif self.encCount == -4:  # Full decrement cycle observed
                self.log.info("Decrementing!")
                if self.decrement_callback:
                    threading.Thread(target=self.decrement_callback).start()
                if self.change_callback:
                    threading.Thread(
                        target=self.change_callback, args=(-1, )).start()
            else:
                self.log.debug("Back to detent without full transition")
                self.detent_without_cycle += 1

            self.invalid = False
            self.encCount = 0

        self.lastState = state

        self.log.debug(
            "Change | A: {}  B: {} | State = {} | Count = {:2}".format(
                A, B, state, self.encCount))
