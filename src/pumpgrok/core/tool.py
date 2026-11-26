"""Tool contracts: specs, envelopes, and the binding decorator (interfaces.md 2.5)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from typing import Any, Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from pumpgrok.core.errors import DataBlind, DataStale
from pumpgrok.core.side_effect import SideEffect

TOOL_NAME_RE = r"^[a-z][a-z0-9_]*(\.[a-z][a-z0-9_]*)+$"


class RetryPolicy(BaseModel):
    """Retry policy (D5). `base_s`/`max_s` are seconds, never money.

    `attempts > 1` is READ-only: write tools keep `attempts=1` (catalog "retry
    none"). Timeouts retry in the registry; 5xx/429 retry at the transport
    client, which reads the same policy.
    """

    model_config = ConfigDict(frozen=True)

    attempts: int = Field(default=1, ge=1, le=10)
    base_s: float = Field(default=0.5, ge=0.0)  # seconds, not money
    max_s: float = Field(default=5.0, ge=0.0)
    jitter: bool = True
    retry_on: tuple[str, ...] = ("timeout", "5xx", "429")

    @classmethod
    def none(cls) -> RetryPolicy:
        return cls(attempts=1)


#: Where a tool's data comes from (ROADMAP P1-2, DECISIONS D-011). Declared per tool on
#: ``ToolSpec.quality`` and stamped onto ``ToolMeta`` by :func:`tool`.
#:
#: - ``ledger``: the local paper ledger; observed when fetched.
#: - ``onchain``: decoded Solana RPC data.
#: - ``onchain_assumed``: decoded with the ``[ASSUMED]`` curve layout (``curve_layout.py``).
#: - ``simulated``: paper execution model output, never a real fill or quote.
#: - ``derived``: deterministic composite of other reads (scores, aggregates, limits).
#: - ``local``: desk-local state and files (docs, memory, notifier, receipts).
DataQuality = Literal["ledger", "onchain", "onchain_assumed", "simulated", "derived", "local"]

#: Input fields that name what a tool call is about, in precedence order.
SUBJECT_FIELDS = ("mint", "creator", "ticket_id")


class ToolMeta(BaseModel):
    """Envelope every tool output carries.

    Provenance (P1-2): ``source`` is the upstream, ``fetched_at`` the retrieval time,
    ``observed_at`` when the source state was observed (``None``: not reported by the
    source; ``fetched_at`` is then the upper bound), ``subject`` what the call is about,
    ``quality`` the data class. Availability is ``stale`` on a returned output; an
    unavailable source raises ``DataStale`` / ``DataBlind`` instead of returning.
    """

    source: str
    fetched_at: datetime
    freshness_s: float = Field(ge=0.0)
    correlation_id: str
    stale: bool = False
    reason: str | None = None
    observed_at: datetime | None = None
    subject: str | None = None
    quality: DataQuality | None = None


class ToolInput(BaseModel):
    """Base for every tool input model. `extra='forbid'` mirrors additionalProperties: false."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    correlation_id: str | None = None


class ToolOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    meta: ToolMeta


class UpstreamModel(BaseModel):
    """Base for untrusted upstream payloads (pump.fun, Jupiter, RPC, x.ai).

    `extra="ignore"` mirrors the wire tolerance of external APIs: unknown
    fields are dropped, never rejected. Malformed payloads never escape as
    `ValidationError`/`TypeError`/`ValueError`: parse through
    `parse_upstream`, which maps them to `DataStale`/`DataBlind`.
    """

    model_config = ConfigDict(extra="ignore", frozen=True)


def parse_upstream[M: BaseModel](model: type[M], payload: object, *, blind: bool = False) -> M:
    """Validate an untrusted payload; malformed input becomes typed, never raw.

    `DataStale` when a last-good value may exist (the caller serves cache);
    `DataBlind` when `blind=True` (no last-good value exists).
    """
    try:
        return model.model_validate(payload)
    except (ValidationError, TypeError, ValueError) as exc:
        if blind:
            raise DataBlind(f"unparsable {model.__name__} payload, no last-good value") from exc
        raise DataStale(f"unparsable {model.__name__} payload") from exc


class ToolSpec(BaseModel):
    """Static description of one tool. One instance per entry in the tool catalog."""

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True)

    name: str = Field(pattern=TOOL_NAME_RE)
    description: str = Field(min_length=10, max_length=1000)
    input_model: type[ToolInput]
    output_model: type[ToolOutput]
    side_effect: SideEffect
    timeout_s: float = Field(gt=0.0, le=300.0)
    retry: RetryPolicy = Field(default_factory=RetryPolicy.none)
    rate_limit_key: str = Field(pattern=r"^[a-z][a-z0-9_]*$")  # enforced per-key in registry._wrap
    requires_approval: bool = False  # roster gate g6: approval_id required in input
    idempotent: bool = False  # True for every write; input carries or derives a key
    backend: str = Field(min_length=1)  # endpoint or module path, for the audit log
    quality: DataQuality | None = None  # P1-2; every production tool sets it (test enforced)

    @property
    def capability(self) -> str:
        """Namespace prefix: 'market' for 'market.price'."""
        return self.name.split(".", 1)[0]

    @model_validator(mode="after")
    def _writes_are_idempotent(self) -> Self:
        if self.side_effect is not SideEffect.READ and not self.idempotent:
            raise ValueError(f"{self.name}: write tools must be idempotent")
        if self.side_effect is SideEffect.READ and self.requires_approval:
            raise ValueError(f"{self.name}: read tools cannot require approval")
        return self

    @model_validator(mode="after")
    def _retry_read_only(self) -> Self:
        if self.retry.attempts > 1 and self.side_effect is not SideEffect.READ:
            raise ValueError(f"{self.name}: retry with attempts > 1 is READ-only")
        return self

    def input_json_schema(self) -> dict[str, Any]:
        schema = self.input_model.model_json_schema()
        schema["additionalProperties"] = False
        return schema


@runtime_checkable
class Tool(Protocol):
    """A callable with a spec. Implementations are plain async functions wrapped by `tool()`."""

    spec: ToolSpec

    async def __call__(self, payload: ToolInput, /) -> ToolOutput: ...


def _stamp_provenance(spec: ToolSpec, payload: ToolInput, out: ToolOutput) -> ToolOutput:
    """Fill ``quality``, ``subject`` and (ledger) ``observed_at`` unless the tool set them."""
    meta = out.meta
    update: dict[str, Any] = {}
    if meta.quality is None and spec.quality is not None:
        update["quality"] = spec.quality
    if meta.subject is None:
        subject = next(
            (v for f in SUBJECT_FIELDS if isinstance(v := getattr(payload, f, None), str) and v),
            None,
        )
        if subject is not None:
            update["subject"] = subject
    if meta.observed_at is None and update.get("quality", meta.quality) == "ledger":
        update["observed_at"] = meta.fetched_at
    if update:
        out.meta = meta.model_copy(update=update)
    return out


def tool(spec: ToolSpec) -> Callable[[Callable[[Any], Awaitable[Any]]], Tool]:
    """Attach a ToolSpec to an async function and validate I/O.

    The registry is the only caller of the returned object; it applies the timeout,
    correlation stamping, the approval check, per-key rate limiting and READ-only
    retry around it (see ToolSpec.retry and ToolSpec.rate_limit_key).
    """

    def wrap(fn: Callable[[Any], Awaitable[Any]]) -> Tool:
        class _BoundTool:
            def __init__(self) -> None:
                self.spec = spec

            async def __call__(self, payload: ToolInput, /) -> ToolOutput:
                if not isinstance(payload, spec.input_model):
                    raise TypeError(f"{spec.name}: expected {spec.input_model.__name__}")
                result = await fn(payload)
                return _stamp_provenance(spec, payload, spec.output_model.model_validate(result))

        return _BoundTool()

    return wrap
