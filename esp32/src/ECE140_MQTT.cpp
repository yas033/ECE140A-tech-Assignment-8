#include "ECE140_MQTT.h"

ECE140_MQTT::ECE140_MQTT(String clientId, String topicPrefix)
: _clientId(clientId), _topicPrefix(topicPrefix), _isTLS(false) {
    Serial.println("[ECE140_MQTT] Initialized with client ID and topic prefix");
    _mqttClient = new PubSubClient(_wifiClient);
}

bool ECE140_MQTT::connectToBroker(int port) {
    _isTLS = false;
    _setupMQTTClient(port);

    Serial.println("[MQTT] Connecting to broker...");

    if (_mqttClient->connect(_clientId.c_str())) {
        Serial.println("[MQTT] Connected successfully!");
        return true;
    } else {
        Serial.print("[MQTT] Connection failed with state: ");
        Serial.println(_mqttClient->state());
        return false;
    }
}

void ECE140_MQTT::_setupMQTTClient(int port) {
    _mqttClient = new PubSubClient(_wifiClient);
    _mqttClient->setServer(_broker, port);
    _mqttClient->setBufferSize(1024);
}

bool ECE140_MQTT::publishMessage(String subtopic, String message) {
    String fullTopic = _topicPrefix + "/" + subtopic;

    Serial.print("[MQTT] Publishing to topic: ");
    Serial.println(fullTopic);

    if (_mqttClient->publish(fullTopic.c_str(), message.c_str())) {
        Serial.println("[MQTT] Message published successfully");
        return true;
    } else {
        Serial.println("[MQTT] Failed to publish message");
        return false;
    }
}

bool ECE140_MQTT::subscribeTopic(String subtopic) {
    String fullTopic = _topicPrefix + "/" + subtopic;

    Serial.print("[MQTT] Subscribing to topic: ");
    Serial.println(fullTopic);

    if (_mqttClient->subscribe(fullTopic.c_str())) {
        Serial.println("[MQTT] Subscribed successfully");
        return true;
    } else {
        Serial.println("[MQTT] Failed to subscribe");
        return false;
    }
}

void ECE140_MQTT::setCallback(void (*callback)(char*, uint8_t*, unsigned int)) {
    _mqttClient->setCallback(callback);
}

void ECE140_MQTT::loop() {
    if (!_mqttClient) return;

    // If disconnected, reconnect
    if (!_mqttClient->connected()) {
        Serial.println("[MQTT] Disconnected. Reconnecting...");

        // IMPORTANT: connectToBroker() should use the same port as before
        // If your connectToBroker needs a port, keep it fixed here:
        if (connectToBroker(1883)) {
            Serial.println("[MQTT] Reconnected. Re-subscribing...");
            // Re-subscribe after reconnect (PubSubClient drops subs on reconnect)
            subscribeTopic("command");
        } else {
            delay(1000);
            return;
        }
    }

    _mqttClient->loop();
}
