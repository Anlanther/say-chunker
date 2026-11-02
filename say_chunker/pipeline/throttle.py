"""Inter-chunk cooldown.

Kept separate so thermal throttling policy is explicit and testable.
"""

from __future__ import annotations

import time
from collections.abc import Callable


class Cooldown:
    def __init__(
        self,
        seconds: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.seconds = max(0.0, seconds)
        self._sleep = sleep

    def wait(self) -> None:
        if self.seconds > 0:
            self._sleep(self.seconds)
