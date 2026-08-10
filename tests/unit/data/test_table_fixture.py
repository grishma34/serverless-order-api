"""Smoke tests for the `orders_table` fixture itself.

The repository layer arrives in Phase 1; these assert the fixture's schema matches
docs/DYNAMODB_DESIGN.md § 2 now, so a Phase 1 failure means the repository is
wrong rather than the harness.
"""

from __future__ import annotations

from tests.conftest import TABLE_NAME


def test_table_is_created_with_the_documented_name(orders_table) -> None:
    assert orders_table.table_name == TABLE_NAME


def test_base_table_keys_are_pk_and_sk(orders_table) -> None:
    keys = {k["AttributeName"]: k["KeyType"] for k in orders_table.key_schema}
    assert keys == {"PK": "HASH", "SK": "RANGE"}


def test_both_sparse_gsis_exist(orders_table) -> None:
    indexes = {gsi["IndexName"] for gsi in orders_table.global_secondary_indexes}
    assert indexes == {"GSI1", "GSI2"}


def test_gsi_key_schemas_match_the_design(orders_table) -> None:
    by_name = {gsi["IndexName"]: gsi for gsi in orders_table.global_secondary_indexes}
    expected = {
        "GSI1": {"GSI1PK": "HASH", "GSI1SK": "RANGE"},
        "GSI2": {"GSI2PK": "HASH", "GSI2SK": "RANGE"},
    }
    for name, want in expected.items():
        got = {k["AttributeName"]: k["KeyType"] for k in by_name[name]["KeySchema"]}
        assert got == want, f"{name} key schema drifted from DYNAMODB_DESIGN.md"


def test_item_collection_round_trips(orders_table) -> None:
    # AP1's shape: one Query on PK returns META plus every ITEM# row.
    orders_table.put_item(
        Item={"PK": "ORDER#01J9", "SK": "META", "entityType": "ORDER", "status": "PLACED"}
    )
    orders_table.put_item(
        Item={"PK": "ORDER#01J9", "SK": "ITEM#001", "entityType": "ORDER_ITEM", "sku": "W-9"}
    )

    from boto3.dynamodb.conditions import Key

    result = orders_table.query(KeyConditionExpression=Key("PK").eq("ORDER#01J9"))

    assert result["Count"] == 2
    assert [item["SK"] for item in result["Items"]] == ["ITEM#001", "META"]


def test_gsi1_query_returns_only_indexed_rows(orders_table) -> None:
    # Sparse index: the line item carries no GSI1 attributes, so it must not appear.
    orders_table.put_item(
        Item={
            "PK": "ORDER#01J9",
            "SK": "META",
            "GSI1PK": "CUST#cust-42",
            "GSI1SK": "PLACED#01J9",
        }
    )
    orders_table.put_item(Item={"PK": "ORDER#01J9", "SK": "ITEM#001", "sku": "W-9"})

    from boto3.dynamodb.conditions import Key

    result = orders_table.query(
        IndexName="GSI1",
        KeyConditionExpression=Key("GSI1PK").eq("CUST#cust-42"),
    )

    assert result["Count"] == 1
    assert result["Items"][0]["SK"] == "META"


def test_table_does_not_leak_between_tests(orders_table) -> None:
    # moto state is per-test; a stale row here would silently corrupt Phase 1.
    assert orders_table.item_count == 0
