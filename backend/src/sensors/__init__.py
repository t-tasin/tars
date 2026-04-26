"""Sensor pipeline (Phase 3.5).

Sensors are the agent's senses: each one ``collect()``s a payload from
some external source and ``publish()``es it to the ``world_state`` table
plus a Redis pub/sub channel ``tars:world:<sensor_name>``.

See ``docs/code_conventions.md`` ("Tool-use on local tier") for the
pre-fetch pattern that consumes these readings, and ``docs/FEATURES.md``
P3.5-01 for the original design.
"""

from sensors.base import BaseSensor
from sensors.healthkit import HealthKitSensor
from sensors.spotify import SpotifySensor
from sensors.tailscale import TailscaleSensor
from sensors.weather import WeatherSensor

__all__ = ["BaseSensor", "HealthKitSensor", "SpotifySensor", "TailscaleSensor", "WeatherSensor"]
