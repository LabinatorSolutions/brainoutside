"""`manage.py generate_postman` — registry → Postman v2.1 collection.

Walks `apps.core.registry.all()` and emits a JSON collection a
contributor can drop into Postman or Bruno to exercise every
endpoint with a couple of clicks. Each registered endpoint
becomes one request item with:

  - Method + URL (templated against `{{base_url}}`)
  - Bearer auth header reading `{{api_key}}`
  - Pretty-printed sample body built from Pydantic defaults (the
    same `_build_sample_input` the /docs/ page uses for its try-it
    panel — kept in lock-step deliberately)
  - Description folded in from the endpoint's `description=` arg or
    docstring first line

Output defaults to stdout (so `manage.py generate_postman > coll.json`
works) — pass `--output PATH` to write to a file.

Bruno collections share the same v2.1 shape with a different file
extension; the JSON we emit imports cleanly into both.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.core.registry import EndpointSpec, registry


class Command(BaseCommand):
    help = "Export the endpoint registry as a Postman v2.1 collection."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--output",
            type=Path,
            default=None,
            help="Write the collection to this path. Defaults to stdout.",
        )
        parser.add_argument(
            "--name",
            default="API + MCP Starter",
            help="Collection name shown in Postman.",
        )
        parser.add_argument(
            "--base-url",
            default="http://localhost:8000",
            help="Default value for the {{base_url}} variable.",
        )

    def handle(
        self,
        *args: object,
        output: Path | None,
        name: str,
        base_url: str,
        **options: object,
    ) -> None:
        specs = registry.all()
        if not specs:
            raise CommandError(
                "No endpoints registered. Did you run manage.py from the project root "
                "with LOCAL_APPS populated?"
            )

        collection = _build_collection(name=name, base_url=base_url, specs=specs)
        payload = json.dumps(collection, indent=2)

        if output is None:
            self.stdout.write(payload)
        else:
            output.write_text(payload, encoding="utf-8")
            self.stdout.write(
                self.style.SUCCESS(
                    f"Wrote {len(specs)} endpoint(s) to {output}."
                )
            )


def _build_collection(*, name: str, base_url: str, specs: list[EndpointSpec]) -> dict[str, Any]:
    return {
        "info": {
            "name": name,
            "description": (
                "Auto-generated from apps.core.registry. Regenerate any time "
                "via `manage.py generate_postman`."
            ),
            "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
        },
        "variable": [
            {"key": "base_url", "value": base_url, "type": "string"},
            {"key": "api_key", "value": "mcpsk_<your_api_key>", "type": "string"},
        ],
        "auth": {
            "type": "bearer",
            "bearer": [{"key": "token", "value": "{{api_key}}", "type": "string"}],
        },
        "item": [_request_item_for(spec) for spec in specs],
    }


def _request_item_for(spec: EndpointSpec) -> dict[str, Any]:
    sample_body = _build_sample_input(spec.input_model.model_json_schema())
    body_str = json.dumps(sample_body, indent=2) if sample_body else "{}"
    description = spec.description or ""
    if spec.deprecated:
        description = f"⚠ DEPRECATED. {description}".strip()
    if spec.credits_cost:
        description = (
            description + f"\n\nCharges {spec.credits_cost} credit(s) per call."
        ).strip()
    return {
        "name": f"{spec.method} /{spec.version}/{spec.slug}",
        "request": {
            "method": spec.method,
            "header": [
                {"key": "Content-Type", "value": "application/json"},
            ],
            "body": {
                "mode": "raw",
                "raw": body_str,
                "options": {"raw": {"language": "json"}},
            },
            "url": {
                "raw": f"{{{{base_url}}}}/api/{spec.version}/{spec.slug}",
                "host": ["{{base_url}}"],
                "path": ["api", spec.version, spec.slug],
            },
            "description": description,
        },
    }


def _build_sample_input(schema: dict) -> dict[str, Any]:
    """Mirror `apps/docs/services/endpoint_detail.py::_build_sample_input` —
    Pydantic defaults + zero-value placeholders for required-no-default
    fields. Kept here as its own copy so apps.core doesn't import from
    apps.docs (Contract 1: apps.core has no reverse deps on feature apps)."""
    sample: dict[str, Any] = {}
    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    for name, prop in properties.items():
        if "default" in prop and prop["default"] is not None:
            sample[name] = prop["default"]
        elif name in required:
            sample[name] = _zero_value_for(prop)
    return sample


def _zero_value_for(prop: dict) -> Any:
    t = prop.get("type")
    if t == "string":
        return ""
    if t == "integer":
        return 0
    if t == "number":
        return 0.0
    if t == "boolean":
        return False
    if t == "array":
        return []
    if t == "object":
        return {}
    return None
