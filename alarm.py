import time
import datetime
import threading
import os
import logging
import logging.handlers
import queue
import pychromecast
import paho.mqtt.client as mqtt
from apscheduler.schedulers.background import BackgroundScheduler
import RPi.GPIO as GPIO
from luma.core.interface.serial import i2c, spi
from luma.core.render import canvas
from luma.oled.device import ssd1306, ssd1325, ssd1331, sh1106
import PIL.ImageFont
import rcMQTT

def setupLogger(loggerName):
    '''Setup queue-based logger that logs to queue, and listener the
a streams from queue'''
    que = queue.Queue(-1)  # no limit on size

    log = logging.getLogger(loggerName)
    queue_handler = logging.handlers.QueueHandler(que)
    log.addHandler(queue_handler)

    stream_handler = logging.StreamHandler()
    listener = logging.handlers.QueueListener(que, stream_handler)
    formatter = logging.Formatter(
        '%(asctime)s %(name)s | %(levelname)8s: %(message)s')
    stream_handler.setFormatter(formatter)
    listener.start()

    return log


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

        self.log = setupLogger("RotaryEncoderGPIO")
        self.log.setLevel(loggingLevel)

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

        ## TODO: If we skip a step, use the previous direction (what if the first step is a skip?)
        ## TODO: Call callback in a thread

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


class AlarmClock:
    def __init__(self, loggingLevel=logging.INFO):
        self.log = setupLogger("AlarmClock")
        self.log.setLevel(
            loggingLevel)  # This toggles all the logging in class

        self.alarmActive = True
        self.alarmWeekends = True
        self._alarmTime = datetime.time(6, 30)

        self.scheduler = BackgroundScheduler()
        self.scheduler.start()
        self.alarmJob = None

        self.scheduleAlarm()

        self.log.debug("Alarm Setup Complete")

    @property
    def alarmTime(self):
        return self._alarmTime

    @alarmTime.setter
    def alarmTime(self, set_time):
        self._alarmTime = set_time
        self.scheduleAlarm()

    def updateDisplay(self, device):
        pass

    def scheduleAlarm(self):
        if self.alarmJob:
            self.alarmJob.remove()

        daysOfWeek = 'mon-sun' if self.alarmWeekends else 'mon-fri'
        self.alarmJob = self.scheduler.add_job(
            self.alarmFunction,
            'cron',
            hour=self.alarmTime.hour,
            minute=self.alarmTime.minute,
            day_of_week=daysOfWeek)
        self.log.info("Alarm Scheduled for: {} on {}".format(
            self.alarmTime, daysOfWeek))
        self.log.debug("Alarm using scheduler job: > {}".format(self.alarmJob))

    def alarmFunction(self):
        self.log.info("Alarm Triggered! Wake-up!")

        self.log.debug("Connecting to Chromecast")
        chromecasts = pychromecast.get_chromecasts()
        cast = next(
            cc for cc in chromecasts if cc.device.friendly_name == "Bedroom")
        self.log.debug("Chromecast Status: " + str(cast.status))

        mc = cast.media_controller
        cast.set_volume(0.00)
        self.log.debug("Audio zero")
        time.sleep(2)
        self.log.debug("Playing")
        # mc.play_media('http://techslides.com/demos/samples/sample.m4a', 'audio/mp4') # Bart Simpson
        #mc.play_media('https://github.com/akosma/Ringtones/raw/master/DaleHendrix.m4r', 'audio/mp4')
        mc.play_media('http://ice1.somafm.com/groovesalad-128-aac',
                      'audio/aac')
        mc.block_until_active()
        cast.set_volume(0.0)
        self.log.debug("Blocking complete, waiting 2")
        time.sleep(5)
        self.log.debug("Ramping")

        volume_inc = 0.01
        ramp_time = 60
        max_volume = 0.4
        volume_steps = int(max_volume / volume_inc)

        for i in range(volume_steps):
            time.sleep(ramp_time / volume_steps)
            volume = i / volume_steps * max_volume
            self.log.debug("Setting Volume: {}".format(volume))
            cast.set_volume(volume)

        self.log.debug("Ramp Complete")
        time.sleep(2)
        self.log.debug("Ending...")
        #mc.pause()


def updateOLED(oled_device, alarm):
    with canvas(oled_device) as draw:
        font_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), 'fonts', 'FreePixel.ttf'))
        #                                             'fonts', 'ARCADE.TTF'))
        font_tall = PIL.ImageFont.truetype(font_path, 25)
        font_med = PIL.ImageFont.truetype(font_path, 16)

        draw.text(
            (10, 20),
            datetime.datetime.now().strftime('%H:%M:%S'),
            fill="white",
            font=font_tall)
        draw.text(
            (6, 49),
            "Wake up: {:2}:{:02}".format(alarm.alarmTime.hour,
                                         alarm.alarmTime.minute),
            fill="white",
            font=font_med)


def main():
    print("Setting Up")

    alarm = AlarmClock(loggingLevel=logging.WARNING)

    pinA = 4
    pinB = 17

    oled_serial = i2c(port=1, address=0x3C)
    # substitute ssd1331(...) or sh1106(...) below if using that device
    oled_device = ssd1306(oled_serial)
    oled_device.contrast(0)

    def encoder_change_callback(direction):
        dummyDate = datetime.date(2000, 1, 1)
        delta = datetime.timedelta(minutes=5 * direction)
        new_time = (datetime.datetime.combine(dummyDate, alarm.alarmTime) +
                    delta).time()
        alarm.alarmTime = new_time
        #updateOLED(oled_device, alarm) # need to be protected by crtiical block

    encoder = RotaryEncoderGPIO(
        pinA,
        pinB,
        loggingLevel=logging.WARNING,
        change_callback=encoder_change_callback)

    try:
        while True:
            #            start_time = time.perf_counter()
            updateOLED(oled_device, alarm)
            #            end_time = time.perf_counter()
            # print("Alarm Clock >> Invalid Transitions: {:4} OLED Time: {:6.2f}".format(encoder.invalid_transitions, (end_time-start_time)*1000))
            time.sleep(0.1)

    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        print("Cleaning Up!")
        encoder.cleanup(
        )  # Clean-up. Need to find a way to do it external. Maybe with contexts and `with`?


if __name__ == '__main__':
    main()
