"""Alert rule engine - evaluates conditions and triggers notifications."""

from __future__ import annotations

from collections.abc import Callable

import structlog

logger = structlog.get_logger(__name__)


class AlertRule:
    """A single alert rule."""

    def __init__(
        self,
        name: str,
        condition: Callable[[], bool],
        message: str,
        severity: str = "WARNING",
        cooldown_seconds: int = 300,
    ) -> None:
        self.name = name
        self.condition = condition
        self.message = message
        self.severity = severity
        self.cooldown_seconds = cooldown_seconds
        self._last_triggered: float = 0.0

    def evaluate(self) -> bool:
        """Check if rule triggers."""
        import time
        now = time.time()
        if now - self._last_triggered < self.cooldown_seconds:
            return False
        if self.condition():
            self._last_triggered = now
            return True
        return False


class AlertEngine:
    """Evaluates alert rules and dispatches notifications."""

    def __init__(self) -> None:
        self._rules: list[AlertRule] = []

    def add_rule(self, rule: AlertRule) -> None:
        """Add an alert rule."""
        self._rules.append(rule)

    def evaluate_all(self) -> list[AlertRule]:
        """Evaluate all rules, return triggered ones."""
        triggered = []
        for rule in self._rules:
            if rule.evaluate():
                triggered.append(rule)
                logger.warning("alert_triggered", name=rule.name, severity=rule.severity)
        return triggered
