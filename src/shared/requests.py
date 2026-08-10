"""Reading an API Gateway HTTP API (payload format 2.0) proxy event.

Handlers stay thin by pulling everything through here, so malformed input becomes
a typed ValidationError — a 400 — rather than a KeyError that would surface as a
500 (docs/API_SPEC.md § Error envelope).
"""

from __future__ import annotations

import base64
import binascii
import json
from typing import Any

from shared.errors import ValidationError

Event = dict[str, Any]


def json_body(event: Event) -> Any:
    """Parse the request body as JSON.

    An absent body is `None`, so a handler can tell "no body" from "empty
    object". Anything unparseable is the client's error, not ours.
    """
    raw = event.get("body")
    if raw is None or raw == "":
        return None

    if event.get("isBase64Encoded"):
        try:
            raw = base64.b64decode(raw).decode()
        except (binascii.Error, UnicodeDecodeError) as exc:
            raise ValidationError("request body is not valid base64") from exc

    try:
        return json.loads(raw)
    except (ValueError, TypeError) as exc:
        raise ValidationError("request body is not valid JSON") from exc


def header(event: Event, name: str) -> str | None:
    """Case-insensitive header lookup.

    API Gateway lowercases header names in the v2 payload, but a hand-built test
    event or a future payload version may not — so don't rely on it.
    """
    headers = event.get("headers") or {}
    wanted = name.lower()
    for key, value in headers.items():
        if key.lower() == wanted:
            return value
    return None


def path_param(event: Event, name: str) -> str:
    """A required path parameter.

    Absent means the route is wired to the wrong handler, but it reaches the
    client as a 400 rather than a 500 — the request is unserviceable either way.
    """
    value = (event.get("pathParameters") or {}).get(name)
    if value is None or not str(value).strip():
        raise ValidationError(f"{name} is required in the path")
    return str(value)


def query_param(event: Event, name: str) -> str | None:
    """An optional query-string parameter. Blank is treated as absent."""
    value = (event.get("queryStringParameters") or {}).get(name)
    if value is None or not str(value).strip():
        return None
    return str(value)
