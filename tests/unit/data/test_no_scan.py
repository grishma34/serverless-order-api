"""REQ-0012 / NFR-0003: the table is never scanned.

Two independent checks, because either alone is defeatable:

- **Static** — greps `src/` for scan calls. Catches dead or unreached code the
  behavioral check would never execute.
- **Behavioral** — records the DynamoDB operations actually issued through
  botocore. Catches a scan reached by indirection (`getattr`, a helper, a
  library) that the grep cannot see.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from shared.models import OrderStatus

# `.scan(`, `client.scan`, or a Select=ALL_ATTRIBUTES projection forced on a table.
SCAN_PATTERNS = (
    re.compile(r"\.scan\s*\("),
    re.compile(r"""["']Scan["']"""),
    re.compile(r"Select\s*=\s*['\"]ALL_ATTRIBUTES['\"]"),
)

# Read operations that are permitted. Writes are checked separately.
ALLOWED_READ_OPERATIONS = {"Query", "GetItem", "BatchGetItem"}


def _python_sources(src_dir: str) -> list[pathlib.Path]:
    return sorted(pathlib.Path(src_dir).rglob("*.py"))


class TestStaticNoScan:
    def test_source_tree_is_not_empty(self, src_dir: str) -> None:
        # Guards the guard: an empty glob would make every check below vacuous.
        assert len(_python_sources(src_dir)) > 3

    def test_no_source_file_mentions_scan(self, src_dir: str) -> None:
        offenders: list[str] = []
        for path in _python_sources(src_dir):
            source = path.read_text()
            for pattern in SCAN_PATTERNS:
                for match in pattern.finditer(source):
                    line_no = source[: match.start()].count("\n") + 1
                    offenders.append(f"{path.name}:{line_no}: {match.group(0)}")

        assert not offenders, "Scan is banned (REQ-0012); found: " + "; ".join(offenders)


class TestBehavioralNoScan:
    """Every repository method, asserted against the real botocore call log."""

    def test_create_order_uses_only_a_transaction(
        self, repository, make_order, dynamodb_calls
    ) -> None:
        repository.create_order(make_order(order_id="01J9A"), "key-1")
        assert dynamodb_calls == ["TransactWriteItems"]

    def test_get_order_issues_a_single_query(self, repository, make_order, dynamodb_calls) -> None:
        repository.create_order(make_order(order_id="01J9A"), "key-1")
        dynamodb_calls.clear()

        repository.get_order("01J9A")

        assert dynamodb_calls == ["Query"]

    def test_idempotency_lookup_is_a_get_item(self, repository, make_order, dynamodb_calls) -> None:
        repository.create_order(make_order(order_id="01J9A"), "key-1")
        dynamodb_calls.clear()

        repository.get_idempotency_record("key-1")

        assert dynamodb_calls == ["GetItem"]

    @pytest.mark.parametrize(
        "call",
        [
            pytest.param(lambda r: r.list_customer_orders("c1"), id="AP3"),
            pytest.param(
                lambda r: r.list_customer_orders_by_status("c1", OrderStatus.PLACED), id="AP4"
            ),
            pytest.param(lambda r: r.list_orders_by_status(OrderStatus.PLACED), id="AP5"),
        ],
    )
    def test_every_list_pattern_uses_only_query(self, repository, dynamodb_calls, call) -> None:
        call(repository)

        assert dynamodb_calls, "no DynamoDB call was recorded — the hook is broken"
        assert set(dynamodb_calls) <= ALLOWED_READ_OPERATIONS
        assert "Scan" not in dynamodb_calls

    def test_transition_uses_a_conditional_update(
        self, repository, make_order, dynamodb_calls
    ) -> None:
        repository.create_order(make_order(order_id="01J9A"), "key-1")
        dynamodb_calls.clear()

        repository.transition_status("01J9A", OrderStatus.PLACED, OrderStatus.PAID)

        assert dynamodb_calls == ["UpdateItem"]

    def test_a_full_workload_never_scans(self, repository, make_order, dynamodb_calls) -> None:
        """Exercise every access pattern in one test and assert on the whole log."""
        for index, order_id in enumerate(["01J9A", "01J9B", "01J9C"]):
            repository.create_order(
                make_order(order_id=order_id, customer_id=f"c{index % 2}"), f"key-{order_id}"
            )
        repository.get_order("01J9A")
        repository.get_idempotency_record("key-01J9A")
        repository.list_customer_orders("c0")
        repository.list_customer_orders_by_status("c0", OrderStatus.PLACED)
        repository.list_orders_by_status(OrderStatus.PLACED)
        repository.transition_status("01J9A", OrderStatus.PLACED, OrderStatus.PAID)

        assert "Scan" not in dynamodb_calls
        assert set(dynamodb_calls) == {
            "TransactWriteItems",
            "Query",
            "GetItem",
            "UpdateItem",
        }

    def test_the_hook_would_actually_catch_a_scan(self, orders_table, dynamodb_calls) -> None:
        """Prove the detector works — otherwise every assertion above is vacuous.

        This is the one deliberate Scan in the suite, issued directly against the
        table rather than through `src/`, so the static check stays clean.
        """
        orders_table.meta.client.scan(TableName=orders_table.name)

        assert "Scan" in dynamodb_calls
