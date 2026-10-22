"""A monotonic deadline shared by admission, model execution and sandbox tools."""
import threading
import time


class CodingTaskBudget:
    def __init__(self, seconds: float):
        if seconds <= 0:
            raise ValueError("Coding task timeout must be positive")
        self.deadline = time.monotonic() + seconds
        self._cancelled = threading.Event()
        self.reason = "Coding task was stopped."

    def remaining(self) -> float:
        return max(0.0, self.deadline - time.monotonic())

    def cancel(self, reason="Coding task was stopped."):
        self.reason = reason
        self._cancelled.set()

    def check(self):
        if self._cancelled.is_set():
            raise RuntimeError(self.reason)
        if self.remaining() <= 0:
            raise TimeoutError("Coding task wall-time limit exceeded.")
