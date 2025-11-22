import importlib
import logging
from types import SimpleNamespace

import pytest

from repocapsule.core.plugins import load_entrypoint_plugins
from repocapsule.core.registries import (
    BytesHandlerRegistry,
    QualityScorerRegistry,
    SinkRegistry,
    SourceRegistry,
    default_sink_registry,
    default_source_registry,
)
from repocapsule.core.config import RepocapsuleConfig, SourceSpec, SinkSpec


class FakeEntryPoint:
    def __init__(self, name, value=None, error=None):
        self.name = name
        self._value = value
        self._error = error

    def load(self):
        if self._error is not None:
            raise self._error
        return self._value


def test_load_entrypoint_plugins_logs_failures(monkeypatch, caplog):
    good_called = SimpleNamespace(value=False)

    def good_plugin(source_reg, sink_reg, bytes_reg, scorer_reg):
        good_called.value = True

    eps = [
        FakeEntryPoint("good_plugin", value=good_plugin),
        FakeEntryPoint("bad_plugin", error=ImportError("boom")),
    ]

    class DummyEntryPoints(list):
        def select(self, group=None):
            return self if group == "repocapsule.plugins" else []

    def fake_entry_points():
        return DummyEntryPoints(eps)

    monkeypatch.setattr(importlib.metadata, "entry_points", fake_entry_points)
    messages: list[str] = []

    def fake_warning(msg, *args, **kwargs):
        text = msg % args if args else str(msg)
        messages.append(text)

    monkeypatch.setattr("repocapsule.core.plugins.log.warning", fake_warning)

    with caplog.at_level("WARNING", logger="repocapsule.core.plugins"):
        load_entrypoint_plugins(
            source_registry=SourceRegistry(),
            sink_registry=SinkRegistry(),
            bytes_registry=BytesHandlerRegistry(),
            scorer_registry=QualityScorerRegistry(),
        )

    assert good_called.value is True
    assert any("bad_plugin" in msg for msg in messages)


def test_source_registry_unknown_kind_raises():
    registry = default_source_registry()
    cfg = RepocapsuleConfig()
    cfg.sources.specs = (SourceSpec(kind="does_not_exist", options={}),)
    with pytest.raises(ValueError):
        registry.build_all(cfg)


def test_sink_registry_unknown_kind_raises():
    registry = default_sink_registry()
    cfg = RepocapsuleConfig()
    cfg.sinks.specs = (SinkSpec(kind="does_not_exist", options={}),)
    with pytest.raises(ValueError):
        registry.build_all(cfg)


def test_default_source_registry_has_expected_ids():
    registry = default_source_registry()
    kinds = set(registry._factories.keys())
    for expected in {"local_dir", "github_zip", "web_pdf_list", "web_page_pdf", "csv_text", "sqlite"}:
        assert expected in kinds


def test_default_sink_registry_has_expected_ids():
    registry = default_sink_registry()
    kinds = set(registry._factories.keys())
    assert "default_jsonl_prompt" in kinds
    assert "parquet_dataset" in kinds
