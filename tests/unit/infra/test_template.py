"""Assertions about `template.yaml` itself.

`sam validate --lint` proves the template is well-formed CloudFormation. It says
nothing about whether the template describes *this* system — that the key design
matches DYNAMODB_DESIGN.md, that routes match API_SPEC.md, or that no policy
quietly grants Scan. That is what these check.
"""

from __future__ import annotations

import importlib
import json

import pytest

from shared.models import OrderStatus
from tests.template import (
    load_template,
    orders_table_properties,
    resource,
    resources_of_type,
)

FUNCTION_TYPE = "AWS::Serverless::Function"

EXPECTED_FUNCTIONS = {
    "CreateOrderFunction": ("POST", "/api/orders"),
    "GetOrderFunction": ("GET", "/api/orders/{orderId}"),
    "ListCustomerOrdersFunction": ("GET", "/api/customers/{customerId}/orders"),
    "ListOrdersByStatusFunction": ("GET", "/api/orders"),
    "UpdateOrderStatusFunction": ("PATCH", "/api/orders/{orderId}"),
}


def route_of(function_body: dict) -> tuple[str, str]:
    (event,) = function_body["Properties"]["Events"].values()
    return event["Properties"]["Method"].upper(), event["Properties"]["Path"]


def policy_statements(function_body: dict) -> list[dict]:
    statements: list[dict] = []
    for policy in function_body["Properties"].get("Policies", []):
        statements.extend(policy.get("Statement", []))
    return statements


def actions_of(statement: dict) -> list[str]:
    action = statement["Action"]
    return [action] if isinstance(action, str) else list(action)


# ---------------------------------------------------------------- the table ---


class TestOrdersTable:
    def test_partition_and_sort_keys_match_the_design(self) -> None:
        keys = {k["AttributeName"]: k["KeyType"] for k in orders_table_properties()["KeySchema"]}
        assert keys == {"PK": "HASH", "SK": "RANGE"}

    def test_both_sparse_indexes_are_defined(self) -> None:
        names = {g["IndexName"] for g in orders_table_properties()["GlobalSecondaryIndexes"]}
        assert names == {"GSI1", "GSI2"}

    def test_gsi_keys_match_the_design(self) -> None:
        by_name = {g["IndexName"]: g for g in orders_table_properties()["GlobalSecondaryIndexes"]}
        expected = {
            "GSI1": {"GSI1PK": "HASH", "GSI1SK": "RANGE"},
            "GSI2": {"GSI2PK": "HASH", "GSI2SK": "RANGE"},
        }
        for name, want in expected.items():
            got = {k["AttributeName"]: k["KeyType"] for k in by_name[name]["KeySchema"]}
            assert got == want, f"{name} drifted from DYNAMODB_DESIGN.md § 2"

    def test_ttl_is_enabled_on_the_documented_attribute(self) -> None:
        # Without this the idempotency records accumulate forever.
        ttl = orders_table_properties()["TimeToLiveSpecification"]
        assert ttl["AttributeName"] == "expiresAt"
        assert ttl["Enabled"] is True

    def test_billing_is_on_demand(self) -> None:
        # NFR-0006: no capacity planning, free-tier friendly.
        assert orders_table_properties()["BillingMode"] == "PAY_PER_REQUEST"

    def test_every_declared_attribute_is_used_by_a_key(self) -> None:
        properties = orders_table_properties()
        declared = {a["AttributeName"] for a in properties["AttributeDefinitions"]}

        used = {k["AttributeName"] for k in properties["KeySchema"]}
        for gsi in properties["GlobalSecondaryIndexes"]:
            used |= {k["AttributeName"] for k in gsi["KeySchema"]}

        assert declared == used


# ------------------------------------------------------------- the functions ---


class TestFunctions:
    def test_there_are_exactly_five(self) -> None:
        assert set(resources_of_type(FUNCTION_TYPE)) == set(EXPECTED_FUNCTIONS)

    @pytest.mark.parametrize(("logical_id", "route"), sorted(EXPECTED_FUNCTIONS.items()))
    def test_each_is_wired_to_its_documented_route(self, logical_id: str, route) -> None:
        assert route_of(resource(logical_id)) == route

    @pytest.mark.parametrize("logical_id", sorted(EXPECTED_FUNCTIONS))
    def test_handler_module_and_attribute_actually_exist(self, logical_id: str) -> None:
        """A typo here deploys fine and fails on the first invocation."""
        handler_path = resource(logical_id)["Properties"]["Handler"]
        module_name, _, attribute = handler_path.rpartition(".")

        module = importlib.import_module(module_name)

        assert callable(getattr(module, attribute))

    def test_runtime_and_architecture_are_set_globally(self) -> None:
        globals_ = load_template()["Globals"]["Function"]
        assert globals_["Runtime"] == "python3.14"
        assert globals_["Architectures"] == ["arm64"]

    def test_table_name_is_passed_by_environment(self) -> None:
        # OrderRepository resolves the table from this variable.
        variables = load_template()["Globals"]["Function"]["Environment"]["Variables"]
        assert "ORDERS_TABLE_NAME" in variables

    def test_code_uri_is_the_src_root(self) -> None:
        # Must match pythonpath=src in pyproject.toml, or imports that work under
        # pytest break in Lambda.
        assert load_template()["Globals"]["Function"]["CodeUri"] == "src/"

    def test_runtime_requirements_sit_inside_the_code_uri(self) -> None:
        """SAM only reads requirements.txt from the CodeUri directory.

        At the repo root it is skipped with a log line and nothing else, so the
        deployment package ships with no dependencies and every import of `ulid`
        fails on the first invocation. `sam validate` cannot catch this.
        """
        from tests.template import TEMPLATE_PATH

        code_uri = load_template()["Globals"]["Function"]["CodeUri"]
        requirements = TEMPLATE_PATH.parent / code_uri / "requirements.txt"

        assert requirements.is_file(), (
            f"expected runtime dependencies at {requirements}; SAM ignores a "
            "requirements.txt outside CodeUri"
        )

    def test_runtime_requirements_list_the_non_stdlib_imports(self) -> None:
        from tests.template import TEMPLATE_PATH

        code_uri = load_template()["Globals"]["Function"]["CodeUri"]
        listed = (TEMPLATE_PATH.parent / code_uri / "requirements.txt").read_text()

        # ulid is imported by order_service and is NOT in the Lambda runtime.
        assert "python-ulid" in listed
        assert "boto3" in listed

    def test_routes_cover_every_documented_endpoint(self) -> None:
        routes = {route_of(body) for body in resources_of_type(FUNCTION_TYPE).values()}
        assert routes == set(EXPECTED_FUNCTIONS.values())

    def test_all_paths_sit_under_the_api_prefix(self) -> None:
        # REQ-0021: CloudFront forwards /api/* untouched, so every route must
        # already carry the prefix — no path rewriting anywhere.
        for _, path in EXPECTED_FUNCTIONS.values():
            assert path.startswith("/api/")


# --------------------------------------------------------------------- IAM ---


class TestLeastPrivilege:
    """NFR-0004, and REQ-0012 enforced in IAM rather than only in code."""

    @pytest.mark.parametrize("logical_id", sorted(EXPECTED_FUNCTIONS))
    def test_no_function_may_scan(self, logical_id: str) -> None:
        for statement in policy_statements(resource(logical_id)):
            for action in actions_of(statement):
                assert "Scan" not in action, f"{logical_id} is granted {action}"

    @pytest.mark.parametrize("logical_id", sorted(EXPECTED_FUNCTIONS))
    def test_no_function_uses_a_wildcard_resource(self, logical_id: str) -> None:
        for statement in policy_statements(resource(logical_id)):
            resources = statement["Resource"]
            for entry in [resources] if isinstance(resources, dict | str) else resources:
                assert entry != "*", f"{logical_id} has a wildcard resource"

    @pytest.mark.parametrize("logical_id", sorted(EXPECTED_FUNCTIONS))
    def test_every_function_has_an_explicit_policy(self, logical_id: str) -> None:
        assert policy_statements(resource(logical_id)), f"{logical_id} has no scoped policy"

    @pytest.mark.parametrize(
        "logical_id",
        ["GetOrderFunction", "ListCustomerOrdersFunction", "ListOrdersByStatusFunction"],
    )
    def test_read_only_functions_cannot_write(self, logical_id: str) -> None:
        write_actions = {
            "PutItem",
            "UpdateItem",
            "DeleteItem",
            "TransactWriteItems",
            "BatchWriteItem",
        }
        for statement in policy_statements(resource(logical_id)):
            for action in actions_of(statement):
                assert action.split(":")[-1] not in write_actions

    def test_list_functions_are_scoped_to_their_index_only(self) -> None:
        # A list function that could read the base table could sidestep the
        # index design entirely.
        for logical_id, index in [
            ("ListCustomerOrdersFunction", "GSI1"),
            ("ListOrdersByStatusFunction", "GSI2"),
        ]:
            (statement,) = policy_statements(resource(logical_id))
            assert index in json.dumps(statement["Resource"])


# ----------------------------------------------------- frontend and delivery ---


class TestFrontendDelivery:
    def _distribution_config(self) -> dict:
        return resource("Distribution")["Properties"]["DistributionConfig"]

    def test_bucket_blocks_all_public_access(self) -> None:
        # REQ-0020: only CloudFront may read it.
        block = resource("FrontendBucket")["Properties"]["PublicAccessBlockConfiguration"]
        assert all(block.values())

    def test_bucket_policy_only_trusts_cloudfront(self) -> None:
        (statement,) = resource("FrontendBucketPolicy")["Properties"]["PolicyDocument"]["Statement"]
        assert statement["Principal"] == {"Service": "cloudfront.amazonaws.com"}

    def test_bucket_policy_is_scoped_to_this_distribution(self) -> None:
        # Without the SourceArn condition, any CloudFront distribution in any
        # account could read the bucket.
        (statement,) = resource("FrontendBucketPolicy")["Properties"]["PolicyDocument"]["Statement"]
        assert "AWS:SourceArn" in statement["Condition"]["StringEquals"]
        assert "Distribution" in json.dumps(statement["Condition"])

    def test_s3_origin_uses_origin_access_control(self) -> None:
        origins = {o["Id"]: o for o in self._distribution_config()["Origins"]}
        assert "OriginAccessControlId" in origins["FrontendS3"]

    def test_api_behaviour_routes_to_the_api_origin(self) -> None:
        # REQ-0021: one domain for UI and API, so no CORS anywhere.
        (behaviour,) = self._distribution_config()["CacheBehaviors"]
        assert behaviour["PathPattern"] == "/api/*"
        assert behaviour["TargetOriginId"] == "OrdersApi"

    def test_api_behaviour_allows_every_method_the_api_uses(self) -> None:
        (behaviour,) = self._distribution_config()["CacheBehaviors"]
        assert {"GET", "POST", "PATCH"} <= set(behaviour["AllowedMethods"])

    def test_api_responses_are_never_cached(self) -> None:
        # CachingDisabled — a cached order listing would show stale statuses.
        (behaviour,) = self._distribution_config()["CacheBehaviors"]
        assert behaviour["CachePolicyId"] == "4135ea2d-6df8-44a3-9df3-4b5a84be39ad"

    def test_default_behaviour_serves_the_static_site(self) -> None:
        assert self._distribution_config()["DefaultCacheBehavior"]["TargetOriginId"] == "FrontendS3"

    def test_http_is_not_served(self) -> None:
        config = self._distribution_config()
        assert config["DefaultCacheBehavior"]["ViewerProtocolPolicy"] == "redirect-to-https"
        for behaviour in config["CacheBehaviors"]:
            assert behaviour["ViewerProtocolPolicy"] in {"https-only", "redirect-to-https"}


class TestObservability:
    def test_api_access_logging_is_enabled(self) -> None:
        # NFR-0005 explicitly requires API Gateway access logging.
        settings = resource("HttpApi")["Properties"]["AccessLogSettings"]
        assert settings["DestinationArn"]
        assert settings["Format"]

    def test_access_log_format_is_json(self) -> None:
        settings = resource("HttpApi")["Properties"]["AccessLogSettings"]
        assert settings["Format"].strip().startswith("{")

    def test_access_log_records_the_request_id(self) -> None:
        # The same ID the handlers echo in X-Request-Id, so an access-log line
        # can be joined to the application logs for that request.
        assert (
            "$context.requestId" in resource("HttpApi")["Properties"]["AccessLogSettings"]["Format"]
        )

    def test_stage_takes_no_url_prefix(self) -> None:
        # A named stage would put /Prod in the path and break /api/* forwarding.
        assert resource("HttpApi")["Properties"]["StageName"] == "$default"


class TestTemplateHygiene:
    def test_environment_parameter_is_constrained(self) -> None:
        parameter = load_template()["Parameters"]["Environment"]
        assert parameter["AllowedValues"] == ["dev", "prod"]

    def test_log_level_parameter_accepts_only_real_levels(self) -> None:
        parameter = load_template()["Parameters"]["LogLevel"]
        assert set(parameter["AllowedValues"]) == {"DEBUG", "INFO", "WARNING", "ERROR"}

    def test_outputs_expose_what_the_pipeline_needs(self) -> None:
        # deploy.yml (Phase 5) syncs the bucket and invalidates the distribution.
        outputs = load_template()["Outputs"]
        assert {"SiteUrl", "FrontendBucketName", "OrdersTableName", "DistributionId"} <= set(
            outputs
        )

    def test_status_values_in_the_template_are_real_statuses(self) -> None:
        # Guards against a hardcoded status appearing in a future policy or
        # condition and drifting from the enum.
        rendered = json.dumps(load_template())
        for token in ("STATUS#", "PLACED#"):
            if token in rendered:
                assert any(status.value in rendered for status in OrderStatus)
