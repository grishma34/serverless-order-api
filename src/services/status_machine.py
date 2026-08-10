"""Order status state machine (REQ-0006 / REQ-0007).

The transition table is data, not control flow, so the legal moves can be read
off in one glance and tested exhaustively rather than sampled.

    PLACED ──► PAID ──► SHIPPED ──► DELIVERED
       │         │
       └────┬────┘
            ▼
        CANCELLED        (terminal; DELIVERED also terminal)

Note what the diagram excludes: once an order is SHIPPED it can no longer be
cancelled — only delivered. `SHIPPED -> CANCELLED` is the worked 409 example in
docs/API_SPEC.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from shared.models import OrderStatus

# Read-only so a caller cannot mutate the rules at runtime.
TRANSITIONS: Mapping[OrderStatus, frozenset[OrderStatus]] = MappingProxyType(
    {
        OrderStatus.PLACED: frozenset({OrderStatus.PAID, OrderStatus.CANCELLED}),
        OrderStatus.PAID: frozenset({OrderStatus.SHIPPED, OrderStatus.CANCELLED}),
        OrderStatus.SHIPPED: frozenset({OrderStatus.DELIVERED}),
        OrderStatus.DELIVERED: frozenset(),
        OrderStatus.CANCELLED: frozenset(),
    }
)

INITIAL_STATUS = OrderStatus.PLACED


def allowed_transitions(status: OrderStatus) -> frozenset[OrderStatus]:
    """States reachable in one step from `status`."""
    return TRANSITIONS[status]


def can_transition(from_status: OrderStatus, to_status: OrderStatus) -> bool:
    """Whether moving between these states is legal.

    A transition to the same state is *not* legal here. That case is an
    idempotent replay, which the service handles before consulting this table —
    keeping "is this a legal move" separate from "has this already happened".
    """
    return to_status in TRANSITIONS[from_status]


def is_terminal(status: OrderStatus) -> bool:
    """Whether the order can never move again."""
    return not TRANSITIONS[status]
