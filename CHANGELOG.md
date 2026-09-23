# Changelog

All notable changes to this project are documented here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.1.0] - 2026-09-23

First working release.

### Added

- Sensor simulator for temperature, vibration and machine status. It also sends spindle speed and good/scrap part counts for any number of machines, with wear drift and random faults.
- MQTT ingestion into TimescaleDB. Messages are validated with pydantic, batched, and written into hypertables. The service reconnects with backoff and has a health endpoint.
- Prebuilt Grafana dashboards for OEE, downtime and sensor trends
- Threshold-based alerts for temperature, vibration, machines down too long, and machines that stop reporting (Grafana unified alerting)
- Whole stack starts with one command (`make up`)

[Unreleased]: https://github.com/calliarc/iot-factory-monitor/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/calliarc/iot-factory-monitor/releases/tag/v0.1.0
