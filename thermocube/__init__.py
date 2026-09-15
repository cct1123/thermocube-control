"""ThermoCube control API. Importing this package never opens a device."""

from thermocube.controller import Faults, SafetyError, Status, ThermoCube
from thermocube.simulator import Simulator

__all__ = ["ThermoCube", "Simulator", "Status", "Faults", "SafetyError"]
__version__ = "0.2.0"
