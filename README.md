# AI Compression Stack

A containerized context-routing, multi-agent dispatch, and compression stack designed to preserve upstream LLM token quotas, eliminate vendor lock-in, and provide automated agent personas.

Incoming requests to the OpenAI-compatible gateway are analyzed, distilled via local models (Ollama), stripped of AST/JSON bloat, and executed via Antigravity (`agy` CLI) or local Ollama with zero required cloud API keys.

---

## Architecture & Workflow

```
User Prompt (Open WebUI / API)
           │
           ├── [Natural Language or Persona Mention: @coder, @reviewer, @architect]
           │
           ▼
[ Router: Auto-Discovery & Pre-Read ]
    - User named specific files? Use those.
    - Natural language request ("Add health check")?
      Local Ollama (0 Cloud Tokens) inspects /workspace file tree and auto-selects target files.
           │
           ▼
[ AST Code & Log Compression Engine ]
    - Parses target files using Python AST & multi-language regex minifiers (ast_compressor.py)
    - Strips docstrings, comments, redundant blank lines, brace whitespace, and framework log noise
    - Slashes input context tokens by 30% to 55% (and log token bloat by 50%–90%)
    - Pre-injects compressed code into "## Workspace Pre-Read Context"
           │
           ▼
[ Antigravity (agy) Execution with Headless MCP ]
    - Subprocess agy --model <AGY_MODEL> --mode accept-edits --dangerously-skip-permissions
    - Receives compressed code upfront (no blind disk searches needed)
    - Equipped with zero-cost in-flight MCP tools:
        • trace_symbol: Multi-language (Java, Python, TypeScript) symbol, Spring bean, and call-site tracer
        • ask_local_assistant: Workspace-grounded code retrieval and drafting via local Ollama (0 cloud tokens)
        • run_command: Sandboxed shell runner with programmatic test execution interceptors
           │
           ▼
[ Execution or Local Fallback ]
    - Exit 0: Emits clean OpenAI response envelope with token savings badge
    - Non-zero / Timeout / 429 Quota: Automatic fallback to local Ollama
```

### Flowchart

```mermaid
flowchart TD
    Client(["User Prompt<br>Open-WebUI :3000"]) -->|POST /v1/chat/completions| Router["Quota Router Gateway<br>FastAPI :8000 (Host :8088)"]

    Router --> CheckHeadroom{"Headroom Proxy Model?"}
    CheckHeadroom -->|Yes| HeadroomDirect["Direct Pass-Through<br>Headroom Proxy :8787"]

    CheckHeadroom -->|No| CheckPersona{"Persona Trigger?<br>@coder / @reviewer / @architect"}
    
    CheckPersona -->|Yes| AutoDiscover["1. Workspace Auto-Discovery<br>• User files or Local Ollama scan<br>• Identifies relevant target files"]
    CheckPersona -->|No| CheckVision{"Multimodal / Image?"}

    CheckVision -->|Yes| GeminiVision["Gemini 2.5 Flash / Vision Fallback"]
    CheckVision -->|No| AutoDiscover

    AutoDiscover --> ASTCompress["2. AST & Log Compression Engine<br>• Strip comments, docstrings & whitespace<br>• Filter framework log stacktraces<br>• Save 30% - 55% input tokens"]

    ASTCompress --> PreRead["3. Inject Pre-Read Context<br>## Workspace Pre-Read Context"]

    PreRead --> DispatchAgy["4. Dispatch agy with MCP Tools<br>• write_to_file / delete_file / run_command<br>• trace_symbol (AST call tracer)<br>• ask_local_assistant (Local Ollama)"]

    DispatchAgy --> CheckStatus{"agy Result Code"}

    CheckStatus -->|Exit 0| AgentSuccess["*[Agent Task: Antigravity | AST Tokens Saved: X (Y%)]*"]
    CheckStatus -->|Non-Zero / Quota / Timeout| FallbackOllama["*[Fallback: Local Ollama]*<br>Local Model Code Gen"]

    GeminiVision --> Envelope["OpenAI-Compatible Response Envelope"]
    HeadroomDirect --> Envelope
    AgentSuccess --> Envelope
    FallbackOllama --> Envelope
    Envelope --> Client
```

---

## Services Overview

The stack is composed of 4 containerized services managed via `docker-compose.yml`:

| Service | Container Name | Host Port | Internal Port | Description |
| :--- | :--- | :--- | :--- | :--- |
| **`router`** | `quota-router` | `8088` | `8000` | FastAPI gateway providing OpenAI-compatible `/v1/chat/completions`, AST code compressor, auto-discovery, telemetry (`/stats`), and model registry. |
| **`headroom`** | `headroom-proxy` | `8787` | `8787` | Context compression and prefix-caching reverse proxy for direct LLM completions. |
| **`ollama`** | `local-ollama` | `11434` | `11434` | Local model inference engine (`qwen2.5-coder:1.5b`) for zero-cost file discovery, in-flight MCP assistance, and offline fallback. |
| **`open-webui`** | `open-webui` | `3000` | `8080` | Full-featured chat interface wired to both `http://router:8000/v1` and `http://headroom:8787/v1`. |

---

## Codebase Directory Layout (`services/router`)

The router service follows a clean enterprise architecture separating source modules from automated test suites:

```text
services/router/
├── Dockerfile
├── requirements.txt
├── src/
│   ├── __init__.py
│   ├── app.py                   # FastAPI Gateway & Quota Router
│   ├── ast_compressor.py        # Multi-Format AST Code & Log Compressor
│   └── mcp_workspace.py         # Headless Zero-Cost MCP Tool Server
└── tests/
    ├── __init__.py
    ├── test_ast_compressor.py   # AST Compression Unit Tests (15 tests)
    └── test_mcp_workspace.py    # MCP Tool & Guardrail Unit Tests (8 tests)
```

---

## Multi-Agent Personas

The stack natively supports specialized personas declared in `.antigravity/agents/`:

| Persona | Triggers | Description |
| :--- | :--- | :--- |
| **`coder`** | `@coder`, `/coder`, `@dev`, `@implement` | Senior Software Engineer: minimal-diff implementation, bug fixes, and unit tests strictly within `/workspace`. |
| **`reviewer`** | `@reviewer`, `/reviewer`, `@audit` | Senior Security & Quality Auditor: inspects code for vulnerabilities, edge-case bugs, missing error branches, and suggests minimal patches. |
| **`architect`** | `@architect`, `/architect` | System Architect: pre-implementation blueprints, interface contracts, and phased technical roadmaps. |

### Global Directives (`AGENTS.md`)
The project root includes [AGENTS.md](AGENTS.md) enforcing:
- Operational scope restricted exclusively to `/workspace`.
- **Pre-Read Context as Source of Truth**: Treats `Workspace Pre-Read Context` as compressed truth to avoid repetitive disk `view_file` calls.
- **Zero-Cost Tool Utilization**: Prioritizes `trace_symbol` and `ask_local_assistant` for call graph tracing and codebase lookups.
- **Fast Convergence & Safe Deletion**: Consolidates multi-file edits and batch deletions into single-turn operations to avoid latency overhead.
- Non-destructive, minimal-diff editing practices.

---

## Key Features

- **Upfront Workspace Auto-Discovery**: Prompt in plain English (e.g. `@coder Add user authentication`) without manually listing file paths. Local Ollama automatically detects the target files from `/workspace` file tree at **0 cloud tokens**.
- **Multi-Format Code & Log Compression Engine (`ast_compressor.py`)**: Automatically minifies target context across multiple languages and data formats before passing context to `agy`, slashing token consumption by **30% to 55%**:
  - **Python (`.py`)**: AST-based docstring, comment, and whitespace minification.
  - **JS / TS / C / C++ / Java / Go / Rust / C# / PHP (`.js`, `.ts`, `.jsx`, `.tsx`, `.c`, `.cpp`, `.go`, `.rs`, `.java`, `.cs`, `.php`, `.vue`, `.svelte`)**: String-aware comment stripping (`//`, `/* */`), brace/semicolon/comma line collapsing, and blank line elimination.
  - **Log Files & Stacktraces (`.log`)**: Filters Spring Boot & Node.js logs — strips framework reflection noise (`org.springframework...`, `sun.reflect...`) while preserving exception headers, `Caused by:` root causes, and user application frames (**50% – 90% token savings**).
  - **JSON (`.json`)**: Whitespace and newline minification.
  - **HTML / XML / SVG (`.html`, `.htm`, `.xml`, `.svg`)**: Comment (`<!-- -->`) removal and tag-gap collapsing.
  - **CSS / SCSS / SASS (`.css`, `.scss`, `.sass`, `.less`)**: Comment stripping and whitespace compression.
  - **YAML (`.yaml`, `.yml`)**: Comment stripping with strict indentation structure preservation.
  - **Shell Scripts (`.sh`, `.bash`, `.zsh`)**: Comment stripping with shebang (`#!`) preservation.
  - **SQL (`.sql`)**: Single-line (`--`) and block (`/* */`) comment stripping.
  - **Tabular Data (`.csv`, `.tsv`)**: Compacts whitespace and auto-truncates large datasets to representative schema samples.
  - **Markdown (`.md`, `.mdx`, `.txt`)**: Comment removal and excessive blank line compaction.
- **In-Flight Zero-Cost MCP Tools (`mcp_workspace.py`)**:
  - `write_to_file`: Headless file creations and non-destructive edits.
  - `run_command`: Sandboxed command runner with hard programmatic interceptors blocking test executions across Java (`mvn`, `gradle`), JS/TS (`jest`, `vitest`, `mocha`, `playwright`, `cypress`), Python (`pytest`, `unittest`), Go, Rust, and .NET.
  - `trace_symbol`: Fast multi-language symbol tracer supporting Java (classes, interfaces, Spring beans, methods), Python (AST), and TypeScript.
  - `ask_local_assistant`: Query local Ollama (`qwen2.5-coder:1.5b`) grounded with automated workspace snippet retrieval for signatures, mock patterns, and code/boilerplate drafting (0 cloud tokens).
  - `delete_file`: Safe single or batch deletion of obsolete files/directories within `/workspace`.
- **Fast Convergence**: Enforces multi-file batching so agent actions complete within 2 turns rather than serial multi-minute round trips.
- **Real-Time Telemetry (`/stats`)**: Query `http://localhost:8088/stats` for live cumulative statistics on AST tokens saved, requests processed, and Headroom proxy cache metrics.

---

## Recommended Local Ollama Models per Hardware Specs

| Hardware Tier | System RAM / VRAM | GPU / CPU Target | Recommended Ollama Model | Quantization | RAM / VRAM Footprint | Performance & Use Case |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Ultra Light / CPU-only** | 8 GB RAM (0 GB VRAM) | Dual-core / Quad-core CPU | `qwen2.5-coder:0.5b` | `Q4_K_M` | ~350 MB | Ultra-fast (~40 t/s on CPU). Ideal for instant signature lookups on low-spec devices. |
| **Standard / Lightweight** *(Default)* | 8–16 GB RAM (2–4 GB VRAM) | Integrated GPU / Entry Card (GTX 1650 / M1) | `qwen2.5-coder:1.5b` | `Q4_K_M` | ~1.1 GB | Excellent balance (~50–80 t/s). Instant local code drafting and snippet retrieval. |
| **Intermediate Workstation** | 16–32 GB RAM (6–8 GB VRAM) | Mid-range GPU (RTX 3060/4060 / M2-M3 16GB) | `qwen2.5-coder:7b` | `Q4_K_M` | ~4.5 GB | High reasoning accuracy. Great at multi-file mock generation and unit test drafting. |
| **High Performance** | 32–64 GB RAM (12–16 GB VRAM) | High-end GPU (RTX 3080/4080 / M2/M3 Pro 32GB) | `qwen2.5-coder:14b` or `deepseek-coder-v2:16b` | `Q4_K_M` | ~9.0 GB | Near-cloud reasoning quality. Performs structural refactoring & complex local code generation. |
| **Enterprise Workstation** | 64+ GB RAM (24+ GB VRAM) | Top-tier GPU (RTX 3090/4090 / M2-M3 Ultra 64GB+) | `qwen2.5-coder:32b` or `codestral:22b` | `Q4_K_M` / `Q8_0` | ~20 GB | State-of-the-art local coding capability. Rivals top cloud models on local code generation. |

---

## Testing & Verification

Run the full 23-test suite inside the Docker container:

```bash
docker compose run --rm -v "${PWD}/services/router:/app" -e PYTHONPATH=/app/src router python -m unittest discover -s tests
```

### Test Suite Breakdown:
- **`tests/test_ast_compressor.py` (15 tests)**: Verifies AST docstring stripping, aggressive C-family newline/brace collapsing, Spring Boot log stacktrace filtering, and multi-format minification.
- **`tests/test_mcp_workspace.py` (8 tests)**: Verifies `ask_local_assistant` zero-cost Ollama queries, `trace_symbol` multi-language tracing, `run_command` test execution blocking, and `apply_guardrails` prompt enforcement.

---

## Getting Started

### Prerequisites
- [Docker](https://docs.docker.com/get-docker/) & Docker Compose v2+
- Linux, macOS, or Windows WSL2
- [Antigravity CLI](https://github.com/google/antigravity) (`agy`) installed and logged in on the host (defaults to `~/.local/bin/agy`)
- Ollama model (defaults to `qwen2.5-coder:1.5b`)

### Quickstart

1. **Clone the repository:**
   ```bash
   git clone https://github.com/<your-username>/ai-compression-stack.git
   cd ai-compression-stack
   ```

2. **Configure environment variables:**
   ```bash
   cp .env.example .env
   ```
   *(All defaults work out of the box with zero required API keys).*

3. **Build and launch the stack:**
   ```bash
   docker compose up -d --build
   ```

4. **Verify health & stats connectivity:**
   ```bash
   curl -s http://localhost:8088/healthz
   curl -s http://localhost:8088/stats
   ```

5. **Access the Open-WebUI Frontend:**
   Open your browser to [http://localhost:3000](http://localhost:3000).

---

## Configuration Reference

Customize these variables in your `.env` file:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `AGY_MODEL` | `gemini-3.8-flash-medium` | Model target used by Antigravity CLI (`agy --model`). |
| `AGY_TIMEOUT` | `180` | Subprocess execution and print timeout in seconds for `agy`. |
| `OLLAMA_MODEL` | `qwen2.5-coder:1.5b` | Model used for local file discovery, MCP assistance, and offline fallback. |
| `OLLAMA_TIMEOUT` | `300` | Timeout in seconds for local Ollama dependency discovery, generation fallback, and analysis. |
| `PORT_ROUTER` | `8088` | Host port exposed for the Quota Router Gateway. |
| `HOST_UID` | `1000` | Host user ID mapped into the container. |
| `HOST_GID` | `1000` | Host group ID mapped into the container. |
| `WORKSPACE_PATH` | `~/development/smoke-test` | Host path bind-mounted to `/workspace` inside containers. |
| `AGY_BIN_PATH` | `~/.local/bin/agy` | Host path to the `agy` CLI binary. |
| `HOST_LOCAL_PATH` | `~/.local` | Host path to `.local` directory (keyrings / auth). |
| `HOST_CONFIG_PATH` | `~/.config` | Host path to `.config` directory (CLI configurations). |
| `HOST_GEMINI_PATH` | `~/.gemini` | Host path to `.gemini` directory (installation ID & session tokens). |
| `HEADROOM_PROXY` | `http://headroom:8787` | Internal Docker URL for the Headroom proxy service. |
| `OLLAMA_URL` | `http://ollama:11434` | Internal Docker URL for the Ollama service. |
| `GEMINI_API_KEY` | *(empty)* | Optional Gemini API key. If omitted, routes via Ollama / `agy`. |

---

## Usage & API Testing

### 1. Persona Prompts in Open WebUI
In [http://localhost:3000](http://localhost:3000), select `auto-router` (or any persona model) and type:
- `@coder Add a health check endpoint and verify tests` *(Ollama auto-discovers relevant files, AST-compresses them, and agy executes)*
- `@reviewer Check app.py for unhandled exceptions and security risks`
- `@architect Design a modular plugin architecture for the compression router`

### 2. Inspecting Token Savings
```bash
curl -s http://localhost:8088/stats | jq .ast_compression
```

### 3. OpenAI-Compatible API Call
```bash
curl -s -X POST http://localhost:8088/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "coder",
    "messages": [
      {"role": "user", "content": "Write unit tests for the token counter"}
    ]
  }'
```

---

## License
MIT