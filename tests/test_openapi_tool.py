"""Tests for the openapi_lookup skill (local spec file — no network)."""

from __future__ import annotations

import json
from typing import Any

from halia.skills.openapi_tool import OpenApiLookup

_SPEC = {
    "openapi": "3.0.0",
    "servers": [{"url": "http://127.0.0.1:3155/api/ai-support"}],
    "paths": {
        "/change-mobile-number/webhook/trigger": {
            "post": {
                "operationId": "triggerChangeMobileNumber",
                "summary": "Trigger CMN",
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {"schema": {"$ref": "#/components/schemas/CMNTrigger"}}
                    },
                },
            }
        },
        "/accounts/profile": {
            "get": {
                "operationId": "findAccount",
                "summary": "Find account by phone",
                "parameters": [
                    {"name": "phone", "in": "query", "required": True, "schema": {"type": "string"}}
                ],
            }
        },
    },
    "components": {
        "schemas": {
            "CMNTrigger": {
                "type": "object",
                "required": ["currentNumber", "newNumber", "zendeskTicketId"],
                "properties": {
                    "currentNumber": {"type": "string"},
                    "newNumber": {"type": "string"},
                    "zendeskTicketId": {"type": "string"},
                    "topUpMethodUsed": {"type": "array", "items": {"type": "string"}},
                },
            }
        }
    },
}


def _spec_file(tmp_path: Any) -> str:
    p = tmp_path / "swagger.json"
    p.write_text(json.dumps(_SPEC))
    return str(p)


def test_index_lists_operations(tmp_path: Any) -> None:
    out = OpenApiLookup().run({"spec": _spec_file(tmp_path)})
    assert "POST /change-mobile-number/webhook/trigger" in out
    assert "triggerChangeMobileNumber" in out
    assert "GET /accounts/profile" in out and "findAccount" in out
    assert "http://127.0.0.1:3155/api/ai-support" in out  # servers surfaced


def test_operation_id_returns_path_and_params(tmp_path: Any) -> None:
    out = OpenApiLookup().run({"spec": _spec_file(tmp_path), "operation_id": "findAccount"})
    assert "GET /accounts/profile" in out
    assert "phone (query, required, string)" in out


def test_operation_id_returns_body_schema_and_example(tmp_path: Any) -> None:
    out = OpenApiLookup().run(
        {"spec": _spec_file(tmp_path), "operation_id": "triggerChangeMobileNumber"}
    )
    assert "request body (application/json, required)" in out
    assert "currentNumber (string, required)" in out
    assert "topUpMethodUsed (array, optional)" in out
    # example JSON carries the real field names (so the model stops inventing the shape)
    assert "example:" in out
    example_json = out.split("example:", 1)[1]
    parsed = json.loads(example_json[example_json.index("{") : example_json.rindex("}") + 1])
    assert set(["currentNumber", "newNumber", "zendeskTicketId", "topUpMethodUsed"]) <= set(parsed)


def test_case_insensitive_id_and_near_miss_hint(tmp_path: Any) -> None:
    # exact match is case-insensitive
    assert "GET /accounts/profile" in OpenApiLookup().run(
        {"spec": _spec_file(tmp_path), "operation_id": "FINDACCOUNT"}
    )
    # a wrong id suggests near matches
    out = OpenApiLookup().run({"spec": _spec_file(tmp_path), "operation_id": "account"})
    assert "no operation with operationId 'account'" in out
    assert "findAccount" in out  # "Did you mean"


def test_query_and_path_filters(tmp_path: Any) -> None:
    q = OpenApiLookup().run({"spec": _spec_file(tmp_path), "query": "account"})
    assert "findAccount" in q and "triggerChangeMobileNumber" not in q
    p = OpenApiLookup().run({"spec": _spec_file(tmp_path), "path": "/accounts"})
    assert "GET /accounts/profile" in p and "phone (query" in p


def test_errors(tmp_path: Any) -> None:
    assert "spec" in OpenApiLookup().run({"spec": "  "})
    assert "not a file" in OpenApiLookup().run({"spec": str(tmp_path / "nope.json")})
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert "not valid JSON" in OpenApiLookup().run({"spec": str(bad)})


def test_wired_and_safe(tmp_path: Any) -> None:
    from halia.presets import get_preset
    from halia.skills import available_skills, default_registry

    assert OpenApiLookup().dangerous is False
    assert OpenApiLookup().untrusted is True
    assert "openapi_lookup" in available_skills()
    assert default_registry().get("openapi_lookup") is not None
    assert "openapi_lookup" in get_preset("qa").skills
