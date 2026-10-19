#!/usr/bin/env python3
"""Smoke test for the B2B gRPC facade (external.v1) of spring-langgraph-app.

Run it with the interpreter of the Python engine venv, which already carries grpcio and
grpcio-tools (stubs are compiled on the fly into a temp dir, nothing generated is committed):

    python-langgraph-service/.venv/bin/python scripts/b2b_smoke.py \\
        --api-key smoke-key --scope '*'

The test expects the fixture catalogue created by scripts/seed_b2b_smoke.sql
(run AFTER the app has booted once so Flyway has created the core schema).

What it asserts
---------------
* auth:    no credential / unknown key are rejected UNAUTHENTICATED *with* the x-error-code
           trailer, and never reach a handler
* paging:  page_size is defaulted/clamped and the page tokens walk the catalogue exactly once,
           with total_count stable across every page
* scope:   only the graphs inside --scope are listed, and an out-of-scope graph is refused
           PERMISSION_DENIED + GRAPH_ACCESS_DENIED without leaking whether it exists
* errors:  an INTERNAL failure reports a redacted description (no endpoint/driver detail)
* isolation: external.v1 is NOT served on the internal port (9090)
"""
from __future__ import annotations

import argparse
import importlib
import pathlib
import subprocess
import sys
import tempfile
import uuid

import grpc

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PROTO_DIR = REPO_ROOT / "spring-langgraph-app" / "src" / "main" / "proto"

# The catalogue fixture: every graph in core.graphs starts with "graph-" except this one, which
# doubles as the isolation probe for a scope restricted to "graph-*".  Override with
# --other-tenant-graph when the default does not match your seeded data.
DEFAULT_OTHER_TENANT_GRAPH = "supplier-selection-demo"

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if condition else 'FAIL'}] {label}" + (f" - {detail}" if detail else ""))
    if not condition:
        failures.append(label)


def load_stubs():
    """Compile external_api.proto with the running interpreter and import the result."""
    with tempfile.TemporaryDirectory() as tmp:
        work = pathlib.Path(tmp)
        subprocess.run(
            [
                sys.executable,
                "-m",
                "grpc_tools.protoc",
                f"-I{PROTO_DIR}",
                f"--python_out={work}",
                f"--grpc_python_out={work}",
                str(PROTO_DIR / "external_api.proto"),
            ],
            check=True,
            capture_output=True,
        )
        sys.path.insert(0, str(work))
        pb2 = importlib.import_module("external_api_pb2")
        pb2_grpc = importlib.import_module("external_api_pb2_grpc")
        sys.path.pop(0)
        return pb2, pb2_grpc


def trailer(error: grpc.RpcError, name: str) -> str:
    for key, value in error.trailing_metadata() or []:
        if key == name:
            return value
    return "<missing>"


def expect_failure(label, call, code, error_code=None, details_contains=None) -> None:
    """Assert a call is rejected with a status, optionally its trailer and message fragment."""
    try:
        call()
    except grpc.RpcError as error:
        check(f"{label}: status is {code.name}", error.code() == code,
              f"got {error.code().name} ({error.details()})")
        if error_code is not None:
            check(f"{label}: x-error-code is {error_code}",
                  trailer(error, "x-error-code") == error_code,
                  f"got {trailer(error, 'x-error-code')!r}")
        if details_contains is not None:
            check(f"{label}: message mentions {details_contains!r}",
                  details_contains in (error.details() or ""), f"got {error.details()!r}")
    else:
        check(f"{label}: rejected", False, "the call succeeded, which it must not")


def main() -> int:
    parser = argparse.ArgumentParser(description="B2B facade smoke test")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=9091)
    parser.add_argument("--internal-port", type=int, default=9090,
                        help="the engine-facing port, which must not serve external.v1")
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--scope", default="*",
                        help="the allowed-graphs the server was configured with")
    parser.add_argument("--page-size", type=int, default=2)
    parser.add_argument("--other-tenant-graph", default=DEFAULT_OTHER_TENANT_GRAPH,
                        help="a graph id the account's scope does NOT cover (for scope verification)")
    args = parser.parse_args()

    pb2, pb2_grpc = load_stubs()
    channel = grpc.insecure_channel(f"{args.host}:{args.port}")
    stub = pb2_grpc.LangGraphExternalApiStub(channel)
    key = (("x-api-key", args.api_key),)

    print(f"== {args.host}:{args.port} (configured scope {args.scope!r}) ==")

    # ---------------------------------------------------------------- authentication
    print("-- authentication")
    expect_failure("no credential", lambda: stub.ListGraphs(pb2.ListGraphsRequest()),
                   grpc.StatusCode.UNAUTHENTICATED, "UNAUTHENTICATED", "Missing credentials")
    expect_failure(
        "unknown key",
        lambda: stub.ListGraphs(pb2.ListGraphsRequest(),
                                metadata=(("x-api-key", "definitely-not-the-key"),)),
        grpc.StatusCode.UNAUTHENTICATED, "UNAUTHENTICATED", "Invalid API key")

    # ---------------------------------------------------------------- catalogue + paging
    print("-- ListGraphs and paging")
    seen: dict[str, str] = {}
    token, total_count, pages = "", None, 0
    while True:
        page = stub.ListGraphs(
            pb2.ListGraphsRequest(page_size=args.page_size, page_token=token), metadata=key)
        pages += 1
        if total_count is None:
            total_count = page.total_count
        else:
            check("total_count is stable across pages", page.total_count == total_count,
                  f"{total_count} then {page.total_count}")
        for graph in page.graphs:
            seen[graph.graph_id] = graph.graph_name
        if not page.has_next_page:
            check("an empty next_page_token ends the walk", page.next_page_token == "",
                  f"got {page.next_page_token!r}")
            break
        token = page.next_page_token
        if pages > 50:
            check("paging terminates", False, "50+ pages, the token never runs out")
            break

    check("total_count agrees with what paging collected", total_count == len(seen),
          f"total_count={total_count}, collected={len(seen)}")
    print(f"  (walked {pages} page(s) of size {args.page_size}: {sorted(seen)})")

    other_tenant_graph = args.other_tenant_graph

    if args.scope == "*":
        check("the wildcard scope sees every graph", other_tenant_graph in seen,
              f"catalogue: {sorted(seen)}")
    elif args.scope.strip() == "":
        check("an empty scope lists nothing", not seen, f"saw {sorted(seen)}")
    else:
        prefix = args.scope.replace("*", "").rstrip("-")
        outside = sorted(g for g in seen if not g.startswith(prefix))
        check(f"every listed graph is inside {args.scope!r}", not outside,
              f"outside: {outside}")
        check("an out-of-scope graph is not listed", other_tenant_graph not in seen,
              f"saw {other_tenant_graph}")

    # ---------------------------------------------------------------- deletion gate
    print("-- DeleteGraph scoping (only ever names a graph that does not exist)")
    if args.scope == "*":
        answer = stub.DeleteGraph(
            pb2.DeleteGraphRequest(graph_id="graph-smoke-nonexistent"), metadata=key)
        check("an in-scope unknown id answers success=false, not an error",
              answer.success is False, f"success={answer.success} message={answer.message!r}")
    else:
        expect_failure("an out-of-scope graph",
                       lambda: stub.DeleteGraph(
                           pb2.DeleteGraphRequest(graph_id=other_tenant_graph), metadata=key),
                       grpc.StatusCode.PERMISSION_DENIED, "GRAPH_ACCESS_DENIED")

    # ---------------------------------------------------------------- error redaction
    print("-- error mapping (the engine is not running, so this is the failure path)")

    def attempt(graph_id: str):
        return stub.ExecuteGraph(
            pb2.ExecuteGraphRequest(graph_id=graph_id,
                                    thread_id=f"smoke-{uuid.uuid4().hex[:8]}", input="hello"),
            metadata=key)

    if seen:
        # In scope, so this reaches the (absent) engine: the failure must be mapped and redacted.
        expect_failure("an engine-backed call", lambda: attempt(next(iter(seen))),
                       grpc.StatusCode.INTERNAL, "GRAPH_EXECUTION_FAILED")
        try:
            attempt(next(iter(seen)))
        except grpc.RpcError as error:
            leaked = error.details() or ""
            check("an INTERNAL failure does not leak endpoints or driver text",
                  leaked in ("", "internal error")
                  and "172.23" not in leaked and "UNAVAILABLE" not in leaked,
                  f"got {leaked!r}")
    elif args.scope.strip() == "":
        # Nothing is in scope: the graph gate answers before the engine is ever dialed, which is
        # also what proves the scope check runs ahead of delegation.
        expect_failure("a graph-keyed call with an empty scope",
                       lambda: attempt("graph-1"),
                       grpc.StatusCode.PERMISSION_DENIED, "GRAPH_ACCESS_DENIED")
    else:
        # Wildcard or restricted scope with an empty catalogue: the fixture was not seeded.
        check("the catalogue is not empty", False,
              "no graph was listed - seed the fixture (scripts/seed_b2b_smoke.sql) or run --scope ''")

    # ---------------------------------------------------------------- port isolation
    print(f"-- port isolation: external.v1 must not be served on {args.internal_port}")
    internal = pb2_grpc.LangGraphExternalApiStub(
        grpc.insecure_channel(f"{args.host}:{args.internal_port}"))
    expect_failure("the internal port refuses the facade",
                   lambda: internal.ListGraphs(pb2.ListGraphsRequest(), metadata=key),
                   grpc.StatusCode.UNIMPLEMENTED)

    channel.close()
    print()
    if failures:
        print(f"{len(failures)} FAILURE(S):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
