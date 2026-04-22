import logging
from pathlib import Path

from bot.cloud import cloud_logging


class _FakeStructuredLogger:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], str]] = []

    def log_struct(self, payload: dict[str, object], *, severity: str) -> None:
        self.calls.append((payload, severity))


def test_emit_structured_log_sends_json_safe_payload(monkeypatch) -> None:
    fake_logger = _FakeStructuredLogger()
    monkeypatch.setattr(cloud_logging, "_STRUCTURED_CLOUD_LOGGER", fake_logger)

    cloud_logging.emit_structured_log(
        {
            "event": "runtime.started",
            "level": "info",
            "path": Path("data") / "health.json",
            "details": {"count": 2, "items": ["a", Path("b")]},
        }
    )

    assert len(fake_logger.calls) == 1
    payload, severity = fake_logger.calls[0]
    assert severity == "INFO"
    assert payload["event"] == "runtime.started"
    assert payload["path"] == "data\\health.json"
    assert payload["details"] == {"count": 2, "items": ["a", "b"]}


class _FakeStdlibHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        return


class _FakeCloudClient:
    def get_default_handler(self, name: str = "cry2-stdlib") -> logging.Handler:
        return _FakeStdlibHandler()


def test_create_stdlib_cloud_handler_filters_app_logs() -> None:
    handler = cloud_logging._create_stdlib_cloud_handler(_FakeCloudClient())

    app_record = logging.LogRecord("bot.core.logger", logging.INFO, __file__, 1, "app", (), None)
    third_party_record = logging.LogRecord(
        "urllib3.connectionpool",
        logging.WARNING,
        __file__,
        1,
        "ext",
        (),
        None,
    )

    assert handler.filter(app_record) is False
    assert handler.filter(third_party_record) is True
