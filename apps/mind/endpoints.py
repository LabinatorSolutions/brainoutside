"""The brain's public surface — endpoint classes (REST + MCP + docs).

M1.8 adds the real read endpoints (get-index, list-notes, get-note,
get-lens, get-identity, get-raw). `ping` is the wiring smoke test:
authenticated liveness through the full registry path.
"""
from __future__ import annotations

from pydantic import BaseModel

from apps.core.ctx import Ctx
from apps.core.registry import Endpoint, endpoint


@endpoint(slug="ping", description="Authenticated liveness check for the brain server.")
class Ping(Endpoint):
    """Confirms auth + registry wiring end to end."""

    class Input(BaseModel):
        pass

    class Output(BaseModel):
        pong: bool
        service: str

    async def run(self, inp: Input, ctx: Ctx) -> Output:
        return self.Output(pong=True, service="my-brain-web-app")
