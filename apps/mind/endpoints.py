"""The brain's dumb read layer — six endpoints serving tier-snapshot bytes.

Every endpoint (REST + MCP, one class each — the vendored registry):
1. resolves the caller's tier from its API key's consumer profile
   (no profile -> public; anonymous never reaches here),
2. serves ONLY from that tier's materialized snapshot,
3. answers "unknown ..." identically for missing and above-tier targets
   (existence is never revealed downward),
4. emits a `read` Event with the entity ids served.

The smart layer (`assemble-context`) arrives in M3.
"""
from __future__ import annotations

from asgiref.sync import sync_to_async
from pydantic import BaseModel, Field

from apps.brain.models import Entity
from apps.core.ctx import Ctx
from apps.core.registry import Endpoint, endpoint
from apps.events.models import emit
from apps.mind import files, tiers


def _tier(ctx: Ctx) -> str:
    return tiers.tier_for_credential(ctx.credential)


def _cred(ctx: Ctx):
    from apps.api_keys.models import APIKey

    return ctx.credential if isinstance(ctx.credential, APIKey) else None


def _visible_entities(tier: str):
    rank = tiers.TIER_ORDER[tier]
    return [e for e in Entity.objects.all() if tiers.TIER_ORDER.get(e.visibility, 2) <= rank]


class NoteRow(BaseModel):
    entity_id: str
    kind: str
    title: str
    description: str
    status: str
    date: str
    last_verified: str
    path: str
    topics: list[str]


def _row(e: Entity) -> NoteRow:
    return NoteRow(
        entity_id=e.entity_id,
        kind=e.kind,
        title=e.title,
        description=e.description,
        status=e.status,
        date=e.date,
        last_verified=e.last_verified.isoformat() if e.last_verified else "",
        path=e.path,
        topics=list(e.topics),
    )


@endpoint(slug="ping", description="Authenticated liveness check for the brain server.")
class Ping(Endpoint):
    class Input(BaseModel):
        pass

    class Output(BaseModel):
        pong: bool
        service: str
        tier: str

    async def run(self, inp: Input, ctx: Ctx) -> Output:
        return await sync_to_async(self._work, thread_sensitive=True)(inp, ctx)

    def _work(self, inp: Input, ctx: Ctx) -> Output:
        return self.Output(pong=True, service="my-brain-web-app", tier=_tier(ctx))


@endpoint(
    slug="get-index",
    description="The brain's index, generated for your visibility tier. Read this first.",
)
class GetIndex(Endpoint):
    class Input(BaseModel):
        pass

    class Output(BaseModel):
        tier: str
        index: str

    async def run(self, inp: Input, ctx: Ctx) -> Output:
        return await sync_to_async(self._work, thread_sensitive=True)(inp, ctx)

    def _work(self, inp: Input, ctx: Ctx) -> Output:
        tier = _tier(ctx)
        content = files.read(tier, "INDEX.md")
        emit("read", consumer=_cred(ctx), entity_ids=["INDEX"], endpoint="get-index", tier=tier)
        return self.Output(tier=tier, index=content)


@endpoint(
    slug="list-notes",
    description=(
        "List brain entities visible to you, filterable by kind "
        "(take/story/lesson/fact/project/identity/catalog/lens), topic, "
        "project, and status."
    ),
)
class ListNotes(Endpoint):
    class Input(BaseModel):
        kind: str = Field(default="", description="Filter by kind; empty = all.")
        topic: str = Field(default="", description="Filter by taxonomy topic; empty = all.")
        project: str = Field(default="", description="Filter by touched project; empty = all.")
        status: str = Field(default="current", description="'current' (default), 'superseded', or '' for all.")

    class Output(BaseModel):
        tier: str
        count: int
        notes: list[NoteRow]

    async def run(self, inp: Input, ctx: Ctx) -> Output:
        return await sync_to_async(self._work, thread_sensitive=True)(inp, ctx)

    def _work(self, inp: Input, ctx: Ctx) -> Output:
        tier = _tier(ctx)
        ents = _visible_entities(tier)
        if inp.kind:
            ents = [e for e in ents if e.kind == inp.kind]
        if inp.topic:
            ents = [e for e in ents if inp.topic in e.topics]
        if inp.project:
            ents = [e for e in ents if inp.project in e.projects]
        if inp.status == "current":
            ents = [e for e in ents if e.status != "superseded"]
        elif inp.status == "superseded":
            ents = [e for e in ents if e.status == "superseded"]
        rows = [_row(e) for e in sorted(ents, key=lambda x: (x.kind, x.entity_id))]
        emit(
            "read",
            consumer=_cred(ctx),
            entity_ids=[r.entity_id for r in rows][:100],
            endpoint="list-notes",
            tier=tier,
            filters=inp.model_dump(),
            count=len(rows),
        )
        return self.Output(tier=tier, count=len(rows), notes=rows)


@endpoint(slug="get-note", description="Full markdown of one entity by its entity_id.")
class GetNote(Endpoint):
    class Input(BaseModel):
        entity_id: str = Field(min_length=1, max_length=200)

    class Output(BaseModel):
        tier: str
        entity_id: str
        path: str
        content: str

    async def run(self, inp: Input, ctx: Ctx) -> Output:
        return await sync_to_async(self._work, thread_sensitive=True)(inp, ctx)

    def _work(self, inp: Input, ctx: Ctx) -> Output:
        tier = _tier(ctx)
        e = Entity.objects.filter(entity_id=inp.entity_id).first()
        if e is None or not tiers.allows(tier, e.visibility):
            if e is not None:
                emit("auth_denied", consumer=_cred(ctx), entity_ids=[inp.entity_id], surface="get-note", tier=tier)
            raise ValueError(f"unknown entity: {inp.entity_id}")
        content = files.read(tier, e.path)
        emit("read", consumer=_cred(ctx), entity_ids=[e.entity_id], endpoint="get-note", tier=tier)
        return self.Output(tier=tier, entity_id=e.entity_id, path=e.path, content=content)


@endpoint(slug="get-lens", description="A lens file — a named retrieval scope (topics, types, ceiling).")
class GetLens(Endpoint):
    class Input(BaseModel):
        name: str = Field(min_length=1, max_length=100, description="Lens name, e.g. 'self-hosting'.")

    class Output(BaseModel):
        tier: str
        name: str
        content: str

    async def run(self, inp: Input, ctx: Ctx) -> Output:
        return await sync_to_async(self._work, thread_sensitive=True)(inp, ctx)

    def _work(self, inp: Input, ctx: Ctx) -> Output:
        tier = _tier(ctx)
        e = Entity.objects.filter(entity_id=f"lens-{inp.name}").first()
        if e is None or not tiers.allows(tier, e.visibility):
            raise ValueError(f"unknown lens: {inp.name}")
        content = files.read(tier, e.path)
        emit("read", consumer=_cred(ctx), entity_ids=[e.entity_id], endpoint="get-lens", tier=tier)
        return self.Output(tier=tier, name=inp.name, content=content)


class IdentityFile(BaseModel):
    entity_id: str
    path: str
    content: str


@endpoint(
    slug="get-identity",
    description="The identity core visible at your tier — load before any voice/context task.",
)
class GetIdentity(Endpoint):
    class Input(BaseModel):
        pass

    class Output(BaseModel):
        tier: str
        files: list[IdentityFile]

    async def run(self, inp: Input, ctx: Ctx) -> Output:
        return await sync_to_async(self._work, thread_sensitive=True)(inp, ctx)

    def _work(self, inp: Input, ctx: Ctx) -> Output:
        tier = _tier(ctx)
        out: list[IdentityFile] = []
        for e in _visible_entities(tier):
            if e.kind != "identity":
                continue
            out.append(IdentityFile(entity_id=e.entity_id, path=e.path, content=files.read(tier, e.path)))
        emit(
            "read",
            consumer=_cred(ctx),
            entity_ids=[f.entity_id for f in out],
            endpoint="get-identity",
            tier=tier,
        )
        return self.Output(tier=tier, files=out)


@endpoint(
    slug="get-raw",
    description=(
        "A raw/ archive file, reachable ONLY when a note visible to you links "
        "it. Use for depth after reading the linking note."
    ),
)
class GetRaw(Endpoint):
    class Input(BaseModel):
        path: str = Field(min_length=5, max_length=300, description="e.g. 'raw/toolerbox-tools-catalog.md'")

    class Output(BaseModel):
        tier: str
        path: str
        content: str

    async def run(self, inp: Input, ctx: Ctx) -> Output:
        return await sync_to_async(self._work, thread_sensitive=True)(inp, ctx)

    def _work(self, inp: Input, ctx: Ctx) -> Output:
        tier = _tier(ctx)
        if not inp.path.startswith("raw/"):
            raise ValueError("get-raw serves only raw/ paths.")
        try:
            content = files.read(tier, inp.path)
        except files.SnapshotMiss:
            emit("auth_denied", consumer=_cred(ctx), entity_ids=[], surface="get-raw", tier=tier, path=inp.path)
            raise ValueError(f"unknown path: {inp.path}") from None
        emit("read", consumer=_cred(ctx), entity_ids=[inp.path], endpoint="get-raw", tier=tier)
        return self.Output(tier=tier, path=inp.path, content=content)
