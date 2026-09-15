"""Hardware-free experiment using only the public device API."""

import time

from thermocube import Simulator


def main() -> None:
    with Simulator() as device:
        device.set_setpoint(18)
        device.start()
        try:
            for _ in range(3):
                time.sleep(1.05)
                print(device.status())
        finally:
            device.stop()  # Deliberate simulation action; disconnect itself never implies STOP.


if __name__ == "__main__":
    main()
