"""Exhaustive state-machine tests (REQ-0006 / REQ-0007).

Every ordered pair of statuses is asserted — 25 of them — rather than a sample,
so no illegal transition can slip through by not being thought of.
"""

from __future__ import annotations

import itertools

import pytest

from services.status_machine import (
    INITIAL_STATUS,
    TRANSITIONS,
    allowed_transitions,
    can_transition,
    is_terminal,
)
from shared.models import OrderStatus

# The legal moves, written out independently of the implementation. If the table
# in status_machine.py changes, this must change too — deliberately.
# Read from docs/API_SPEC.md § Status state machine.
LEGAL_TRANSITIONS = {
    (OrderStatus.PLACED, OrderStatus.PAID),
    (OrderStatus.PLACED, OrderStatus.CANCELLED),
    (OrderStatus.PAID, OrderStatus.SHIPPED),
    (OrderStatus.PAID, OrderStatus.CANCELLED),
    (OrderStatus.SHIPPED, OrderStatus.DELIVERED),
}

ALL_PAIRS = list(itertools.product(OrderStatus, OrderStatus))


class TestTransitionMatrix:
    def test_the_matrix_covers_every_ordered_pair(self) -> None:
        assert len(ALL_PAIRS) == len(OrderStatus) ** 2 == 25

    @pytest.mark.parametrize(("from_status", "to_status"), ALL_PAIRS)
    def test_every_pair_matches_the_specification(
        self, from_status: OrderStatus, to_status: OrderStatus
    ) -> None:
        expected = (from_status, to_status) in LEGAL_TRANSITIONS
        assert can_transition(from_status, to_status) is expected

    def test_exactly_five_transitions_are_legal(self) -> None:
        legal = {pair for pair in ALL_PAIRS if can_transition(*pair)}
        assert legal == LEGAL_TRANSITIONS

    @pytest.mark.parametrize("status", list(OrderStatus))
    def test_no_status_transitions_to_itself(self, status: OrderStatus) -> None:
        # Self-transition is a replay, handled by the service, not a legal move.
        assert can_transition(status, status) is False

    def test_shipped_cannot_be_cancelled(self) -> None:
        # The worked 409 example in API_SPEC.md — pinned so it can't regress.
        assert can_transition(OrderStatus.SHIPPED, OrderStatus.CANCELLED) is False

    def test_shipped_can_only_be_delivered(self) -> None:
        assert allowed_transitions(OrderStatus.SHIPPED) == {OrderStatus.DELIVERED}


class TestTerminalStates:
    @pytest.mark.parametrize("status", [OrderStatus.DELIVERED, OrderStatus.CANCELLED])
    def test_terminal_states_have_no_exits(self, status: OrderStatus) -> None:
        assert is_terminal(status) is True
        assert allowed_transitions(status) == frozenset()

    @pytest.mark.parametrize("status", [OrderStatus.PLACED, OrderStatus.PAID, OrderStatus.SHIPPED])
    def test_non_terminal_states_have_exits(self, status: OrderStatus) -> None:
        assert is_terminal(status) is False
        assert allowed_transitions(status)

    @pytest.mark.parametrize(
        ("terminal", "target"),
        [
            (terminal, target)
            for terminal in (OrderStatus.DELIVERED, OrderStatus.CANCELLED)
            for target in OrderStatus
        ],
    )
    def test_nothing_escapes_a_terminal_state(
        self, terminal: OrderStatus, target: OrderStatus
    ) -> None:
        assert can_transition(terminal, target) is False


class TestTableIntegrity:
    def test_every_status_appears_in_the_table(self) -> None:
        # A new enum member without a table entry would raise KeyError at runtime.
        assert set(TRANSITIONS) == set(OrderStatus)

    def test_every_target_is_a_real_status(self) -> None:
        for targets in TRANSITIONS.values():
            assert targets <= set(OrderStatus)

    def test_the_table_is_read_only(self) -> None:
        with pytest.raises(TypeError):
            TRANSITIONS[OrderStatus.DELIVERED] = frozenset({OrderStatus.PLACED})  # type: ignore[index]

    def test_orders_start_as_placed(self) -> None:
        assert INITIAL_STATUS is OrderStatus.PLACED

    def test_every_status_is_reachable_from_the_initial_state(self) -> None:
        """No state is stranded — otherwise it is dead weight in the model."""
        seen = {INITIAL_STATUS}
        frontier = [INITIAL_STATUS]
        while frontier:
            for target in allowed_transitions(frontier.pop()):
                if target not in seen:
                    seen.add(target)
                    frontier.append(target)

        assert seen == set(OrderStatus)

    def test_the_machine_is_acyclic(self) -> None:
        # A cycle would let an order return to an earlier state and re-run
        # side effects that already happened.
        for start in OrderStatus:
            seen: set[OrderStatus] = set()
            frontier = [start]
            while frontier:
                for target in allowed_transitions(frontier.pop()):
                    assert target is not start, f"cycle through {start.value}"
                    if target not in seen:
                        seen.add(target)
                        frontier.append(target)
