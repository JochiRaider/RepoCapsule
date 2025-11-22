import importlib
import json
from pathlib import Path

import pytest

from repocapsule.core.builder import _assert_runtime_free_spec, build_pipeline_plan
from repocapsule.core.concurrency import resolve_pipeline_executor_config
from repocapsule.core.config import (
    PipelineConfig,
    QCConfig,
    QCHeuristics,
    RepocapsuleConfig,
    RunMetadata,
    SinkSpec,
    SourceSpec,
    load_config_from_path,
)
from repocapsule.core.interfaces import RepoContext
from repocapsule.core.pipeline import run_pipeline
from repocapsule.core.safe_http import SafeHttpClient


class DummyPdfSource:
    """Lightweight stand-in whose name triggers heavy-source detection."""


def handle_pdf(data, rel_path, ctx, policy):
    return None


def handle_evtx(data, rel_path, ctx, policy):
    return None


def _make_basic_spec(tmp_path: Path) -> RepocapsuleConfig:
    cfg = RepocapsuleConfig()

    ctx = RepoContext(
        repo_full_name="local/test",
        repo_url="https://example.com/local",
        license_id="UNKNOWN",
    )
    cfg.sinks.context = ctx

    src_root = tmp_path / "input"
    src_root.mkdir()
    (src_root / "file.py").write_text("print('hello')\n", encoding="utf-8")

    cfg.sources.specs = (
        SourceSpec(kind="local_dir", options={"root_dir": str(src_root)}),
    )

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    jsonl_path = out_dir / "data.jsonl"
    prompt_path = out_dir / "data.prompt.txt"

    cfg.sinks.specs = (
        SinkSpec(
            kind="default_jsonl_prompt",
            options={
                "jsonl_path": str(jsonl_path),
                "prompt_path": str(prompt_path),
            },
        ),
    )
    return cfg


# --------------------
# 2.1 config validate
# --------------------


def test_qcconfig_enabled_off_raises():
    qc = QCConfig(enabled=True, mode="off")
    with pytest.raises(ValueError):
        qc.validate()


def test_qcconfig_inline_without_scorer_raises():
    qc = QCConfig(enabled=True, mode="inline", scorer=None)
    with pytest.raises(ValueError):
        qc.validate()

    qc = QCConfig(enabled=True, mode="advisory", scorer=None)
    with pytest.raises(ValueError):
        qc.validate()


def test_qcconfig_negative_heuristics_raises():
    h = QCHeuristics()
    h.target_code_min = 0
    qc = QCConfig(enabled=True, mode="post", heuristics=h)

    with pytest.raises(ValueError) as excinfo:
        qc.validate()
    assert "qc.heuristics.target_code_min" in str(excinfo.value)


def test_qcconfig_weight_out_of_range_raises():
    h = QCHeuristics()
    h.code_punct_weight = 1.5
    qc = QCConfig(enabled=True, mode="post", heuristics=h)

    with pytest.raises(ValueError) as excinfo:
        qc.validate()
    assert "qc.heuristics.code_punct_weight" in str(excinfo.value)


def test_pipeline_executor_kind_normalization_valid_values():
    cfg = RepocapsuleConfig()
    cfg.pipeline.executor_kind = " Thread "
    cfg.validate()
    assert cfg.pipeline.executor_kind == "thread"

    cfg = RepocapsuleConfig()
    cfg.pipeline.executor_kind = "AUTO"
    cfg.validate()
    assert cfg.pipeline.executor_kind == "auto"


def test_pipeline_executor_kind_invalid_raises():
    cfg = RepocapsuleConfig()
    cfg.pipeline.executor_kind = "invalid-kind"
    with pytest.raises(ValueError) as excinfo:
        cfg.validate()
    assert "pipeline.executor_kind" in str(excinfo.value)


def test_validate_paths_same_string_raises():
    cfg = RepocapsuleConfig()
    cfg.metadata.primary_jsonl = "out/data.jsonl"
    cfg.metadata.prompt_path = "out/data.jsonl"

    with pytest.raises(ValueError) as excinfo:
        cfg.validate()
    assert "primary_jsonl and prompt_path refer to the same file path" in str(excinfo.value)


def test_validate_paths_same_real_path_raises(tmp_path: Path):
    jsonl = tmp_path / "data.jsonl"

    cfg = RepocapsuleConfig()
    cfg.metadata.primary_jsonl = str(jsonl)
    cfg.metadata.prompt_path = str(tmp_path / "./data.jsonl")

    with pytest.raises(ValueError):
        cfg.validate()


def test_config_roundtrip_to_from_dict():
    cfg = RepocapsuleConfig()
    cfg.metadata.primary_jsonl = "out/data.jsonl"
    cfg.metadata.repo_url = "https://example.com/repo"

    data = cfg.to_dict()
    cfg2 = RepocapsuleConfig.from_dict(data)

    assert cfg2.metadata.primary_jsonl == cfg.metadata.primary_jsonl
    assert cfg2.metadata.repo_url == cfg.metadata.repo_url
    assert "sources" not in data.get("sources", {})
    assert "sinks" not in data.get("sinks", {})


def test_config_roundtrip_json(tmp_path: Path):
    cfg = RepocapsuleConfig()
    cfg.metadata.repo_url = "https://example.com/repo"
    path = tmp_path / "config.json"

    cfg.to_json(path)
    loaded = RepocapsuleConfig.from_json(path)

    assert loaded.metadata.repo_url == cfg.metadata.repo_url


config_mod = importlib.import_module("repocapsule.core.config")
HAS_TOML = getattr(config_mod, "tomllib", None) is not None


@pytest.mark.skipif(not HAS_TOML, reason="tomllib/tomli not available")
def test_config_roundtrip_toml(tmp_path: Path):
    cfg = RepocapsuleConfig()
    cfg.metadata.repo_url = "https://example.com/repo"

    path = tmp_path / "config.toml"
    content = '[metadata]\nrepo_url = "%s"\n' % cfg.metadata.repo_url
    path.write_text(content, encoding="utf-8")

    loaded = load_config_from_path(path)
    assert loaded.metadata.repo_url == cfg.metadata.repo_url


# ----------------------------
# 2.2 builder + concurrency
# ----------------------------


def test_assert_runtime_free_spec_with_http_client_raises():
    cfg = RepocapsuleConfig()
    cfg.http.client = SafeHttpClient(timeout=1.0)
    with pytest.raises(ValueError) as excinfo:
        _assert_runtime_free_spec(cfg)
    assert "http.client must be unset" in str(excinfo.value)


def test_assert_runtime_free_spec_with_file_extractor_raises():
    cfg = RepocapsuleConfig()
    cfg.pipeline.file_extractor = object()  # type: ignore[assignment]
    with pytest.raises(ValueError) as excinfo:
        _assert_runtime_free_spec(cfg)
    assert "pipeline.file_extractor must be unset" in str(excinfo.value)


def test_assert_runtime_free_spec_with_bytes_handlers_raises():
    def sniff(data: bytes, rel_path: str) -> bool:
        return False

    def handler(data: bytes, rel_path: str, ctx, policy):
        return None

    cfg = RepocapsuleConfig()
    cfg.pipeline.bytes_handlers = ((sniff, handler),)
    with pytest.raises(ValueError) as excinfo:
        _assert_runtime_free_spec(cfg)
    assert "pipeline.bytes_handlers must be empty" in str(excinfo.value)


def test_build_pipeline_plan_registry_wiring(tmp_path: Path):
    cfg = _make_basic_spec(tmp_path)
    plan = build_pipeline_plan(cfg, mutate=False)

    assert plan.sources
    assert plan.sinks
    assert plan.runtime.http_client is not None
    assert plan.spec.metadata.primary_jsonl == plan.spec.sinks.primary_jsonl_name


def test_resolve_executor_config_auto_no_heavy():
    cfg = RepocapsuleConfig()
    cfg.pipeline.executor_kind = "auto"

    exec_cfg, fail_fast = resolve_pipeline_executor_config(cfg, runtime=None)

    assert exec_cfg.kind == "thread"
    assert exec_cfg.max_workers >= 1
    assert exec_cfg.window >= exec_cfg.max_workers
    assert fail_fast is bool(cfg.pipeline.fail_fast)


def test_resolve_executor_config_auto_heavy_handlers_and_sources():
    cfg = RepocapsuleConfig()
    cfg.pipeline.executor_kind = "auto"
    cfg.pipeline.bytes_handlers = (
        (lambda b, p: False, handle_pdf),
        (lambda b, p: False, handle_evtx),
    )
    cfg.sources.sources = (DummyPdfSource(),)

    exec_cfg, _ = resolve_pipeline_executor_config(cfg, runtime=None)

    assert exec_cfg.kind == "process"


def test_resolve_executor_config_auto_only_heavy_handlers():
    cfg = RepocapsuleConfig()
    cfg.pipeline.executor_kind = "auto"
    cfg.pipeline.bytes_handlers = ((lambda b, p: False, handle_pdf),)

    exec_cfg, _ = resolve_pipeline_executor_config(cfg, runtime=None)
    assert exec_cfg.kind == "thread"


def test_resolve_executor_config_auto_only_heavy_sources():
    cfg = RepocapsuleConfig()
    cfg.pipeline.executor_kind = "auto"
    cfg.sources.sources = (DummyPdfSource(),)

    exec_cfg, _ = resolve_pipeline_executor_config(cfg, runtime=None)
    assert exec_cfg.kind == "thread"


# ------------------------
# 2.3 pipeline smoke test
# ------------------------


def test_run_pipeline_smoke(tmp_path: Path):
    src_root = tmp_path / "input"
    src_root.mkdir()
    (src_root / "example.py").write_text("print('hello')\n", encoding="utf-8")
    (src_root / "README.md").write_text("# Title\n\nSome docs\n", encoding="utf-8")

    out_dir = tmp_path / "out"
    out_dir.mkdir()
    jsonl_path = out_dir / "data.jsonl"
    prompt_path = out_dir / "data.prompt.txt"

    cfg = RepocapsuleConfig()
    ctx = RepoContext(
        repo_full_name="local/test",
        repo_url="https://example.com/local",
        license_id="UNKNOWN",
    )
    cfg.sinks.context = ctx

    cfg.sources.specs = (
        SourceSpec(kind="local_dir", options={"root_dir": str(src_root)}),
    )
    cfg.sinks.specs = (
        SinkSpec(
            kind="default_jsonl_prompt",
            options={
                "jsonl_path": str(jsonl_path),
                "prompt_path": str(prompt_path),
            },
        ),
    )

    stats = run_pipeline(config=cfg)

    assert stats["files"] >= 1
    assert stats["records"] >= 1

    assert jsonl_path.exists()
    lines = jsonl_path.read_text(encoding="utf-8").splitlines()
    assert lines
    first = json.loads(lines[0])
    assert "text" in first
    assert "meta" in first
