# Evidence

Captured from real runs, not transcribed. Regenerate with `bash docs/evidence/capture.sh`.

Every claim in the README maps to a test below. None of it touches AWS:
these are the guarantees the suite can prove on its own. The ones that
structurally cannot be proven locally — real condition expressions, IAM
scoping, CloudFront and S3 origin access — are captured against the
deployed stack in `docs/SMOKE_EVIDENCE.md` by `docs/evidence/smoke.sh`.

Captured on: 2026-08-13T08:45:59Z · Python 3.14.6

## "No table scans" — REQ-0012 / NFR-0003

Two independent checks: a static grep over `src/`, and a botocore call log
recording the DynamoDB operations actually issued. The last test in the list
fires a real `Scan` to prove the detector is not vacuous.

```
TestStaticNoScan::test_source_tree_is_not_empty PASSED [  9%]
TestStaticNoScan::test_no_source_file_mentions_scan PASSED [ 18%]
TestBehavioralNoScan::test_create_order_uses_only_a_transaction PASSED [ 27%]
TestBehavioralNoScan::test_get_order_issues_a_single_query PASSED [ 36%]
TestBehavioralNoScan::test_idempotency_lookup_is_a_get_item PASSED [ 45%]
TestBehavioralNoScan::test_every_list_pattern_uses_only_query[AP3] PASSED [ 54%]
TestBehavioralNoScan::test_every_list_pattern_uses_only_query[AP4] PASSED [ 63%]
TestBehavioralNoScan::test_every_list_pattern_uses_only_query[AP5] PASSED [ 72%]
TestBehavioralNoScan::test_transition_uses_a_conditional_update PASSED [ 81%]
TestBehavioralNoScan::test_a_full_workload_never_scans PASSED [ 90%]
TestBehavioralNoScan::test_the_hook_would_actually_catch_a_scan PASSED [100%]
============================== 11 passed in 1.07s ==============================
```

## "A retry cannot create a duplicate" — REQ-0010

The data-layer proof and the same guarantee as the client experiences it.

```
TestIdempotentCreate::test_same_key_twice_raises_duplicate PASSED [ 10%]
TestIdempotentCreate::test_same_key_twice_leaves_exactly_one_order PASSED [ 20%]
TestIdempotentCreate::test_retry_does_not_write_the_second_orders_rows PASSED [ 30%]
TestIdempotentCreate::test_different_keys_same_payload_create_distinct_orders PASSED [ 40%]
TestIdempotentCreate::test_reusing_an_order_id_with_a_new_key_is_rejected PASSED [ 50%]
TestReplaySemantics::test_second_identical_request_returns_200_not_201 PASSED [ 60%]
TestReplaySemantics::test_replay_returns_the_original_body PASSED [ 70%]
TestReplaySemantics::test_replay_creates_no_second_order PASSED [ 80%]
TestReplaySemantics::test_replay_wins_over_a_changed_payload PASSED [ 90%]
TestReplaySemantics::test_different_keys_create_different_orders PASSED [100%]
============================== 10 passed in 1.19s ==============================
```

## State machine — every ordered pair asserted (REQ-0006 / REQ-0007)

```
.......................................................                  [100%]
55 passed in 0.04s
ordered pairs asserted: 25
```

## Infrastructure assertions

The template and pipeline are checked as data: no IAM policy grants Scan,
no workflow reads a static AWS key, and the OIDC trust is pinned to one repo.

```
........................................................................ [ 97%]
...                                                                      [100%]
147 passed in 1.15s
```

## Coverage — NFR-0001 (gate: 90%)

```

Name                                    Stmts   Miss Branch BrPart  Cover   Missing
-----------------------------------------------------------------------------------
src/data/__init__.py                        0      0      0      0   100%
src/data/order_repository.py              166      0     40      0   100%
src/handlers/__init__.py                    0      0      0      0   100%
src/handlers/create_order.py               15      0      0      0   100%
src/handlers/dependencies.py               10      0      2      0   100%
src/handlers/get_order.py                   9      0      0      0   100%
src/handlers/list_customer_orders.py        9      0      0      0   100%
src/handlers/list_orders_by_status.py       9      0      0      0   100%
src/handlers/update_order_status.py        19      0      4      0   100%
src/services/__init__.py                    0      0      0      0   100%
src/services/order_service.py             145      0     52      0   100%
src/services/status_machine.py             12      0      0      0   100%
src/shared/__init__.py                      0      0      0      0   100%
src/shared/errors.py                       39      0      0      0   100%
src/shared/logging.py                      30      0      8      0   100%
src/shared/models.py                       53      0      0      0   100%
src/shared/requests.py                     37      0     12      0   100%
src/shared/responses.py                    43      0      4      0   100%
-----------------------------------------------------------------------------------
TOTAL                                     596      0    122      0   100%
Required test coverage of 90% reached. Total coverage: 100.00%
609 passed in 27.05s
```
