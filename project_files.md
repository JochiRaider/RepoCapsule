# Project File Tree
```text
.
├── scripts/
│   ├── manual_test_github.py
│   ├── manual_test_github_toml.py
│   └── manual_test_web_pdf.py
├── src/
│   ├── repocapsule/
│       ├── cli/
│       │   ├── __init__.py
│       │   ├── main.py
│       │   └── runner.py
│       ├── core/
│       │   ├── extras/
│       │   │   ├── __init__.py
│       │   │   ├── md_kql.py
│       │   │   └── qc.py
│       │   ├── __init__.py
│       │   ├── builder.py
│       │   ├── chunk.py
│       │   ├── concurrency.py
│       │   ├── config.py
│       │   ├── convert.py
│       │   ├── dataset_card.py
│       │   ├── decode.py
│       │   ├── factories.py
│       │   ├── hooks.py
│       │   ├── interfaces.py
│       │   ├── licenses.py
│       │   ├── log.py
│       │   ├── naming.py
│       │   ├── pipeline.py
│       │   ├── plugins.py
│       │   ├── qc_controller.py
│       │   ├── qc_post.py
│       │   ├── qc_utils.py
│       │   ├── records.py
│       │   ├── registries.py
│       │   └── safe_http.py
│       ├── sinks/
│       │   ├── __init__.py
│       │   ├── parquet.py
│       │   └── sinks.py
│       ├── sources/
│       │   ├── __init__.py
│       │   ├── csv_source.py
│       │   ├── evtxio.py
│       │   ├── fs.py
│       │   ├── githubio.py
│       │   ├── jsonl_source.py
│       │   ├── parquetio.py
│       │   ├── pdfio.py
│       │   ├── sources_webpdf.py
│       │   └── sqlite_source.py
│       └── __init__.py 
│       
├── tests/
│   ├── conftest.py
│   ├── test_builder_runtime_layering.py
│   ├── test_chunk.py
│   ├── test_cli_main.py
│   ├── test_concurrency.py
│   ├── test_config_builder_pipeline.py
│   ├── test_convert.py
│   ├── test_dataset_card.py
│   ├── test_decode.py
│   ├── test_log_and_naming.py
│   ├── test_pipeline_middlewares.py
│   ├── test_plugins_and_registries.py
│   ├── test_qc_controller.py
│   ├── test_qc_defaults.py
│   ├── test_records.py
│   ├── test_runner_finalizers.py
│   └── test_safe_http.py
├── .gitattributes
├── .gitignore
├── README.md
├── example_config.toml
├── manual_test_github.toml
├── pyproject.toml
├── sample.jsonl
└── project_files.md
```
