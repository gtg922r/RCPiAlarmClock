import time
import datetime
import os
import logging
import logging.handlers
import pychromecast
from apscheduler.schedulers.background import BackgroundScheduler
from luma.core.interface.serial import i2c, spi
from luma.core.render import canvas
from luma.oled.device import ssd1306
import PIL.ImageFont
import rcpy
import rcMQTT
import rcio


class AlarmClock:
    def __init__(self, loggingLevel=logging.INFO):
        self.log = rcpy.setupQueueLogger("AlarmClock", loggingLevel)

        self.alarmActive = True
        self.alarmWeekends = True
        self._alarmTime = datetime.time(6, 30)

        self.scheduler = BackgroundScheduler()
        self.scheduler.start()
        self.alarmJob = None

        self.mqtt = rcMQTT.RCMQTTclient(loggingLevel=logging.DEBUG)

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

        self.mqtt.publish("smartthings/Bedroom Light/switch/cmd", "on")

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
    log = rcpy.setupQueueLogger("main", logging.DEBUG)
    alarm = AlarmClock(loggingLevel=logging.DEBUG)

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
        # updateOLED(oled_device, alarm) # need to be protected by crtiical block

    encoder = rcio.RotaryEncoderGPIO(
        pinA,
        pinB,
        loggingLevel=logging.WARNING,
        change_callback=encoder_change_callback)

    try:
        while True:
            # t_start = time.perf_counter()
            updateOLED(oled_device, alarm)
            # t_end = time.perf_counter()
            # log.debug("OLED update time: {}".format(t_end-t_start))
            time.sleep(0.1)

    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        print("Cleaning Up!")
        encoder.cleanup()


if __name__ == '__main__':
    main()
