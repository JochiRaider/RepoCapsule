<div id="top"></div>

<!-- PROJECT SHIELDS -->

[][contributors-url]
[][forks-url]
[][stars-url]
[][issues-url]
[![MIT License][license-shield]][license-url]

<!-- PROJECT LOGO -->

<br />
<div align="center">
<a href="https://github.com/JochiRaider/RepoCapsule">
<img src="images/logo.png" alt="RepoCapsule logo" width="80" height="80">
</a>

<h3 align="center">RepoCapsule</h3>

<p align="center">
A modular, stdlib-first pipeline to turn code, docs, PDFs, and logs into clean JSONL chunks.
<br />
<a href="https://github.com/JochiRaider/RepoCapsule"><strong>Explore the docs »</strong></a>
<br />
<br />
<a href="https://github.com/JochiRaider/RepoCapsule/issues">Report Bug</a>
·
<a href="https://github.com/JochiRaider/RepoCapsule/issues">Request Feature</a>
</p>
</div>

<!-- TABLE OF CONTENTS -->

<details>
<summary>Table of Contents</summary>
<ol>
<li>
<a href="#about-the-project">About The Project</a>
<ul>
<li><a href="#built-with">Built With</a></li>
</ul>
</li>
<li>
<a href="#getting-started">Getting Started</a>
<ul>
<li><a href="#prerequisites">Prerequisites</a></li>
<li><a href="#installation">Installation</a></li>
</ul>
</li>
<li><a href="#usage">Usage</a></li>
<li><a href="#architecture">Architecture & Concepts</a></li>
<li><a href="#roadmap">Roadmap</a></li>
<li><a href="#contributing">Contributing</a></li>
<li><a href="#license">License</a></li>
<li><a href="#contact">Contact</a></li>
<li><a href="#acknowledgments">Acknowledgments</a></li>
</ol>
</details>

<!-- ABOUT THE PROJECT -->

About The Project

![Product Name Screen Shot][product-screenshot]

RepoCapsule is a robust, extensible library for ingesting heterogeneous data sources—GitHub repositories, local file trees, web-hosted PDFs, and Windows EVTX logs—and transforming them into normalized, high-quality datasets for AI pipelines.

Key Features

Modular Architecture: Core logic is separated from I/O implementations (sources/sinks), enabling easy extension via a registry and plugin system.

Safe by Default: Includes defenses against zip bombs, SSRF (via SafeHttpClient), and memory exhaustion (streaming-first design).

Format Aware: Specialized handling for Markdown, reStructuredText, code, PDFs, and Windows Event Logs (EVTX).

Declarative Configuration: Pipelines can be defined purely via serializable configuration (TOML/JSON), making it ideal for CLI tools and reproducible runs.

Quality Control (QC): Optional, tunable QC scoring with inline gating, near-duplicate detection (SimHash/MinHash), and post-processing analysis.

<p align="right">(<a href="#top">back to top</a>)</p>

Built With

RepoCapsule is designed to be lightweight, with heavy dependencies made optional.

Python (3.11+)

Core: Standard Library only (pathlib, zipfile, concurrent.futures, urllib, dataclasses).

Optional Extras:

[tok]: tiktoken for exact token counting.

[pdf]: pypdf for PDF extraction.

[evtx]: python-evtx for Windows Event Log parsing.

[qc]: torch, transformers, tiktoken for advanced quality scoring heuristics.

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- GETTING STARTED -->

Getting Started

Prerequisites

Python 3.11+

pip

Installation

From Source (Recommended for development):

git clone [https://github.com/JochiRaider/RepoCapsule.git](https://github.com/JochiRaider/RepoCapsule.git)
cd RepoCapsule
pip install -e .
# Install extras as needed:
pip install -e ".[tok,pdf,evtx,qc]"


From PyPI (Future):

pip install repocapsule[all]


<p align="right">(<a href="#top">back to top</a>)</p>

<!-- USAGE EXAMPLES -->

Usage

RepoCapsule supports two primary usage patterns: Library Mode (building objects in Python) and Declarative Mode (using configuration files).

1. Library Mode (Python API)

Directly instantiate sources, sinks, and the pipeline engine.

from pathlib import Path
from repocapsule import RepocapsuleConfig, convert_local_dir

# Simple one-shot conversion of a local directory
stats = convert_local_dir(
    root_dir="./my-repo",
    out_jsonl="./out/dataset.jsonl",
    out_prompt="./out/dataset.prompt.txt"
)
print(stats)


For more control, build a config object:

from repocapsule import RepocapsuleConfig, convert
from repocapsule.core.chunk import ChunkPolicy

# Configure the pipeline
cfg = RepocapsuleConfig()
cfg.chunk.policy = ChunkPolicy(mode="doc", target_tokens=512)
cfg.sources.local.include_exts = {".py", ".md"}

# Use a helper to set up the run profile
from repocapsule.cli.runner import make_local_profile
run_cfg = make_local_profile(
    root_dir="./src",
    out_jsonl="./data.jsonl",
    base_config=cfg
)

# Run it
convert(run_cfg)


2. Declarative Mode (TOML Config)

Define your pipeline in a TOML file. This is ideal for reproducible data processing jobs.

pipeline.toml:

[sources.github]
per_file_cap = 1048576 # 1MB

[pipeline]
executor_kind = "thread"
max_workers = 4

[qc]
enabled = true
mode = "inline"
min_score = 0.5

[sinks]
output_dir = "./out"
jsonl_basename = "training_data"

# Define sources declaratively
[[sources.specs]]
kind = "github_zip"
options = { url = "[https://github.com/psf/requests](https://github.com/psf/requests)" }


Run it via Python:

from repocapsule import load_config_from_path, convert

cfg = load_config_from_path("pipeline.toml")
convert(cfg)


<p align="right">(<a href="#top">back to top</a>)</p>

<!-- ARCHITECTURE -->

Architecture & Concepts

The library is organized into three main layers:

Core (src/core): Contains the PipelineEngine, configuration logic, concurrency handling (Executor), and base interfaces (Source, Sink, Protocol).

Builder Pattern: build_pipeline_plan converts a declarative RepocapsuleConfig into a runtime PipelinePlan, handling dependency injection and validation.

Registries: src/core/registries.py allows dynamic registration of Sources, Sinks, and Scorers, enabling a plugin architecture.

Implementations (src/sources, src/sinks):

Sources: LocalDirSource, GitHubZipSource, WebPdfListSource, WebPagePdfSource, JSONLTextSource.

Sinks: JSONLSink, PromptTextSink (for debugging/vis).

Orchestration (src/cli): Higher-level helpers (like convert_github) that stitch configuration and execution together for common tasks.

Key Concepts

ChunkPolicy: Defines how text is split (e.g., by paragraph, by code line, token overlap).

Quality Control (QC):

Modes: inline (drop bad records immediately), advisory (tag but keep), post (process full dataset after extraction).

Scorers: Pluggable logic to assess text quality. The default scorer uses heuristics (length, symbol ratio) and can be extended with ML models (perplexity).

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- ROADMAP -->

Roadmap

[ ] CLI Tool: A dedicated repocapsule command-line interface for running TOML configs.

[ ] More Sinks: Support for Parquet and LanceDB.

[ ] Advanced Extractors: Better handling of Jupyter Notebooks and specialized XML formats.

[ ] Plugin Ecosystem: Standardize the plugin entry point for community extensions.

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- CONTRIBUTING -->

Contributing

Contributions are welcome! Please check the LICENSE file and open an issue/PR if you have ideas.

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- LICENSE -->

License

Distributed under the MIT License. See LICENSE for more information.

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- CONTACT -->

Contact

Maintainer: @JochiRaider

Project Link: https://github.com/JochiRaider/RepoCapsule

<p align="right">(<a href="#top">back to top</a>)</p>

<!-- MARKDOWN LINKS & IMAGES -->

[issues-url]:

[]: #
[contributors-url]: https://github.com/JochiRaider/RepoCapsule/graphs/contributors
[]: #
[forks-url]: https://github.com/JochiRaider/RepoCapsule/network/members
[]: #
[stars-url]: https://github.com/JochiRaider/RepoCapsule/stargazers
[]: #work/members
[]: #utors
