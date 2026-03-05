#include <Arduino.h>
#include <Wire.h>
#include <WiFi.h>
#include <Adafruit_AMG88xx.h>
#include <TensorFlowLite_ESP32.h>
#include "tensorflow/lite/micro/all_ops_resolver.h"
#include "tensorflow/lite/micro/micro_interpreter.h"
#include "tensorflow/lite/micro/micro_error_reporter.h"
#include "tensorflow/lite/schema/schema_generated.h"
#include "ECE140_WIFI.h"
#include "ECE140_MQTT.h"
#include "model_data.h"
#include "model_params.h"

ECE140_WIFI wifi;
Adafruit_AMG88xx amg;

const char* ucsdUsername = UCSD_USERNAME;
String ucsdPasswordStr = String(UCSD_PASSWORD); 
const char* ucsdPassword = ucsdPasswordStr.c_str();
const char* wifiSsid = WIFI_SSID;
const char* nonEnterpriseWifiPassword = NON_ENTERPRISE_WIFI_PASSWORD;

const char* mqttClientId = MQTT_CLIENT_ID;
const char* mqttTopicPrefix = MQTT_TOPIC;

ECE140_MQTT mqtt{String(mqttClientId), String(mqttTopicPrefix)};

static bool continuous = false;
static unsigned long lastSendMs = 0;
static const unsigned long CONT_MS = 1000;
static float pixels[64];

String buildPayload(const float* px, float therm, const char* prediction, float confidence) {
  String payload = "{";
  payload += "\"mac_address\":\"" + WiFi.macAddress() + "\",";
  payload += "\"pixels\":[";
  for (int i = 0; i < 64; i++) {
    payload += String(px[i], 4);
    if (i < 63) payload += ",";
  }
  payload += "],";
  payload += "\"thermistor\":" + String(therm, 3) + ",";
  payload += "\"prediction\":\"" + String(prediction) + "\",";
  payload += "\"confidence\":" + String(confidence, 4);
  payload += "}";
  return payload;
}

void simplePredict(const float* px, float therm, const char** predOut, float* confOut) {
  float maxv = px[0];
  for (int i = 1; i < 64; i++) if (px[i] > maxv) maxv = px[i];
  float score = (maxv - therm) / 6.0f;
  if (score < 0) score = 0;
  if (score > 1) score = 1;
  if (score >= 0.5f) *predOut = "PRESENT";
  else *predOut = "EMPTY";
  *confOut = score >= 0.5f ? score : (1.0f - score);
}

void sendOneReading() {

  amg.readPixels(pixels);
  float therm = amg.readThermistor();

  const char* pred = "EMPTY";
  float conf = 0.5f;

  simplePredict(pixels, therm, &pred, &conf);

  Serial.println("---------------------------");
  Serial.print("Thermistor: ");
  Serial.print(therm);
  Serial.println(" C");

  Serial.print("Prediction: ");
  Serial.print(pred);
  Serial.print("  Confidence: ");
  Serial.println(conf);

  Serial.print("Max Pixel Temp: ");
  float maxv = pixels[0];
  for(int i=1;i<64;i++) if(pixels[i]>maxv) maxv=pixels[i];
  Serial.println(maxv);

  String payload = buildPayload(pixels, therm, pred, conf);
  mqtt.publishMessage("readings", payload);

  Serial.println("[ESP32] Published /readings");
  Serial.println("---------------------------");
}

void onMqttMessage(char* topic, uint8_t* payload, unsigned int length) {
  String msg;
  msg.reserve(length + 1);
  for (unsigned int i = 0; i < length; i++) msg += (char)payload[i];
  msg.trim();
  msg.toLowerCase();

  Serial.print("[ESP32] MQTT msg on topic: ");
  Serial.print(topic);
  Serial.print("  payload: ");
  Serial.println(msg);

  if (msg.indexOf("get_one") >= 0) {
    continuous = false;
    sendOneReading();
  } else if (msg.indexOf("start_continuous") >= 0) {
    continuous = true;
    Serial.println("[ESP32] continuous = true");
  } else if (msg.indexOf("stop") >= 0) {
    continuous = false;
    Serial.println("[ESP32] continuous = false");
  }
}

void setup() {
    Serial.begin(115200);
    delay(2000);
    if(strlen(nonEnterpriseWifiPassword)<2){
        wifi.connectToWPAEnterprise(wifiSsid, ucsdUsername, ucsdPassword);
    } else {
        wifi.connectToWiFi(wifiSsid, nonEnterpriseWifiPassword);
    }
    if (!amg.begin()) {
        while (1) delay(1000);
    }

    mqtt.connectToBroker(1883);
    mqtt.setCallback(onMqttMessage);
    mqtt.subscribeTopic("command");

    Serial.print("MAC = ");
    Serial.println(WiFi.macAddress());
}

void loop() {
    mqtt.loop();

    static unsigned long lastCheck = 0;
    if (millis() - lastCheck > 5000) {
        lastCheck = millis();
        mqtt.setCallback(onMqttMessage);
        mqtt.subscribeTopic("command");
    }

    if (continuous && (millis() - lastSendMs) >= CONT_MS) {
        lastSendMs = millis();
        sendOneReading();
    }
    delay(5);
}