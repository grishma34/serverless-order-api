"""Read `template.yaml` from tests, so infra and tests cannot drift.

TEST_STRATEGY.md § Fixtures requires the moto table to be built from the same
schema the stack deploys. A schema duplicated into conftest would silently rot
the first time the template changed; parsing the template means a mismatch
becomes a test failure instead.

PyYAML cannot load CloudFormation's shorthand tags (`!Ref`, `!Sub`, `!GetAtt`),
so the loader below turns them into their long-form dict equivalents.
"""

from __future__ import annotations

import pathlib
from typing import Any

import yaml

TEMPLATE_PATH = pathlib.Path(__file__).resolve().parent.parent / "template.yaml"

ORDERS_TABLE_LOGICAL_ID = "OrdersTable"


class CloudFormationLoader(yaml.SafeLoader):
    """SafeLoader that understands CloudFormation's `!Tag` shorthand."""


def _construct_tag(loader: yaml.Loader, tag_suffix: str, node: yaml.Node) -> Any:
    """Render `!Sub x` as `{"Fn::Sub": "x"}` — the long form CFN also accepts."""
    if isinstance(node, yaml.ScalarNode):
        value: Any = loader.construct_scalar(node)
    elif isinstance(node, yaml.SequenceNode):
        value = loader.construct_sequence(node, deep=True)
    else:
        value = loader.construct_mapping(node, deep=True)

    # Ref is the one intrinsic without an Fn:: prefix.
    key = "Ref" if tag_suffix == "Ref" else f"Fn::{tag_suffix}"
    return {key: value}


CloudFormationLoader.add_multi_constructor("!", _construct_tag)


def load_template() -> dict[str, Any]:
    """The parsed template."""
    with TEMPLATE_PATH.open() as handle:
        return yaml.load(handle, Loader=CloudFormationLoader)


def resources() -> dict[str, Any]:
    return load_template()["Resources"]


def resource(logical_id: str) -> dict[str, Any]:
    return resources()[logical_id]


def resources_of_type(cfn_type: str) -> dict[str, Any]:
    return {name: body for name, body in resources().items() if body["Type"] == cfn_type}


def orders_table_properties() -> dict[str, Any]:
    return resource(ORDERS_TABLE_LOGICAL_ID)["Properties"]


def orders_table_schema() -> dict[str, Any]:
    """create_table kwargs taken from the template, minus the name.

    TableName is a `!Sub` on the stack name, which means nothing locally — tests
    supply their own. Everything that defines the *shape* comes from the
    template.
    """
    properties = orders_table_properties()
    return {
        "BillingMode": properties["BillingMode"],
        "KeySchema": properties["KeySchema"],
        "AttributeDefinitions": properties["AttributeDefinitions"],
        "GlobalSecondaryIndexes": properties["GlobalSecondaryIndexes"],
    }


def ttl_attribute_name() -> str:
    """The TTL attribute, so the fixture enables TTL on the same field."""
    return orders_table_properties()["TimeToLiveSpecification"]["AttributeName"]
