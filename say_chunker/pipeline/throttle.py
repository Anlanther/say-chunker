"""Inter-chunk cooldown."""

from __future__ import annotations

import time
from typing import Callable


class Throttle:
    def __init__(
        self,
        cooldown_seconds: float,
        *,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        self.cooldown_seconds = cooldown_seconds
        self._sleep = sleep_fn

    def wait(self) -> None:
        if self.cooldown_seconds > 0:
            self._sleep(self.cooldown_seconds)
