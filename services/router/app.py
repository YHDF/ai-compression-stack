import os
import time
import json
import base64
import shutil
import subprocess
import requests
from typing import List, Union, Dict, Any, Optional
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI(title="Local AI Context-Router & Compression Stack")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
HEADROOM_PROXY = os.getenv("HEADROOM_PROXY", "http://headroom:8787").rstrip("/")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:0.5b").strip()
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "300"))
AGY_MODEL = os.getenv("AGY_MODEL", "gpt-oss-120b-medium").strip()
AGY_TIMEOUT = int(os.getenv("AGY_TIMEOUT", "180"))
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/workspace")

MAX_PRE_READ_SIZE = 50 * 1024  # 50 KB limit
IGNORED_EXTENSIONS = {
    ".log", ".lock", ".tmp", ".bin", ".tar", ".gz", ".zip", ".7z",
    ".pyc", ".pyo", ".pyd", ".db", ".sqlite", ".sqlite3", ".parquet",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".woff", ".woff2", ".ttf", ".eot"
}

# Initialize Gemini client only if a non-empty key is provided
gemini_client = None
if GEMINI_API_KEY and GEMINI_API_KEY != "your_key_here":
    try:
        from google import genai
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"Notice: Gemini client not initialized: {e}")

def ensure_workspace_mcp():
    """Ensure that the local workspace-tools MCP server is registered for agy."""
    try:
        home_dir = os.getenv("HOME", "/home/appuser")
        config_path = os.path.join(home_dir, ".gemini", "config", "mcp_config.json")
        os.makedirs(os.path.dirname(config_path), exist_ok=True)
        data = {}
        if os.path.exists(config_path):
            try:
                with open(config_path, "r") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        servers = data.setdefault("mcpServers", {})
        mcp_script = "/app/mcp_workspace.py"
        if "workspace_tools" not in servers and os.path.exists(mcp_script):
            servers["workspace_tools"] = {
                "command": "python3",
                "args": [mcp_script],
                "disabled": False
            }
            with open(config_path, "w") as f:
                json.dump(data, f, indent=2)
            print("[ROUTER] Registered workspace_tools MCP server in mcp_config.json", flush=True)
    except Exception as e:
        print(f"Notice: could not ensure workspace MCP server: {e}", flush=True)

ensure_workspace_mcp()

class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]

class ChatCompletionRequest(BaseModel):
    model: str = "auto-router"
    messages: List[ChatMessage]

def package_response(model_name: str, content: str):
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content
                },
                "finish_reason": "stop"
            }
        ]
    }

def call_ollama_generation(prompt: str) -> str:
    """Fallback or direct code generation using local Ollama model."""
    print(f"[ROUTER] Invoking Ollama fallback (model={OLLAMA_MODEL})", flush=True)
    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False
        }
        resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=OLLAMA_TIMEOUT)
        if resp.status_code == 200:
            return resp.json().get("response", "")
        return f"Ollama generation returned HTTP {resp.status_code}: {resp.text}"
    except Exception as e:
        return f"Local Ollama generation error: {str(e)}"

def parse_parts(content):
    text = ""
    parts = []
    has_image = False

    if isinstance(content, str):
        text = content
        try:
            from google.genai import types
            parts.append(types.Part.from_text(text=text))
        except Exception:
            pass
    elif isinstance(content, list):
        for item in content:
            if item.get("type") == "text":
                part_text = item.get("text", "")
                text += part_text + "\n"
                try:
                    from google.genai import types
                    parts.append(types.Part.from_text(text=part_text))
                except Exception:
                    pass
            elif item.get("type") == "image_url":
                has_image = True
                url = item.get("image_url", {}).get("url", "")
                try:
                    from google.genai import types
                    if "base64," in url:
                        header, data = url.split("base64,", 1)
                        mime = header.split(";")[0].replace("data:", "")
                        parts.append(types.Part.from_bytes(data=base64.b64decode(data), mime_type=mime))
                    elif url.startswith("http"):
                        r = requests.get(url, timeout=10)
                        parts.append(types.Part.from_bytes(data=r.content, mime_type=r.headers.get("Content-Type", "image/png")))
                except Exception as e:
                    print(f"Error parsing image: {e}")

    return text.strip(), parts, has_image

@app.get("/healthz")
def healthz():
    """Verify network reachability to both HEADROOM_PROXY and OLLAMA_URL."""
    ollama_ok = False
    headroom_ok = False

    try:
        r_ol = requests.get(f"{OLLAMA_URL}/api/version", timeout=3)
        ollama_ok = (r_ol.status_code == 200)
    except Exception:
        try:
            r_ol = requests.get(OLLAMA_URL, timeout=3)
            ollama_ok = (r_ol.status_code == 200)
        except Exception:
            ollama_ok = False

    try:
        r_hr = requests.get(f"{HEADROOM_PROXY}/healthz", timeout=3)
        headroom_ok = (r_hr.status_code in (200, 404))
    except Exception:
        try:
            r_hr = requests.get(HEADROOM_PROXY, timeout=3)
            headroom_ok = True
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
    """Exposes OpenAI-compatible model registry for Open WebUI discovery."""
    models_def = [
        ("auto-router", "Dynamic quota-aware context router"),
        ("coder", "Antigravity Coder: minimal-diff implementation & tests"),
        ("reviewer", "Antigravity Reviewer: security & quality auditor"),
        ("architect", "Antigravity Architect: system design & roadmap planner"),
        ("headroom-proxy", "Headroom Compression Proxy (Direct upstream pass-through)")
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

    return {
        "ast_compression": ast_stats,
        "headroom_proxy": headroom_stats
    }

MAX_TOTAL_PRE_READ_SIZE = 40 * 1024  # 40 KB cumulative budget

def read_target_files(target_files: List[str]) -> str:
    """Router Pre-Reader: Ingests matching target files from /workspace under a cumulative size budget."""
    if not target_files or not os.path.exists(WORKSPACE_DIR):
        return ""

    context_blocks = []
    workspace_root = os.path.abspath(WORKSPACE_DIR)
    total_bytes = 0

    for raw_path in target_files:
        if not isinstance(raw_path, str) or not raw_path.strip():
            continue
        clean_path = raw_path.strip()

        # Resolve path within workspace
        if os.path.isabs(clean_path):
            abs_path = os.path.abspath(clean_path)
        else:
            abs_path = os.path.abspath(os.path.join(workspace_root, clean_path))

        # Security check: avoid directory traversal outside workspace
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

    if context_blocks:
        return "## Workspace Pre-Read Context:\n" + "\n\n".join(context_blocks)
    return ""

def discover_relevant_files(prompt: str) -> List[str]:
    """Auto-discover relevant workspace files using keyword matching or local Ollama dependency analysis."""
    if not os.path.exists(WORKSPACE_DIR):
        return []

    # 1. Check if user explicitly mentioned file paths in prompt
    words = prompt.replace("`", " ").replace('"', ' ').replace("'", " ").replace(",", " ").split()
    explicit = [w for w in words if "." in w and not w.startswith("@") and not w.startswith("/") and not w.endswith(".")]
    valid_explicit = []
    for f in explicit:
        full_p = os.path.join(WORKSPACE_DIR, f)
        if os.path.isfile(full_p):
            valid_explicit.append(f)
    if valid_explicit:
        print(f"[ROUTER] Explicit target file(s) matched: {valid_explicit}", flush=True)
        return valid_explicit

    # 2. Collect code/config files in workspace (hierarchical scan)
    workspace_files = []
    root = os.path.abspath(WORKSPACE_DIR)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d not in ("venv", "env", "__pycache__", "node_modules", "dist", "build", ".git")]
        for fn in filenames:
            ext = os.path.splitext(fn)[1].lower()
            if ext in (
                ".py", ".json", ".yaml", ".yml", ".md", ".mdx", ".sh", ".bash", ".zsh",
                ".sql", ".toml", ".ini", ".cfg", ".ts", ".js", ".jsx", ".tsx", ".mjs", ".cjs",
                ".html", ".htm", ".xml", ".svg", ".css", ".scss", ".sass", ".less",
                ".csv", ".tsv", ".go", ".rs", ".java", ".c", ".cpp", ".cc", ".h", ".hpp", ".cs", ".php", ".vue", ".svelte"
            ):
                rel = os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")
                workspace_files.append(rel)

    if not workspace_files:
        return []

    # Single-file workspace needs no disambiguation
    if len(workspace_files) == 1:
        return workspace_files

    # 3. For multi-file projects, score candidates using prompt keywords to avoid context overflow
    prompt_tokens = set(w.lower().replace("-", "").replace("_", "") for w in words if len(w) > 2)
    scored = []
    for f in workspace_files:
        f_norm = f.lower().replace("-", "").replace("_", "")
        base_norm = os.path.basename(f).lower().replace("-", "").replace("_", "")
        score = sum(2 for t in prompt_tokens if t in base_norm) + sum(1 for t in prompt_tokens if t in f_norm)
        scored.append((score, f))

    scored.sort(key=lambda x: x[0], reverse=True)
    candidate_files = [f for _, f in scored[:30]]

    # 4. Query local Ollama (0 cloud tokens) to select the essential working set
    print(f"[ROUTER] Asking Ollama ({OLLAMA_MODEL}) to select relevant files from {len(candidate_files)} candidates...", flush=True)
    try:
        query_prompt = (
            "You are an embedded codebase dependency analyzer.\n"
            f"Candidate project files:\n{json.dumps(candidate_files)}\n\n"
            f"Task: '{prompt}'\n\n"
            "Return strictly a JSON array of the file paths directly implicated by this task. "
            "Order by relevance, starting with the primary target. Exclude peripheral or unaffected files.\n"
            "JSON:"
        )
        res = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": query_prompt,
                "format": "json",
                "stream": False
            },
            timeout=OLLAMA_TIMEOUT
        )
        if res.status_code == 200:
            ans = res.json().get("response", "").strip()
            import re
            m = re.search(r"\[.*?\]", ans, re.DOTALL)
            if m:
                chosen = json.loads(m.group(0))
                if isinstance(chosen, list):
                    filtered = [f for f in chosen if f in workspace_files]
                    if filtered:
                        print(f"[ROUTER] Ollama selected target files: {filtered}", flush=True)
                        return filtered
    except Exception as e:
        print(f"Notice: Ollama auto-discovery fallback: {e}", flush=True)

    # Fallback to top scored keyword matches
    fallback_matches = [f for s, f in scored[:3] if s > 0]
    print(f"[ROUTER] Keyword scoring selected files: {fallback_matches}", flush=True)
    return fallback_matches

def apply_guardrails(beautified_prompt: str, context_str: str) -> str:
    """Enforce architectural boundaries on agy to prevent multi-turn search loops and preserve token quota."""
    guardrails = (
        "## Operational Boundaries & Guardrails:\n"
        "- Scope: Modify strictly the files required to fulfill the specification.\n"
        "- Fast Convergence: Consolidate actions. Execute batch operations (e.g. batch deletions or multi-file edits) in a single turn. Complete execution in 2 turns maximum.\n"
        "- Token Economy: Leverage pre-read context; avoid redundant disk scans or serial tool iterations.\n"
        "- Output: Maintain minimal, clean diffs with high-fidelity verification.\n\n"
    )
    parts = [guardrails]
    if context_str:
        parts.append(context_str + "\n\n")
    parts.append(f"## Architectural Task Specification:\n{beautified_prompt}")
    return "".join(parts)

def execute_agy(prepared_prompt: str, agent_id: Optional[str] = None) -> Optional[str]:
    """Execute agy CLI non-interactively with active AGY_MODEL, optional persona agent, and workspace cwd."""
    agy_path = shutil.which("agy") or "/usr/local/bin/agy"
    if not os.path.exists(agy_path) and not shutil.which("agy"):
        print(f"agy executable not found at {agy_path}", flush=True)
        return None

    # Enforce workspace access, model selection, edit permissions, and skip interactive prompts
    cmd = [
        agy_path,
        "--model", AGY_MODEL,
        "--add-dir", WORKSPACE_DIR,
        "--mode", "accept-edits",
        "--dangerously-skip-permissions",
        "--print-timeout", f"{AGY_TIMEOUT}s"
    ]
    if agent_id:
        cmd.extend(["--agent", agent_id])
    cmd.extend(["-p", prepared_prompt])

    preview = (prepared_prompt[:80] + "...") if len(prepared_prompt) > 80 else prepared_prompt
    print(
        f"[ROUTER] Target: AGY | Persona: {agent_id or 'default'} | Model: {AGY_MODEL} | Prompt: {preview}",
        flush=True
    )

    env = os.environ.copy()
    # Strip general proxy variables so agy connects directly to Google Cloud endpoints
    for proxy_var in ["HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"]:
        env.pop(proxy_var, None)
    home_dir = os.getenv("HOME")
    if not home_dir or home_dir == "/root":
        home_dir = "/home/appuser" if os.path.exists("/home/appuser") else os.getenv("WORKSPACE_DIR", "/workspace")
    env["HOME"] = home_dir
    cwd = WORKSPACE_DIR if os.path.exists(WORKSPACE_DIR) else "/workspace"

    # Ensure workspace is registered in trustedWorkspaces
    try:
        cli_settings = os.path.join(home_dir, ".gemini", "antigravity-cli", "settings.json")
        if os.path.exists(cli_settings):
            with open(cli_settings, "r") as f:
                cfg = json.load(f)
            tw = cfg.setdefault("trustedWorkspaces", [])
            if cwd not in tw:
                tw.append(cwd)
                with open(cli_settings, "w") as f:
                    json.dump(cfg, f, indent=2)
    except Exception as e:
        print(f"Notice: could not verify trustedWorkspaces: {e}", flush=True)

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=AGY_TIMEOUT,
            env=env,
            cwd=cwd
        )
        output = proc.stdout.strip()
        stderr_lower = proc.stderr.lower() if proc.stderr else ""
        stdout_lower = output.lower()

        if proc.returncode != 0:
            print(f"agy exited with code {proc.returncode}: {proc.stderr}", flush=True)
            return None

        if "quota exceeded" in stdout_lower or "quota exceeded" in stderr_lower or "rate limit" in stdout_lower:
            print("agy reported quota or rate limit exceeded", flush=True)
            return None

        return output if output else "Task completed successfully."
    except subprocess.TimeoutExpired:
        print(f"agy execution timed out ({AGY_TIMEOUT}s)", flush=True)
        return None
    except Exception as e:
        print(f"agy subprocess execution error: {e}", flush=True)
        return None

@app.post("/v1/chat/completions")
def completions(req: ChatCompletionRequest):
    last_msg = next((m for m in reversed(req.messages) if m.role == "user"), None)
    if not last_msg:
        return package_response(req.model, "No prompt received.")

    prompt_text, parts, has_image = parse_parts(last_msg.content)

    # 1. Inspect Model Name or Prompt Prefixes for Persona Switching
    agent_id = None
    if req.model in ["coder", "reviewer", "architect"]:
        agent_id = req.model

    clean_prompt = prompt_text.strip()
    if clean_prompt.startswith("@reviewer") or clean_prompt.startswith("/reviewer"):
        agent_id = "reviewer"
        clean_prompt = clean_prompt.split(maxsplit=1)[1] if " " in clean_prompt else ""
    elif clean_prompt.startswith("@audit") or clean_prompt.startswith("/audit"):
        agent_id = "reviewer"
        clean_prompt = clean_prompt.split(maxsplit=1)[1] if " " in clean_prompt else ""
    elif clean_prompt.startswith("@architect") or clean_prompt.startswith("/architect"):
        agent_id = "architect"
        clean_prompt = clean_prompt.split(maxsplit=1)[1] if " " in clean_prompt else ""
    elif clean_prompt.startswith("@coder") or clean_prompt.startswith("/coder"):
        agent_id = "coder"
        clean_prompt = clean_prompt.split(maxsplit=1)[1] if " " in clean_prompt else ""
    elif clean_prompt.startswith("@dev") or clean_prompt.startswith("/dev"):
        agent_id = "coder"
        clean_prompt = clean_prompt.split(maxsplit=1)[1] if " " in clean_prompt else ""
    elif clean_prompt.startswith("@implement") or clean_prompt.startswith("/implement"):
        agent_id = "coder"
        clean_prompt = clean_prompt.split(maxsplit=1)[1] if " " in clean_prompt else ""

    # Direct Headroom Proxy Route
    if "headroom" in req.model.lower():
        try:
            headroom_req = req.model_dump()
            headroom_res = requests.post(f"{HEADROOM_PROXY}/v1/chat/completions", json=headroom_req, timeout=AGY_TIMEOUT)
            if headroom_res.status_code == 200:
                return headroom_res.json()
            return JSONResponse(status_code=headroom_res.status_code, content=headroom_res.json())
        except Exception as e:
            return package_response(req.model, f"*[Headroom Proxy Error]*\n\n{e}")

    if agent_id:
        # Pre-read referenced or auto-discovered workspace files and AST-compress them
        target_files = discover_relevant_files(clean_prompt or prompt_text)
        ws_context = read_target_files(target_files) if target_files else ""

        prepared = (ws_context + "\n\n" + clean_prompt) if ws_context else (clean_prompt or prompt_text)
        agy_output = execute_agy(prepared, agent_id=agent_id)
        if agy_output is not None:
            from ast_compressor import get_stats
            s = get_stats()
            saved_str = f" | AST Tokens Saved: {s.get('tokens_saved', 0)} ({s.get('savings_percentage', 0)}%)" if s.get('total_requests', 0) > 0 else ""
            badge = f"*[Agent Task: {agent_id.capitalize()} ({AGY_MODEL}){saved_str}]*\n\n"
            return package_response(req.model, badge + agy_output)

        # Automatic fallback to local Ollama on failure/timeout
        fallback_out = call_ollama_generation(clean_prompt or prompt_text)
        return package_response(req.model, "*[Fallback: Local Ollama]*\n\n" + fallback_out)

    # 2. Multimodal / Vision Bypass
    if has_image:
        if gemini_client and parts:
            target = "Gemini Vision"
            print(f"[ROUTER] Target: {target}", flush=True)
            try:
                res = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=parts)
                out = "*[Vision Routed: Gemini 2.5 Flash]*\n\n" + (res.text or "")
                return package_response(req.model, out)
            except Exception as e:
                print(f"Gemini vision request failed: {e}")
        # Fallback to local Ollama if no Gemini key or request failed
        target = f"Local Ollama (Vision Fallback: {OLLAMA_MODEL})"
        print(f"[ROUTER] Target: {target}", flush=True)
        gen_out = call_ollama_generation(f"[Image content attached] {prompt_text}")
        return package_response(req.model, "*[Fallback: Local Ollama]*\n\n" + gen_out)

    # 3. Local Prompt Beautification & Target Extraction (Ollama)
    structured_schema = {
        "type": "object",
        "properties": {
            "is_complex_agent": {
                "type": "boolean",
                "description": "True if prompt involves multi-file refactoring, broad codebase edits, or agent-level actions. False for standard single functions, questions, or algorithms."
            },
            "beautified_prompt": {
                "type": "string",
                "description": "Clean, concise, and structured architectural engineering specification converted from informal or messy user prompt."
            },
            "target_files": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Specific file paths in the workspace to inspect or modify."
            }
        },
        "required": ["is_complex_agent", "beautified_prompt"]
    }

    meta = {"is_complex_agent": False, "beautified_prompt": prompt_text, "target_files": []}
    try:
        ollama_res = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": (
                    "You are a technical requirements synthesizer. Convert this user request into a precise, structured architectural specification. "
                    "Eliminate conversational chatter, slang, and ambiguity. Identify whether it requires multi-step autonomous execution, "
                    f"and list any directly referenced target file paths:\n\n{prompt_text}"
                ),
                "format": structured_schema,
                "stream": False
            },
            timeout=OLLAMA_TIMEOUT
        )
        if ollama_res.status_code == 200:
            resp_json = ollama_res.json()
            raw_meta = json.loads(resp_json.get("response", "{}"))
            if isinstance(raw_meta, dict):
                meta.update(raw_meta)
    except Exception as e:
        print(f"Ollama beautification fallback: {e}")

    is_complex = meta.get("is_complex_agent", False)
    beautified = meta.get("beautified_prompt") or prompt_text
    target_files = meta.get("target_files", [])

    # 4. Complex Agent Pipeline: Pre-Reader -> Headroom Compress -> Guardrails -> agy Dispatch
    if is_complex:
        # Discover and pre-read matching files from workspace
        if not target_files:
            target_files = discover_relevant_files(beautified)
        context_str = read_target_files(target_files)
        # Apply anti-hallucination & quota safeguards
        guarded_prompt = apply_guardrails(beautified, context_str)

        agy_output = execute_agy(guarded_prompt)
        if agy_output is not None:
            from ast_compressor import get_stats
            s = get_stats()
            saved_str = f" | AST Tokens Saved: {s.get('tokens_saved', 0)} ({s.get('savings_percentage', 0)}%)" if s.get('total_requests', 0) > 0 else ""
            out = f"*[Agent Task: Antigravity ({AGY_MODEL}){saved_str}]*\n\n" + agy_output
            return package_response(req.model, out)

        # Automatic fallback: if agy exits non-zero or exceeds quota, fallback to Ollama
        target = f"Fallback Local Ollama ({OLLAMA_MODEL})"
        print(f"[ROUTER] Target: {target}", flush=True)
        fallback_out = call_ollama_generation(beautified)
        return package_response(req.model, "*[Fallback: Local Ollama]*\n\n" + fallback_out)

    # 5. Simple Task: Use Gemini 2.5 Flash if client available, otherwise route to local Ollama
    if gemini_client:
        target = "Gemini 2.5 Flash"
        print(f"[ROUTER] Target: {target}", flush=True)
        try:
            res = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=beautified
            )
            out = "*[Simple Task: Gemini 2.5 Flash]*\n\n" + (res.text or "")
            return package_response(req.model, out)
        except Exception as e:
            print(f"Gemini simple generation error: {e}")

    # Fallback / Default local Ollama
    target = f"Local Ollama ({OLLAMA_MODEL})"
    print(f"[ROUTER] Target: {target}", flush=True)
    ollama_out = call_ollama_generation(beautified)
    return package_response(req.model, "*[Simple Task: Local Ollama]*\n\n" + ollama_out)
