#!/bin/sh
# Builds the Mosquitto password file from environment variables, then starts
# the broker. Passwords are hashed (PBKDF2-SHA512) by mosquitto_passwd.
set -eu

: "${MQTT_INGESTOR_PASSWORD:?MQTT_INGESTOR_PASSWORD must be set (see .env.example)}"
: "${MQTT_SIMULATOR_PASSWORD:?MQTT_SIMULATOR_PASSWORD must be set (see .env.example)}"

PASSWD=/mosquitto/data/passwd
rm -f "$PASSWD"
touch "$PASSWD"
mosquitto_passwd -b "$PASSWD" ingestor "$MQTT_INGESTOR_PASSWORD"
mosquitto_passwd -b "$PASSWD" simulator "$MQTT_SIMULATOR_PASSWORD"
chown mosquitto:mosquitto "$PASSWD"
chmod 0700 "$PASSWD"

exec mosquitto -c /mosquitto/config/mosquitto.conf
