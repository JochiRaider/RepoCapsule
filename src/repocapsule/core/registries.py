# registries.py
# SPDX-License-Identifier: MIT
from __future__ import annotations

from dataclasses import dataclass, field
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    List,
    Mapping,
    Optional,
    Protocol,
    Sequence,
    Tuple,
)

from .config import RepocapsuleConfig, SourceSpec, SinkSpec
from .interfaces import (
    Source,
    Sink,
    SourceFactory,
    SinkFactory,
    QualityScorer,
    SourceFactoryContext,
    SinkFactoryContext,
)
from .log import get_logger

if TYPE_CHECKING:  # pragma: no cover - type-only deps
    from .factories import SinkFactoryResult


@dataclass
class SourceRegistry:
    _factories: Dict[str, SourceFactory] = field(default_factory=dict)

    def register(self, factory: SourceFactory) -> None:
        self._factories[factory.id] = factory

    def build_all(self, ctx: SourceFactoryContext, specs: Sequence[SourceSpec]) -> List[Source]:
        out: List[Source] = []
        for spec in specs:
            factory = self._factories.get(spec.kind)
            if factory is None:
                raise ValueError(f"Unknown source kind {spec.kind!r}")
            out.extend(factory.build(ctx, spec))
        return out


@dataclass
class SinkRegistry:
    _factories: Dict[str, SinkFactory] = field(default_factory=dict)

    def register(self, factory: SinkFactory) -> None:
        self._factories[factory.id] = factory

    def build_all(self, ctx: SinkFactoryContext, specs: Sequence[SinkSpec]) -> Tuple[List[Sink], Mapping[str, Any], SinkFactoryContext]:
        sinks: List[Sink] = []
        merged_meta: Dict[str, Any] = {}
        current_ctx = ctx
        for spec in specs:
            factory = self._factories.get(spec.kind)
            if factory is None:
                raise ValueError(f"Unknown sink kind {spec.kind!r}")
            result: "SinkFactoryResult" = factory.build(current_ctx, spec)
            sinks.extend(result.sinks)
            for k, v in result.metadata.items():
                if k not in merged_meta or merged_meta[k] is None:
                    merged_meta[k] = v
            # Propagate any sink_config updates back into the context so that
            # subsequent factories see the latest settings.
            current_ctx = SinkFactoryContext(
                repo_context=result.sink_config.context or current_ctx.repo_context,
                sink_config=result.sink_config,
            )
        return sinks, merged_meta, current_ctx


class BytesHandlerRegistry:
    def __init__(self) -> None:
        self._handlers: List[Tuple[Callable[[bytes, str], bool], Callable[..., Optional[Iterable[Any]]]]] = []

    def register(self, sniff: Callable[[bytes, str], bool], handler: Callable[..., Optional[Iterable[Any]]]) -> None:
        self._handlers.append((sniff, handler))

    def handlers(self) -> Tuple[Tuple[Callable[[bytes, str], bool], Callable[..., Optional[Iterable[Any]]]], ...]:
        return tuple(self._handlers)


class QualityScorerFactory(Protocol):
    id: str

    def build(self, options: Mapping[str, Any]) -> QualityScorer:
        ...


class QualityScorerRegistry:
    def __init__(self) -> None:
        self._factories: Dict[str, QualityScorerFactory] = {}
        self.log = get_logger(__name__)
        # DEFAULT_QC_SCORER_ID is used when qc.scorer_id is None and a default scorer is registered.

    def register(self, factory: QualityScorerFactory) -> None:
        self._factories[factory.id] = factory

    def get(self, factory_id: Optional[str] = None) -> Optional[QualityScorerFactory]:
        if factory_id is not None:
            return self._factories.get(factory_id)
        if not self._factories:
            return None
        first_key = next(iter(self._factories))
        return self._factories[first_key]

    def build(
        self,
        options: Mapping[str, Any],
        *,
        factory_id: Optional[str] = None,
    ) -> Optional[QualityScorer]:
        factory = self.get(factory_id)
        if factory is None:
            return None
        try:
            return factory.build(options)
        except Exception as exc:
            self.log.warning("Quality scorer factory %s failed: %s", getattr(factory, "id", None), exc)
            return None

    def ids(self) -> Tuple[str, ...]:
        return tuple(self._factories.keys())


def default_source_registry() -> SourceRegistry:
    from .factories import (
        LocalDirSourceFactory,
        GitHubZipSourceFactory,
        WebPdfListSourceFactory,
        WebPagePdfSourceFactory,
        CsvTextSourceFactory,
        SQLiteSourceFactory,
    )

    reg = SourceRegistry()
    reg.register(LocalDirSourceFactory())
    reg.register(GitHubZipSourceFactory())
    reg.register(WebPdfListSourceFactory())
    reg.register(WebPagePdfSourceFactory())
    reg.register(CsvTextSourceFactory())
    reg.register(SQLiteSourceFactory())
    return reg


def default_sink_registry() -> SinkRegistry:
    from .factories import DefaultJsonlPromptSinkFactory, ParquetDatasetSinkFactory

    reg = SinkRegistry()
    reg.register(DefaultJsonlPromptSinkFactory())
    reg.register(ParquetDatasetSinkFactory())
    return reg


bytes_handler_registry = BytesHandlerRegistry()
quality_scorer_registry = QualityScorerRegistry()
