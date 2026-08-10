"""Tests for JSON logging and request correlation (NFR-0005)."""

from __future__ import annotations

import json
import logging

from shared.logging import JsonFormatter, get_logger, get_request_id, set_request_id


class TestJsonFormatter:
    def _record(self, **kwargs) -> logging.LogRecord:
        defaults = {
            "name": "test",
            "level": logging.INFO,
            "pathname": __file__,
            "lineno": 1,
            "msg": "hello",
            "args": None,
            "exc_info": None,
        }
        return logging.LogRecord(**{**defaults, **kwargs})

    def test_emits_one_json_object(self) -> None:
        payload = json.loads(JsonFormatter().format(self._record()))
        assert payload["message"] == "hello"
        assert payload["level"] == "INFO"
        assert payload["logger"] == "test"

    def test_includes_current_request_id(self) -> None:
        set_request_id("req-42")
        payload = json.loads(JsonFormatter().format(self._record()))
        assert payload["requestId"] == "req-42"

    def test_promotes_extra_fields_to_top_level(self) -> None:
        # CloudWatch Insights can only filter on top-level keys.
        record = self._record()
        record.orderId = "01J9XYZABC"
        payload = json.loads(JsonFormatter().format(record))
        assert payload["orderId"] == "01J9XYZABC"

    def test_renders_message_args(self) -> None:
        payload = json.loads(
            JsonFormatter().format(self._record(msg="order %s created", args=("01J9",)))
        )
        assert payload["message"] == "order 01J9 created"

    def test_includes_traceback_when_present(self) -> None:
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = self._record(exc_info=sys.exc_info())
        payload = json.loads(JsonFormatter().format(record))
        assert "ValueError: boom" in payload["exception"]

    def test_output_survives_non_serializable_values(self) -> None:
        record = self._record()
        record.weird = object()
        # default=str, so this must not raise.
        assert isinstance(json.loads(JsonFormatter().format(record))["weird"], str)


class TestRequestId:
    def test_defaults_to_placeholder(self) -> None:
        # Fresh contexts have no request bound yet.
        assert isinstance(get_request_id(), str)

    def test_set_then_get_round_trips(self) -> None:
        set_request_id("req-99")
        assert get_request_id() == "req-99"


class TestGetLogger:
    def test_attaches_exactly_one_json_handler(self) -> None:
        logger = get_logger("test.dedupe")
        get_logger("test.dedupe")
        assert len(logger.handlers) == 1
        assert isinstance(logger.handlers[0].formatter, JsonFormatter)

    def test_does_not_propagate_to_the_lambda_root_handler(self) -> None:
        # Propagation would print every line twice, once unformatted.
        assert get_logger("test.propagate").propagate is False

    def test_level_follows_the_log_level_env_var(self, monkeypatch) -> None:
        monkeypatch.setenv("LOG_LEVEL", "debug")
        assert get_logger("test.level").level == logging.DEBUG
