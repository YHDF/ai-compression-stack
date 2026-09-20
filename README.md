# AI Compression Stack

A containerized context-routing, multi-agent dispatch, and compression stack designed to preserve upstream LLM token quotas, eliminate vendor lock-in, and provide an autonomous **Tech Lead Orchestrator**.

Incoming requests to the OpenAI-compatible gateway are analyzed, distilled with AST code compression, and executed via **Antigravity (`agy` CLI)** powered by **Claude 3.5 Sonnet** as the Tech Lead, with repetitive boilerplate and seed data delegated to local Ollama at 0 cloud tokens.

---

## Architecture & Workflow (Tech Lead Pattern)

```
User Prompt (Open WebUI / API)
           │
           ├── [Natural Language or Persona: @coder, @reviewer, @architect]
           │
           ▼
[ Router: Auto-Discovery & Context Pre-Reader ]
    - Detects explicit target files mentioned in the prompt or scans core workspace files.
    - Python AST Skeletonizer (ast_compressor.py) collapses function bodies to '...' (signatures only),
      slashing input context tokens by up to 80% without losing architectural context.
    - Pre-injects compressed definitions into "## Workspace Pre-Read Context".
           │
           ▼
[ Local vs. Cloud Routing Gate ]
    - Forced Local (@local, @ollama) or simple non-coding Q&A?
      -> Routes directly to local Ollama (0 Cloud Tokens).
    - Coding, Implementation, Scripting, or Refactoring?
      -> Dispatches to Tech Lead Orchestrator (Antigravity agy CLI).
           │
           ▼
[ Tech Lead Orchestration (Claude 3.5 Sonnet via agy) ]
    - Subprocess: agy --model claude-3-5-sonnet --max-turns 2 --mode accept-edits --dangerously-skip-permissions
    - Receives compressed workspace context upfront (no blind file-search loops).
    - Division of Labor (Zero Cloud Token Economy):
        • Core Implementation & Tests: Claude directly implements complete, production-ready
          solutions in 1 single turn using MCP write_to_file and replace_file_content (no line limits).
        • Repetitive Boilerplate / Seed Data: Claude delegates repetitive tasks (mock CSVs, seed tables,
          large boilerplate) to local Ollama via ask_local_assistant(query=..., target_file=...) at 0 cloud tokens.
        • In-Flight Sandboxed Tools: write_to_file, replace_file_content, trace_symbol, ask_local_assistant, run_command, delete_file.
           │
           ▼
[ Delivery, Auto-Persistence & Local Fallback ]
    - Exit 0: Code persisted to /workspace disk, emits clean OpenAI response envelope with token metrics.
    - Safety Auto-Persist: Router verifies and persists any raw code blocks generated in text.
    - Fallback: If cloud quota is exhausted or times out, seamlessly falls back to local Ollama.
```

### Flowchart

```mermaid
flowchart TD
    Client(["User Prompt<br>Open-WebUI :3000"]) -->|POST /v1/chat/completions| Router["Quota Router Gateway<br>FastAPI :8000 (Host :8088)"]

    Router --> CheckHeadroom{"Headroom Proxy Model?"}
    HeadroomDirect["Direct Pass-Through<br>Headroom Proxy :8787"]
    CheckHeadroom -->|Yes| HeadroomDirect

    CheckHeadroom -->|No| PreRead["1. Workspace Context & AST Skeletonizer<br>• Discover target files<br>• Compress definitions to signatures ('...')<br>• Save up to 80% input tokens"]

    PreRead --> CheckRoute{"Routing Gate<br>Forced @local or simple Q&A?"}

    CheckRoute -->|Yes| LocalTriage["2a. Local Ollama (0 Cloud Tokens)<br>• Fast local response<br>• Auto-persisted to /workspace disk"]

    CheckRoute -->|No (Coding / Complex)| DispatchAgy["2b. Tech Lead Dispatch: agy (Claude 3.5 Sonnet)<br>• Full solution written in 1 turn via MCP write_to_file<br>• Boilerplate/seed data delegated to ask_local_assistant<br>• Fast symbol lookup via trace_symbol<br>• Command sandbox blocking runaway test logs"]

    DispatchAgy --> CheckStatus{"agy Result Code"}

    CheckStatus -->|Exit 0| Deliver["3. Response & SSE Stream Completion<br>Files persisted to /workspace"]
    CheckStatus -->|Non-Zero / Quota / Timeout| FallbackOllama["*[Fallback: Local Ollama]*<br>Local Model Code Gen & Disk Write"]

    HeadroomDirect --> Envelope["OpenAI-Compatible Response Envelope"]
    LocalTriage --> Envelope
    Deliver --> Envelope
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
| **`ollama`** | `local-ollama` | `11434` | `11434` | Local inference engine (`qwen2.5-coder:1.5b`) for zero-cost boilerplate generation, in-flight MCP assistance, and offline fallback. |
| **`open-webui`** | `open-webui` | `3000` | `8080` | Modern chat interface connected to `http://router:8000/v1` and `http://headroom:8787/v1`. |

---

## Codebase Directory Layout (`services/router`)

The router service follows a lean, minimalist architecture with zero dead code or band-aids:

```text
services/router/
├── Dockerfile
├── requirements.txt
├── src/
│   ├── __init__.py
│   ├── app.py                   # FastAPI Gateway, Keep-Alive SSE Stream, & Tech Lead Router (574 lines)
│   ├── ast_compressor.py        # AST Code Skeletonizer & Token Compressor (241 lines)
│   └── mcp_workspace.py         # Headless Zero-Cost MCP Tool Server (589 lines)
└── tests/
    ├── __init__.py
    ├── test_ast_compressor.py   # AST Compression Unit Tests
    └── test_mcp_workspace.py    # MCP Tool, Circuit Breaker & Routing Tests
```

---

## Multi-Agent Personas & Tech Lead Protocol

The stack supports specialized personas declared in `.antigravity/agents/`:

| Persona | Triggers | Description | Division of Labor Protocol |
| :--- | :--- | :--- | :--- |
| **`coder`** *(Default)* | `@coder`, `/coder`, `@dev` | Senior Lead Engineer: designs architecture, implements core business logic, and writes unit tests in 1 turn. | Claude 3.5 Sonnet implements core logic directly via `write_to_file`. Delegates dummy CSVs, seed databases, or repetitive boilerplate to local Ollama via `ask_local_assistant`. |
| **`reviewer`** | `@reviewer`, `/reviewer`, `@audit` | Senior Security & Quality Auditor: inspects code for vulnerabilities, edge-case bugs, and missing error branches. | Performs static analysis and provides concise architectural diffs and recommendations. |
| **`architect`** | `@architect`, `/architect` | System Architect: pre-implementation blueprints, interface contracts, and phased technical roadmaps. | Outputs structural schemas and system designs without unnecessary boilerplate. |
| **`tester`** | `@tester`, `/tester`, `@test` | Test Authoring & Verification Specialist: unit test design, regression testing, and static verification. | Authors test suites directly in `tests/test_*.py`. Formulates exact quiet, fail-fast test execution commands for the developer or CI. |

### Global Directives (`AGENTS.md`)
Enforces strict operational safety:
- **Confined Operational Scope**: All operations are strictly confined to `/workspace`.
- **Pre-Read Context as Source of Truth**: Uses pre-read AST skeletons to prevent repetitive `view_file` calls.
- **1-Turn Convergence**: Solves problems completely in a single turn without leaving `TODO` placeholders.
- **Test Prohibition in Agent Loop**: Prevents running long-running test suites inside the agent loop to avoid flooding the context window with terminal logs; instructs the user to run tests locally under Next Steps.

---

## Key Features

- **Tech Lead Orchestrator (`AGY_MODEL=claude-3-5-sonnet`)**: Claude 3.5 Sonnet directly writes production code and test suites via MCP filesystem tools without arbitrary line limits or artificial handcuffs.
- **Zero-Cloud-Token Boilerplate Delegation**: Claude delegates low-cognitive-load files (e.g. 50-line mock domain CSVs, seed fixtures) to local Ollama via `ask_local_assistant(..., target_file=...)` at **0 cloud tokens**.
- **AST Skeletonizer (`ast_compressor.py`)**: Replaces Python function and method bodies with `...` to extract interface signatures, saving up to **80% of context tokens** while preserving full structural hierarchy.
- **Live SSE Streaming & Keep-Alive Pings**: Sends an immediate role chunk followed by periodic `: keep-alive` comments to prevent browser or reverse-proxy timeouts during deep multi-file turns.
- **Zero-Cost MCP Server (`mcp_workspace.py`)**:
  - `write_to_file` & `replace_file_content`: Safe filesystem persistence directly to `/workspace`.
  - `ask_local_assistant`: Queries local Ollama with optional `target_file` disk persistence.
  - `trace_symbol`: Fast cross-language symbol lookup (Python, TypeScript, JavaScript, Java).
  - `run_command`: Programmatically sandboxed shell execution with 30s timeout and test-runner blocking.
  - `delete_file`: Batch deletion of obsolete files.
- **Auto-Persistence Safety Net**: Automatically detects markdown code blocks in text responses and saves them to `/workspace` if fallback Ollama outputs text instead of calling tools.
- **Real-Time Telemetry (`/stats`)**: Query `http://localhost:8088/stats` for live cumulative statistics on AST tokens saved and Headroom proxy cache metrics.

---

## Recommended Local Ollama Models per Hardware Specs

| Hardware Tier | System RAM / VRAM | GPU / CPU Target | Recommended Ollama Model | Quantization | RAM / VRAM Footprint | Performance & Use Case |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Ultra Light / CPU-only** | 8 GB RAM (0 GB VRAM) | Dual-core / Quad-core CPU | `qwen2.5-coder:0.5b` | `Q4_K_M` | ~350 MB | Ultra-fast (~40 t/s on CPU). Ideal for instant signature lookups on low-spec devices. |
| **Standard / Lightweight** *(Default)* | 8–16 GB RAM (2–4 GB VRAM) | Integrated GPU / Entry Card (GTX 1650 / M1) | `qwen2.5-coder:1.5b` | `Q4_K_M` | ~1.1 GB | Excellent balance (~50–80 t/s). Instant local code drafting and boilerplate generation. |
| **Intermediate Workstation** | 16–32 GB RAM (6–8 GB VRAM) | Mid-range GPU (RTX 3060/4060 / M2-M3 16GB) | `qwen2.5-coder:7b` | `Q4_K_M` | ~4.5 GB | High reasoning accuracy. Great at multi-file mock generation and unit test drafting. |
| **High Performance** | 32–64 GB RAM (12–16 GB VRAM) | High-end GPU (RTX 3080/4080 / M2/M3 Pro 32GB) | `qwen2.5-coder:14b` or `deepseek-coder-v2:16b` | `Q4_K_M` | ~9.0 GB | Near-cloud reasoning quality. Performs structural refactoring & complex local code generation. |
| **Enterprise Workstation** | 64+ GB RAM (24+ GB VRAM) | Top-tier GPU (RTX 3090/4090 / M2-M3 Ultra 64GB+) | `qwen2.5-coder:32b` or `codestral:22b` | `Q4_K_M` / `Q8_0` | ~20 GB | State-of-the-art local coding capability. Rivals top cloud models on local code generation. |

---

## Testing & Verification

Run the test suite inside the running Docker container:

```bash
docker compose exec router python -m unittest discover -s tests
```

Or execute using an ephemeral container:

```bash
docker compose run --rm -v "${PWD}/services/router:/app" -e PYTHONPATH=/app/src router python -m unittest discover -s tests
```

---

## Getting Started

### Prerequisites
- [Docker](https://docs.docker.com/get-docker/) & Docker Compose v2+
- Linux, macOS, or Windows WSL2
- [Antigravity CLI](https://github.com/google/antigravity) (`agy`) installed and authenticated on host
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

3. **Build and launch the stack:**
   ```bash
   docker compose up -d --build
   ```

   > [!TIP]
   > **Windows WSL2 Users**: Always run Docker Compose commands (`docker compose up -d --build`) from inside your **WSL Linux terminal** (e.g. `Ubuntu`). This ensures environment variables like `HOST_HOME=${HOME}` map properly to your Linux user home directory where `.local/bin/agy`, `.gemini`, and `.config` reside.

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
| `AGY_MODEL` | `claude-3-5-sonnet` | Model target used by Antigravity CLI (`agy --model`). Supports `claude-3-5-sonnet`, `claude-3-7-sonnet`, `gpt-oss-120b`. |
| `AGY_TIMEOUT` | `180` | Execution timeout in seconds for `agy` (default: 180s). |
| `OLLAMA_MODEL` | `qwen2.5-coder:1.5b` | Model used for local boilerplate delegation and fallback. |
| `OLLAMA_TIMEOUT` | `180` | Timeout in seconds for local Ollama inference (default: 180s). |
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

---

## Usage & API Testing

### 1. Persona Prompts in Open WebUI
In [http://localhost:3000](http://localhost:3000), select `auto-router` (or any persona model) and type:
- `@coder Implement the email domain validation script and unit tests`
- `@reviewer Check src/auth.py for unhandled exceptions and security risks`
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