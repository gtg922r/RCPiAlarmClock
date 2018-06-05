import logging
import paho.mqtt.client as mqtt
import rcpy


class RCMQTTclient:
    '''MQTT Client with connection to MQTT server'''

    def __init__(self, loggingLevel=logging.WARNING):
        self.log = rcpy.setupQueueLogger("RC-MQTT-Client")
        self.log.setLevel(loggingLevel)

        self._client = mqtt.Client("maniac-alarm")
        self.on_message = None
        self.on_connect = None
        self._client.on_connect = self._on_connect
        self._client.on_message = self._on_message

        self._client.username_pw_set("homeassistant", "homeassistant")
        self._client.connect("192.168.86.68")
        self._client.loop_start()

    def _on_connect(self, client, userdata, flags, rc):
        self.log.info("Connected with result code " + str(rc))

        # Subscribing in on_connect() means that if we lose the connection and
        # reconnect then subscriptions will be renewed.
        # client.subscribe("$SYS/#")

        if (self.on_connect):
            self.on_connect(self, client, userdata, flags, rc)

    def _on_message(self, client, userdata, msg):
        # Print any message we receive
        self.log.debug(msg.topic + " " + str(msg.payload))

        if (self.on_message):
            self.on_message(self, client, userdata, msg)

    def publish(self, topic, payload=None, qos=0, retain=False):
        self.log.debug("Sending: " + topic + " " + str(payload))
        self._client.publish(topic, payload, qos, retain)
