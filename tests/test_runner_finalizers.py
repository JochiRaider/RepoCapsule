import json

from repocapsule.cli.runner import _dispatch_finalizers
from repocapsule.core.config import RepocapsuleConfig
from repocapsule.core.records import build_run_header_record
from repocapsule.sinks.sinks import JSONLSink


def test_dispatch_finalizers_does_not_clobber_jsonl(tmp_path) -> None:
    jsonl_path = tmp_path / "data.jsonl"
    sink = JSONLSink(jsonl_path)
    sink.set_header_record(build_run_header_record(RepocapsuleConfig()))

    # Simulate the main pipeline writing records.
    sink.open()
    sink.write({"text": "body", "meta": {"kind": "chunk", "path": "file.txt"}})
    sink.close()

    summary = {"text": "", "meta": {"kind": "run_summary"}}
    _dispatch_finalizers([sink], summary, str(jsonl_path), None)

    kinds = [json.loads(line)["meta"]["kind"] for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    assert kinds == ["run_header", "chunk", "run_summary"]
