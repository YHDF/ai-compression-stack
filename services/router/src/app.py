import os
import re
import time
import json
import queue
import threading
import shutil
import subprocess
import requests
from typing import List, Union, Dict, Any, Optional
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Local AI Context-Router & Compression Stack")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
HEADROOM_PROXY = os.getenv("HEADROOM_PROXY", "http://headroom:8787").rstrip("/")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:1.5b").strip()
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "180"))
AGY_MODEL = os.getenv("AGY_MODEL", "claude-3-5-sonnet").strip()
AGY_TIMEOUT = int(os.getenv("AGY_TIMEOUT", "180"))
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/workspace")

MAX_PRE_READ_SIZE = 50 * 1024  # 50 KB limit per file
MAX_TOTAL_PRE_READ_SIZE = 40 * 1024  # 40 KB cumulative context budget
IGNORED_EXTENSIONS = {
    ".log", ".lock", ".tmp", ".bin", ".tar", ".gz", ".zip", ".7z",
    ".pyc", ".pyo", ".pyd", ".db", ".sqlite", ".sqlite3", ".parquet",
    ".csv", ".tsv", ".jsonl", ".ndjson",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".eot"
}


def ensure_workspace_mcp():
    """Ensure the local workspace-tools MCP server and agent personas are registered for agy."""
    try:
        home_dir = os.getenv("HOME", "/home/appuser")
        mcp_script = "/app/src/mcp_workspace.py" if os.path.exists("/app/src/mcp_workspace.py") else "/app/mcp_workspace.py"

        server_def = {
            "command": "python3",
            "args": [mcp_script],
            "timeout": 300,
            "toolTimeout": 300,
            "disabled": False
        }

        config_paths = [
            os.path.join(home_dir, ".gemini", "config", "mcp_config.json"),
            os.path.join(home_dir, ".gemini", "antigravity-cli", "mcp_config.json"),
            os.path.join(WORKSPACE_DIR, ".agents", "mcp_config.json"),
        ]
        for cfg_path in config_paths:
            try:
                os.makedirs(os.path.dirname(cfg_path), exist_ok=True)
                data = {}
                if os.path.exists(cfg_path):
                    try:
                        with open(cfg_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception:
                        data = {}
                data.setdefault("mcpServers", {})["workspace_tools"] = server_def
                with open(cfg_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass

        # Sync persona markdown files if available
        src_agents = "/app/agents" if os.path.isdir("/app/agents") else "/app/.antigravity/agents"
        dest_agents = os.path.join(home_dir, ".gemini", "config", "agents")
        if os.path.isdir(src_agents):
            os.makedirs(dest_agents, exist_ok=True)
            for fname in os.listdir(src_agents):
                if fname.endswith(".md"):
                    shutil.copy2(os.path.join(src_agents, fname), os.path.join(dest_agents, fname))

        print("[ROUTER] Registered workspace_tools MCP server and synced personas", flush=True)
    except Exception as e:
        print(f"Notice: could not ensure workspace MCP server: {e}", flush=True)


ensure_workspace_mcp()


class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]


class ChatCompletionRequest(BaseModel):
    model: str = "auto-router"
    messages: List[ChatMessage]
    stream: Optional[bool] = False


def package_response(model_name: str, content: str):
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop"
            }
        ]
    }


def parse_parts(content: Union[str, List[Dict[str, Any]]]):
    """Extract clean text content and compatibility tuple from input parts."""
    if isinstance(content, str):
        return content.strip(), [], False
    if isinstance(content, list):
        text_parts = [item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text"]
        has_image = any(isinstance(item, dict) and item.get("type") == "image_url" for item in content)
        return "\n".join(text_parts).strip(), [], has_image
    return str(content).strip(), [], False


def call_ollama_generation(prompt: str, persona: str = "coder", ws_context: str = "") -> str:
    """Invoke local Ollama for zero-cloud-token triage, boilerplate, or fallback generation."""
    print(f"[ROUTER] Invoking Ollama (model={OLLAMA_MODEL}, persona={persona})", flush=True)
    try:
        context_block = f"\n## Workspace Context:\n{ws_context}\n\n" if ws_context else ""
        system_directive = (
            "You are an autonomous local code generator. "
            "Write production-ready, complete code without placeholders or omissions.\n"
            "Format file blocks with explicit markdown headers: ### [path/to/file.ext]\n```lang\n<code>\n```\n\n"
        )
        formatted_prompt = f"{system_directive}{context_block}Task:\n{prompt}"
        resp = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": formatted_prompt, "stream": False},
            timeout=OLLAMA_TIMEOUT
        )
        if resp.status_code == 200:
            return resp.json().get("response", "")
        return f"Ollama HTTP {resp.status_code}: {resp.text}"
    except Exception as e:
        return f"Local Ollama error: {str(e)}"


@app.get("/healthz")
def healthz():
    """Verify network reachability to HEADROOM_PROXY and OLLAMA_URL."""
    ollama_ok = False
    headroom_ok = False
    try:
        r = requests.get(f"{OLLAMA_URL}/api/version", timeout=3)
        ollama_ok = (r.status_code == 200)
    except Exception:
        ollama_ok = False

    try:
        r = requests.get(f"{HEADROOM_PROXY}/healthz", timeout=3)
        headroom_ok = (r.status_code in (200, 404))
    except Exception:
        headroom_ok = False

    all_ok = ollama_ok and headroom_ok
    return JSONResponse(
        status_code=200 if all_ok else 503,
        content={
            "status": "ok" if all_ok else "degraded",
            "services": {
                "ollama": "reachable" if ollama_ok else "unreachable",
                "headroom": "reachable" if headroom_ok else "unreachable"
            },
            "ollama_url": OLLAMA_URL,
            "headroom_proxy": HEADROOM_PROXY,
            "workspace_dir": WORKSPACE_DIR
        }
    )


@app.get("/v1/models")
@app.get("/models")
def list_models():
    """OpenAI-compatible model registry for Open WebUI discovery."""
    models_def = [
        ("auto-router", "Dynamic quota-aware context router"),
        ("coder", "Antigravity Coder: Tech Lead implementation & tests"),
        ("reviewer", "Antigravity Reviewer: code & security auditor"),
        ("architect", "Antigravity Architect: system design & roadmap"),
        ("tester", "Antigravity Tester: test design & static verification"),
        ("headroom-proxy", "Headroom Compression Proxy pass-through")
    ]
    return {
        "object": "list",
        "data": [
            {
                "id": mid,
                "object": "model",
                "created": 1700000000,
                "owned_by": "local-compression-stack",
                "permission": [],
                "root": mid,
                "parent": None,
                "name": mid
            }
            for mid, _ in models_def
        ]
    }


@app.get("/stats")
def stats():
    """Aggregate token compression statistics from AST engine and Headroom proxy."""
    try:
        from ast_compressor import get_stats
        ast_stats = get_stats()
    except Exception as e:
        ast_stats = {"error": str(e)}

    headroom_stats = {}
    try:
        r = requests.get(f"{HEADROOM_PROXY}/stats", timeout=3)
        if r.status_code == 200:
            headroom_stats = r.json()
    except Exception as e:
        headroom_stats = {"error": f"Headroom unreachable: {e}"}

    return {"ast_compression": ast_stats, "headroom_proxy": headroom_stats}


def read_target_files(target_files: List[str]) -> str:
    """Ingests target files from /workspace under a cumulative budget, applying AST compression."""
    if not target_files or not os.path.exists(WORKSPACE_DIR):
        return ""

    context_blocks = []
    workspace_root = os.path.abspath(WORKSPACE_DIR)
    total_bytes = 0

    for raw_path in target_files:
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        clean_path = raw_path.strip()
        abs_path = os.path.abspath(clean_path if os.path.isabs(clean_path) else os.path.join(workspace_root, clean_path))

        if not abs_path.startswith(workspace_root) or not os.path.isfile(abs_path):
            continue

        ext = os.path.splitext(abs_path)[1].lower()
        if ext in IGNORED_EXTENSIONS or "log" in os.path.basename(abs_path).lower():
            continue

        try:
            file_size = os.path.getsize(abs_path)
            if file_size > MAX_PRE_READ_SIZE or (total_bytes + file_size > MAX_TOTAL_PRE_READ_SIZE):
                continue

            with open(abs_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            rel_path = os.path.relpath(abs_path, workspace_root).replace("\\", "/")
            lang = ext.lstrip(".") if ext else "text"

            try:
                from ast_compressor import compress_code_snippet
                comp_content, orig_t, comp_t = compress_code_snippet(content, filename=rel_path)
                saved_pct = int((orig_t - comp_t) / orig_t * 100) if orig_t > comp_t else 0
                tag = f" (AST Compressed: -{saved_pct}% tokens)" if saved_pct > 0 else ""
                context_blocks.append(f"### File: {rel_path}{tag}\n```{lang}\n{comp_content}\n```")
            except Exception:
                context_blocks.append(f"### File: {rel_path}\n```{lang}\n{content}\n```")
            total_bytes += file_size
        except Exception as e:
            print(f"Notice: Error reading file {abs_path}: {e}")

    return ("## Workspace Pre-Read Context:\n" + "\n\n".join(context_blocks)) if context_blocks else ""


def discover_relevant_files(prompt: str) -> List[str]:
    """Auto-discover relevant workspace files mentioned in prompt or top source files."""
    if not os.path.exists(WORKSPACE_DIR):
        return []
    ws_root = Path(WORKSPACE_DIR).resolve()
    explicit = []
    for token in re.findall(r'[a-zA-Z0-9_\-./\\]+\.[a-zA-Z0-9_\-]+', prompt):
        p = (ws_root / token.strip("`'\"[]()*,")).resolve()
        if p.is_file() and p.is_relative_to(ws_root):
            rel = str(p.relative_to(ws_root)).replace("\\", "/")
            if rel not in explicit:
                explicit.append(rel)
    if explicit:
        return explicit
    core_files = []
    for ext in (".py", ".json", ".yaml", ".yml", ".md", ".ts", ".js"):
        for p in ws_root.glob(f"*{ext}"):
            if p.is_file() and not p.name.startswith("."):
                core_files.append(str(p.relative_to(ws_root)).replace("\\", "/"))
                if len(core_files) >= 3:
                    return core_files
    return core_files


def apply_guardrails(beautified_prompt: str, context_str: str) -> str:
    """Position Claude as Tech Lead: writes production code directly in 1 turn, delegates boilerplate/seed data to Ollama."""
    guardrails = (
        "## Operational Directives (TECH LEAD ORCHESTRATION PROTOCOL):\n"
        "- TECH LEAD EXECUTION: You are the senior lead engineer. Implement the core architecture, business logic, and test suites directly using 'write_to_file' and 'replace_file_content'.\n"
        "- LOCAL DELEGATION FOR BOILERPLATE: For repetitive boilerplate, seed data, or mock CSV generation, you may delegate to local Ollama via 'ask_local_assistant(query=\"...\", target_file=\"path/to/file.ext\")' to save cloud tokens.\n"
        "- EFFICIENT CONTEXT: Relevant existing code context is provided below. Use 'trace_symbol' for fast symbol lookup instead of scanning the whole repo.\n"
        "- 1-TURN CONVERGENCE: Deliver complete, production-ready, fully working implementations in a single turn without leaving placeholders or TODOs.\n"
        "- Absolute Test Prohibition: NEVER run long-running test suites or blocking commands. Instruct the user on how to run tests under Next Steps.\n\n"
    )
    parts = [guardrails]
    if context_str:
        parts.append(context_str + "\n\n")
    parts.append(f"## Architectural Task Specification:\n{beautified_prompt}")
    return "".join(parts)


def auto_persist_code_blocks(output_text: str, workspace_dir: str, default_target_files: Optional[List[str]] = None) -> List[str]:
    """Safety net: Writes markdown code blocks to disk if output wasn't executed via tools."""
    if not output_text or not os.path.exists(workspace_dir):
        return []

    from mcp_workspace import normalize_path
    persisted = []
    ws_root = Path(workspace_dir).resolve()

    code_block_re = re.compile(r"(?:###\s*\[?([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9_\-]+)\]?\s*\n)?```([a-zA-Z0-9_\-]*)\n(.*?)```", re.DOTALL)
    assigned_targets = list(default_target_files) if default_target_files else []
    target_idx = 0

    for match in code_block_re.finditer(output_text):
        header_file = match.group(1)
        code = match.group(3)
        if not code or len(code.strip()) < 10:
            continue

        target_file = header_file
        if not target_file and target_idx < len(assigned_targets):
            target_file = assigned_targets[target_idx]
            target_idx += 1

        if not target_file:
            continue

        try:
            resolved = normalize_path(target_file)
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(code, encoding="utf-8")
            rel_name = str(resolved.relative_to(ws_root)).replace("\\", "/")
            if rel_name not in persisted:
                persisted.append(rel_name)
            print(f"[ROUTER] Auto-persisted deliverable: {resolved}", flush=True)
        except Exception as e:
            print(f"[ROUTER] Failed auto-persisting {target_file}: {e}", flush=True)

    return persisted




def execute_agy(prepared_prompt: str, agent_id: Optional[str] = None) -> Optional[str]:
    """Execute agy CLI non-interactively with active AGY_MODEL and workspace cwd."""
    agy_path = shutil.which("agy") or "/usr/local/bin/agy"
    if not os.path.exists(agy_path) or not os.access(agy_path, os.X_OK):
        print(f"[ROUTER] agy executable not found at {agy_path}", flush=True)
        return None

    cmd = [
        agy_path,
        "--model", AGY_MODEL,
        "--mode", "accept-edits",
        "--dangerously-skip-permissions",
        "--max-turns", "2",
        "--print-timeout", f"{AGY_TIMEOUT}s"
    ]
    if agent_id:
        cmd.extend(["--agent", agent_id])
    cmd.extend(["-p", prepared_prompt])

    preview = (prepared_prompt[:80] + "...") if len(prepared_prompt) > 80 else prepared_prompt
    print(f"[ROUTER] Target: AGY | Persona: {agent_id or 'default'} | Model: {AGY_MODEL} | Prompt: {preview}", flush=True)

    env = os.environ.copy()
    for proxy_var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
        env.pop(proxy_var, None)
    home_dir = os.getenv("HOME") or "/home/appuser"
    env["HOME"] = home_dir
    cwd = WORKSPACE_DIR if os.path.exists(WORKSPACE_DIR) else "/workspace"

    try:
        proc = subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=AGY_TIMEOUT + 15, env=env, cwd=cwd)
        output = proc.stdout.strip()
        stderr_lower = proc.stderr.lower() if proc.stderr else ""

        if proc.returncode != 0:
            err_msg = (proc.stderr or proc.stdout or "").strip()
            print(f"[ROUTER] agy exited with code {proc.returncode}: {err_msg}", flush=True)
            return None

        if "quota exceeded" in stderr_lower or "resource_exhausted" in stderr_lower or "rate limit exceeded" in stderr_lower:
            print(f"[ROUTER] agy reported quota/rate limit error: {proc.stderr.strip()}", flush=True)
            return None

        if output and agent_id == "coder":
            try:
                auto_persist_code_blocks(output, cwd)
            except Exception as e:
                print(f"[ROUTER] Notice in auto_persist_code_blocks: {e}", flush=True)

        return output if output else "Task completed successfully."
    except Exception as e:
        print(f"[ROUTER] agy subprocess error: {e}", flush=True)
        return None


def process_chat_request(req: ChatCompletionRequest) -> str:
    """Main routing pipeline: verbatim bypass -> context prep -> Tech Lead agy -> local fallback."""
    last_msg = next((m for m in reversed(req.messages) if m.role == "user"), None)
    if not last_msg:
        return "No prompt received."

    prompt_text, _, _ = parse_parts(last_msg.content)
    clean_prompt = prompt_text.strip()

    # 1. Persona Detection via Prefix
    agent_id = req.model if req.model in ["coder", "reviewer", "architect", "tester"] else "coder"
    for prefix, persona in [
        ("@reviewer", "reviewer"), ("/reviewer", "reviewer"), ("@audit", "reviewer"),
        ("@architect", "architect"), ("/architect", "architect"),
        ("@tester", "tester"), ("/tester", "tester"), ("@test", "tester"),
        ("@coder", "coder"), ("/coder", "coder"), ("@dev", "coder")
    ]:
        if clean_prompt.startswith(prefix):
            agent_id = persona
            clean_prompt = clean_prompt.split(maxsplit=1)[1] if " " in clean_prompt else ""
            break

    # 2. Direct Headroom Route
    if "headroom" in req.model.lower():
        try:
            r = requests.post(f"{HEADROOM_PROXY}/v1/chat/completions", json=req.model_dump(), timeout=AGY_TIMEOUT)
            if r.status_code == 200:
                resp_json = r.json()
                return resp_json.get("choices", [{}])[0].get("message", {}).get("content", "")
            return f"*[Headroom Proxy Error: HTTP {r.status_code}]*"
        except Exception as e:
            return f"*[Headroom Proxy Error]*\n\n{e}"


    # 4. Inbound Triage: Query Ollama for Target Files & Complexity
    target_files = []
    complexity = "complex"
    try:
        structured_schema = {
            "type": "object",
            "properties": {
                "beautified_prompt": {"type": "string"},
                "target_files": {"type": "array", "items": {"type": "string"}},
                "complexity": {"type": "string", "enum": ["trivial", "complex"]}
            },
            "required": ["beautified_prompt", "complexity"]
        }
        res = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": f"Analyze task and extract target files and complexity (trivial vs complex):\n\n{clean_prompt}",
                "format": structured_schema,
                "stream": False
            },
            timeout=OLLAMA_TIMEOUT
        )
        if res.status_code == 200:
            raw_meta = json.loads(res.json().get("response", "{}"))
            if isinstance(raw_meta, dict):
                target_files = raw_meta.get("target_files", [])
                complexity = raw_meta.get("complexity", "complex")
    except Exception as e:
        print(f"[ROUTER] Inbound triage notice: {e}", flush=True)

    # 5. Workspace Context Pre-Reading (AST-Compressed)
    real_targets = [tf.strip().lstrip("/") for tf in target_files if (Path(WORKSPACE_DIR) / tf.strip().lstrip("/")).is_file()]
    if not real_targets:
        real_targets = discover_relevant_files(clean_prompt)
    ws_context = read_target_files(real_targets)

    # 6. Routing Decision: Forced Local vs Tech Lead (agy)
    forced_cloud = any(clean_prompt.lower().startswith(p) for p in ["@agy", "/agy", "@cloud", "/cloud", "@claude", "/claude"])
    forced_local = any(clean_prompt.lower().startswith(p) for p in ["@local", "/local", "@ollama", "/ollama"])
    is_coding = any(kw in clean_prompt.lower() for kw in ["def ", "class ", "script", "test", "implement", "write", "create", "api", ".py", ".ts", ".js"])

    # Only route to local Ollama if explicitly asked or purely trivial non-coding Q&A
    if (forced_local or (complexity == "trivial" and not is_coding and not forced_cloud)) and not forced_cloud:
        print(f"[ROUTER] Target: Local Ollama (Triage: 0 Cloud Tokens) | Persona: {agent_id}", flush=True)
        local_out = call_ollama_generation(clean_prompt or prompt_text, persona=agent_id, ws_context=ws_context)
        saved = auto_persist_code_blocks(local_out, WORKSPACE_DIR, default_target_files=target_files) if agent_id == "coder" else []
        saved_badge = f"\n\n*[Files saved: {', '.join(saved)}]*" if saved else ""
        return f"*[Local Triage: Ollama ({agent_id.capitalize()} | 0 Cloud Tokens)]*{saved_badge}\n\n{local_out}"

    # 7. Dispatch to Tech Lead Orchestrator (agy / Claude 3.5 Sonnet)
    guarded_prompt = apply_guardrails(clean_prompt, ws_context)
    agy_output = execute_agy(guarded_prompt, agent_id=agent_id)
    if agy_output is not None:
        return f"*[Agent Task: {agent_id.capitalize()} ({AGY_MODEL})]*\n\n{agy_output}"

    # 8. Local Ollama Safety Fallback
    print(f"[ROUTER] agy unavailable; invoking Ollama fallback", flush=True)
    fallback_out = call_ollama_generation(clean_prompt or prompt_text, persona=agent_id, ws_context=ws_context)
    saved = auto_persist_code_blocks(fallback_out, WORKSPACE_DIR, default_target_files=target_files) if agent_id == "coder" else []
    saved_badge = f"\n\n*[Files saved: {', '.join(saved)}]*" if saved else ""
    return f"*[Fallback: Local Ollama ({agent_id.capitalize()})]*{saved_badge}\n\n{fallback_out}"


def generate_stream_response(req: ChatCompletionRequest):
    """Format response as live SSE stream with periodic keep-alive pings to prevent client timeout."""
    def event_stream():
        chunk_id = f"chatcmpl-{int(time.time())}"
        created_ts = int(time.time())

        # 1. Yield initial chunk so client registers stream start immediately
        yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_ts, 'model': req.model, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': ''}, 'finish_reason': None}]})}\n\n"

        # 2. Run pipeline asynchronously in worker thread while emitting keep-alive pings
        res_q = queue.Queue()

        def worker():
            try:
                res_q.put(("ok", process_chat_request(req)))
            except Exception as e:
                res_q.put(("err", str(e)))

        t = threading.Thread(target=worker, daemon=True)
        t.start()

        content = None
        while t.is_alive():
            try:
                status, payload = res_q.get(timeout=2.0)
                content = payload if status == "ok" else f"*[Pipeline Error]*\n\n{payload}"
                break
            except queue.Empty:
                yield ": keep-alive\n\n"

        if content is None:
            try:
                status, payload = res_q.get(timeout=2.0)
                content = payload if status == "ok" else f"*[Pipeline Error]*\n\n{payload}"
            except queue.Empty:
                content = "*[Router Notice: Processing finished with no output]*"

        # 3. Stream content in line chunks
        for line in content.splitlines(keepends=True) or [content]:
            yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_ts, 'model': req.model, 'choices': [{'index': 0, 'delta': {'content': line}, 'finish_reason': None}]})}\n\n"

        # 4. Stream completion finish chunk
        yield f"data: {json.dumps({'id': chunk_id, 'object': 'chat.completion.chunk', 'created': created_ts, 'model': req.model, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"}
    )


@app.post("/v1/chat/completions")
@app.post("/chat/completions")
def completions(req: ChatCompletionRequest):
    if req.stream:
        return generate_stream_response(req)
    content = process_chat_request(req)
    return package_response(req.model, content)
