import os
import re
import time
import json
import base64
import shutil
import subprocess
import requests
import queue
import threading
from typing import List, Union, Dict, Any, Optional
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="Local AI Context-Router & Compression Stack")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
HEADROOM_PROXY = os.getenv("HEADROOM_PROXY", "http://headroom:8787").rstrip("/")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:1.5b").strip()
OLLAMA_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "600"))
AGY_MODEL = os.getenv("AGY_MODEL", "gemini-3.8-flash-medium").strip()
AGY_TIMEOUT = int(os.getenv("AGY_TIMEOUT", "600"))
WORKSPACE_DIR = os.getenv("WORKSPACE_DIR", "/workspace")

MAX_PRE_READ_SIZE = 50 * 1024  # 50 KB limit
IGNORED_EXTENSIONS = {
    ".log", ".lock", ".tmp", ".bin", ".tar", ".gz", ".zip", ".7z",
    ".pyc", ".pyo", ".pyd", ".db", ".sqlite", ".sqlite3", ".parquet",
    ".csv", ".tsv", ".jsonl", ".ndjson",
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
    """Ensure that the local workspace-tools MCP server and agent personas are registered for agy."""
    try:
        home_dir = os.getenv("HOME", "/home/appuser")
        mcp_script = "/app/src/mcp_workspace.py"
        if not os.path.exists(mcp_script):
            mcp_script = "/app/mcp_workspace.py"

        # Ensure symlink /app/mcp_workspace.py exists pointing to /app/src/mcp_workspace.py
        try:
            if os.path.exists("/app/src/mcp_workspace.py") and not os.path.exists("/app/mcp_workspace.py"):
                os.symlink("/app/src/mcp_workspace.py", "/app/mcp_workspace.py")
        except Exception:
            pass

        server_def = {
            "command": "python3",
            "args": [mcp_script],
            "disabled": False
        }

        # Multiple candidate config locations to ensure agy finds it regardless of version
        config_paths = [
            os.path.join(home_dir, ".gemini", "config", "mcp_config.json"),
            os.path.join(home_dir, ".gemini", "antigravity-cli", "mcp_config.json"),
            os.path.join(home_dir, ".gemini", "antigravity", "mcp_config.json"),
            os.path.join(WORKSPACE_DIR, ".agents", "mcp_config.json"),
            os.path.join(WORKSPACE_DIR, ".antigravity", "mcp_config.json")
        ]

        for config_path in config_paths:
            try:
                os.makedirs(os.path.dirname(config_path), exist_ok=True)
                data = {}
                if os.path.exists(config_path):
                    try:
                        with open(config_path, "r", encoding="utf-8") as f:
                            data = json.load(f)
                    except Exception:
                        data = {}
                servers = data.setdefault("mcpServers", {})
                servers["workspace_tools"] = server_def
                with open(config_path, "w", encoding="utf-8") as f:
                    json.dump(data, f, indent=2)
            except Exception:
                pass

        # Also register directly via agy CLI if available
        try:
            agy_bin = shutil.which("agy") or "/usr/local/bin/agy"
            if os.path.exists(agy_bin) and os.access(agy_bin, os.X_OK):
                subprocess.run(
                    [agy_bin, "mcp", "add", "workspace_tools", "python3", mcp_script],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=10,
                    check=False
                )
        except Exception:
            pass

        # Sync agent personas if available in /app/.antigravity or /workspace/.antigravity
        agents_src_dirs = [
            "/app/.antigravity/agents",
            "/app/agents",
            os.path.join(WORKSPACE_DIR, ".antigravity", "agents")
        ]
        agents_dest_dirs = [
            os.path.join(home_dir, ".gemini", "config", "agents"),
            os.path.join(home_dir, ".gemini", "antigravity", "agents"),
            os.path.join(home_dir, ".gemini", "antigravity-cli", "agents"),
            os.path.join(WORKSPACE_DIR, ".antigravity", "agents"),
            os.path.join(WORKSPACE_DIR, ".agents")
        ]
        for src in agents_src_dirs:
            if os.path.isdir(src):
                for dest in agents_dest_dirs:
                    try:
                        os.makedirs(dest, exist_ok=True)
                        for fname in os.listdir(src):
                            if fname.endswith(".md"):
                                shutil.copy2(os.path.join(src, fname), os.path.join(dest, fname))
                    except Exception:
                        pass
                break
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
                "message": {
                    "role": "assistant",
                    "content": content
                },
                "finish_reason": "stop"
            }
        ]
    }

def call_ollama_generation(prompt: str, is_code_task: bool = True) -> str:
    """Fallback or direct code generation using local Ollama model."""
    print(f"[ROUTER] Invoking Ollama fallback (model={OLLAMA_MODEL})", flush=True)
    try:
        formatted_prompt = prompt
        if is_code_task and "### [" not in prompt:
            directive = (
                "## CRITICAL SYSTEM DIRECTIVE:\n"
                "You are an autonomous code generator. All files must be persisted to the workspace.\n"
                "For EVERY file you implement, you MUST prefix its code block with an explicit markdown header indicating the exact relative file path, formatted as:\n"
                "### [path/to/filename.ext]\n"
                "```language\n"
                "<complete file content>\n"
                "```\n"
                "Provide complete, functional code without omissions or placeholders.\n\n"
            )
            formatted_prompt = directive + prompt

        payload = {
            "model": OLLAMA_MODEL,
            "prompt": formatted_prompt,
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
    raw_tokens = prompt.replace("`", " ").replace('"', ' ').replace("'", " ").replace(",", " ").split()
    valid_explicit = []
    for raw in raw_tokens:
        clean = raw.strip("[](){}<>,:;'\"`*").strip()
        if not clean or "." not in clean or clean.startswith("@") or clean.endswith("."):
            continue
        if os.path.isabs(clean) and clean.startswith(WORKSPACE_DIR):
            clean = os.path.relpath(clean, WORKSPACE_DIR).replace("\\", "/")
        full_p = os.path.join(WORKSPACE_DIR, clean)
        if os.path.isfile(full_p) and clean not in valid_explicit:
            valid_explicit.append(clean)

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
    words = re.findall(r'[A-Za-z0-9_]{3,}', prompt)
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
        "## Operational Boundaries & Guardrails (STRICT ZERO-CLOUD-TOKEN PROTOCOL):\n"
        "- MANDATORY LOCAL DELEGATION (CRITICAL): You are strictly FORBIDDEN from generating multi-line code, tests, or implementations in your cloud output, and FORBIDDEN from calling 'view_file' to browse the repository.\n"
        "- All relevant code is already provided below in 'Workspace Pre-Read Context'. Prefer 'trace_symbol' and 'ask_local_assistant' for call-graph inquiries.\n"
        "- To create or update files, you MUST invoke 'ask_local_assistant(query=\"...\", target_file=\"path/to/file.ext\")'. Local Ollama will generate and write the code directly to disk at ZERO cloud tokens.\n"
        "- Direct edits without Ollama are strictly prohibited unless it is a 1-2 line trivial fix via 'replace_file_content'.\n"
        "- FAST CONVERGENCE & Zero Search Loops: Complete your entire task in 1 single turn. Do NOT engage in multi-turn exploratory loops.\n"
        "- Absolute Test Prohibition: NEVER run test suites ('npm test', 'pytest', etc.). Instruct the user to execute tests locally under Next Steps.\n\n"
    )
    parts = [guardrails]
    if context_str:
        parts.append(context_str + "\n\n")
    parts.append(f"## Architectural Task Specification:\n{beautified_prompt}")
    return "".join(parts)

def auto_persist_code_blocks(output_text: str, workspace_dir: str, default_target_files: Optional[List[str]] = None) -> List[str]:
    """
    Safety net: Parses output text for code deliverables that were returned in markdown
    rather than executed via tools, and writes them directly to disk.
    """
    if not output_text or not os.path.exists(workspace_dir):
        return []

    import re
    from mcp_workspace import normalize_path

    persisted = []
    ws_root = Path(workspace_dir).resolve()

    BLACKLIST_NAMES = {
        "node.js", "vue.js", "next.js", "react.js", "express.js",
        "nest.js", "deno.js", "nuxt.js", "electron.js", "angular.js"
    }
    VALID_EXTS = {
        ".js", ".jsx", ".ts", ".tsx", ".py", ".json", ".yml", ".yaml",
        ".md", ".sh", ".bash", ".sql", ".html", ".css", ".env", ".example",
        ".txt", ".cfg", ".ini", ".toml", ".xml", ".dockerfile"
    }

    # Match each markdown code block
    code_block_re = re.compile(r"```([a-zA-Z0-9_\-\./]*)\n(.*?)```", re.DOTALL)
    file_candidate_re = re.compile(
        r"(?:`|\[)?([a-zA-Z0-9_\-\./\\]+\.[a-zA-Z0-9_\-]+)(?:`|\])?",
        re.MULTILINE
    )

    assigned_targets = list(default_target_files) if default_target_files else []
    target_idx = 0

    for match in code_block_re.finditer(output_text):
        lang = match.group(1).strip()
        code = match.group(2)
        start_idx = match.start()

        # Skip empty or tiny blocks
        if not code.strip() or len(code.strip()) < 10:
            continue

        # Look at the text preceding this code block (up to 400 chars)
        preceding_text = output_text[max(0, start_idx - 400):start_idx]
        preceding_lines = [l.strip() for l in preceding_text.splitlines() if l.strip()]

        detected_filename = None

        # Check preceding lines in reverse order (closest lines first)
        for line in reversed(preceding_lines[-5:]):
            # Skip markdown table rows
            if line.startswith("|") and line.endswith("|"):
                continue
            
            cands = file_candidate_re.findall(line)
            valid_cands = []
            for c in cands:
                clean_c = c.strip("`'\"[]()*:").replace("file://", "").strip()
                if clean_c.startswith("a/") or clean_c.startswith("b/") or clean_c.startswith("a\\") or clean_c.startswith("b\\"):
                    continue
                base_c = os.path.basename(clean_c).lower()
                ext = os.path.splitext(base_c)[1].lower()
                if base_c not in BLACKLIST_NAMES and (ext in VALID_EXTS or "env" in base_c):
                    valid_cands.append(clean_c)
            
            if valid_cands:
                detected_filename = valid_cands[-1]
                break

        # Check first line of code block for filename comment
        if not detected_filename:
            first_line = code.strip().splitlines()[0] if code.strip() else ""
            if first_line.startswith(("#", "//", "/*", "<!--", "--")):
                comment_cands = file_candidate_re.findall(first_line)
                for c in comment_cands:
                    clean_c = c.strip("`'\"[]()*:").replace("file://", "").strip()
                    base_c = os.path.basename(clean_c).lower()
                    ext = os.path.splitext(base_c)[1].lower()
                    if base_c not in BLACKLIST_NAMES and (ext in VALID_EXTS or "env" in base_c):
                        detected_filename = clean_c
                        break

        # Fallback to next known target file
        if not detected_filename and target_idx < len(assigned_targets):
            detected_filename = assigned_targets[target_idx]
            target_idx += 1

        # Fallback to language extension
        if not detected_filename:
            if len(code.strip()) > 40 and lang.lower() not in ("bash", "sh", "shell", "console", "cmd", "powershell"):
                ext_map = {
                    "python": ".py", "py": ".py", "javascript": ".js", "js": ".js",
                    "typescript": ".ts", "ts": ".ts", "java": ".java", "sql": ".sql",
                    "json": ".json", "yaml": ".yml", "yml": ".yml", "html": ".html", "css": ".css"
                }
                ext = ext_map.get(lang.lower(), ".py" if "import " in code or "def " in code else None)
                if ext:
                    detected_filename = f"generated_module{ext}"

        if not detected_filename:
            continue

        resolved = normalize_path(detected_filename)
        already_exists = resolved.exists()

        if resolved.name == "package.json" and already_exists:
            try:
                existing_pkg = json.loads(resolved.read_text(encoding="utf-8"))
                new_pkg = json.loads(code)
                if "scripts" in new_pkg and isinstance(new_pkg["scripts"], dict):
                    existing_scripts = existing_pkg.setdefault("scripts", {})
                    for k, v in new_pkg["scripts"].items():
                        existing_scripts[k] = v
                    resolved.write_text(json.dumps(existing_pkg, indent=2), encoding="utf-8")
                    persisted.append(f"{resolved.name} (scripts merged)")
                    continue
            except Exception as e:
                print(f"[ROUTER] Notice merging package.json: {e}", flush=True)

        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(code, encoding="utf-8")
            rel_name = str(resolved.relative_to(ws_root))
            if rel_name not in persisted:
                persisted.append(rel_name)
            print(f"[ROUTER] Auto-persisted deliverable to disk: {resolved}", flush=True)
        except Exception as e:
            print(f"[ROUTER] Failed to auto-persist {resolved}: {e}", flush=True)

    if persisted:
        print(f"[ROUTER] Successfully auto-persisted {len(persisted)} deliverables: {', '.join(persisted)}", flush=True)

    return persisted

def execute_agy(prepared_prompt: str, agent_id: Optional[str] = None) -> Optional[str]:
    """Execute agy CLI non-interactively with active AGY_MODEL, optional persona agent, and workspace cwd."""
    agy_path = shutil.which("agy") or "/usr/local/bin/agy"
    if not os.path.exists(agy_path) or os.path.isdir(agy_path) or not os.access(agy_path, os.X_OK):
        print(f"agy executable not found or not executable at {agy_path}", flush=True)
        return None

    # Enforce workspace access, model selection, edit permissions, and skip interactive prompts
    cmd = [
        agy_path,
        "--model", AGY_MODEL,
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
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=AGY_TIMEOUT + 15,
            env=env,
            cwd=cwd
        )
        output = proc.stdout.strip()
        stderr_lower = proc.stderr.lower() if proc.stderr else ""

        if proc.returncode != 0:
            err_msg = (proc.stderr or "").strip() or (proc.stdout or "").strip()
            err_lower = err_msg.lower()
            if any(term in err_lower for term in ["quota exceeded", "rate limit", "resource_exhausted", "too many requests", "429"]):
                print(f"[ROUTER] agy reported quota/rate limit exceeded: {err_msg}", flush=True)
            else:
                print(f"[ROUTER] agy exited with code {proc.returncode}: {err_msg}", flush=True)
            return None

        # When proc.returncode == 0, output in stdout is the actual model completion.
        # Check ONLY stderr for actual system/API warnings or quota errors, not stdout,
        # to avoid false positives when the generated code/text discusses rate limits.
        if "quota exceeded" in stderr_lower or "resource_exhausted" in stderr_lower or "rate limit exceeded" in stderr_lower:
            print(f"[ROUTER] agy stderr reported quota/rate limit error: {proc.stderr.strip()}", flush=True)
            return None

        if output and agent_id == "coder":
            try:
                auto_persist_code_blocks(output, cwd)
            except Exception as e:
                print(f"[ROUTER] Notice in auto_persist_code_blocks: {e}", flush=True)

        return output if output else "Task completed successfully."
    except subprocess.TimeoutExpired:
        print(f"agy execution timed out ({AGY_TIMEOUT}s)", flush=True)
        return None
    except Exception as e:
        print(f"agy subprocess execution error: {e}", flush=True)
        return None

def process_chat_request(req: ChatCompletionRequest) -> str:
    last_msg = next((m for m in reversed(req.messages) if m.role == "user"), None)
    if not last_msg:
        return "No prompt received."

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
                resp_json = headroom_res.json()
                if isinstance(resp_json, dict) and "choices" in resp_json and len(resp_json["choices"]) > 0:
                    return resp_json["choices"][0].get("message", {}).get("content", "")
                return json.dumps(resp_json)
            return f"*[Headroom Proxy Error: HTTP {headroom_res.status_code}]*"
        except Exception as e:
            return f"*[Headroom Proxy Error]*\n\n{e}"

    # 2. Multimodal / Vision Bypass
    if has_image:
        if gemini_client and parts:
            target = "Gemini Vision"
            print(f"[ROUTER] Target: {target}", flush=True)
            try:
                res = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=parts)
                return "*[Vision Routed: Gemini 2.5 Flash]*\n\n" + (res.text or "")
            except Exception as e:
                print(f"Gemini vision request failed: {e}")
        # Fallback to local Ollama if no Gemini key or request failed
        target = f"Local Ollama (Vision Fallback: {OLLAMA_MODEL})"
        print(f"[ROUTER] Target: {target}", flush=True)
        gen_out = call_ollama_generation(f"[Image content attached] {prompt_text}")
        return "*[Fallback: Local Ollama]*\n\n" + gen_out

    # 3. Unified Inbound Pipeline: Ollama Context Synthesizer (0 Cloud Tokens)
    # Extracts the essential technical brief and target files so agy is only invoked with necessary info
    synthesized_prompt = clean_prompt or prompt_text
    target_files = []
    try:
        structured_schema = {
            "type": "object",
            "properties": {
                "beautified_prompt": {
                    "type": "string",
                    "description": "Clean, concise, and structured technical specification converted from user prompt, with all conversational noise stripped."
                },
                "target_files": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Specific file paths in the workspace directly implicated by the task."
                }
            },
            "required": ["beautified_prompt"]
        }
        ollama_res = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": (
                    "You are a technical requirements synthesizer. Convert this user request into a precise, structured technical specification. "
                    "Eliminate conversational chatter, pleasantries, and ambiguity. Extract any directly referenced or target file paths:\n\n"
                    f"{clean_prompt or prompt_text}"
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
                synthesized_prompt = raw_meta.get("beautified_prompt") or synthesized_prompt
                target_files = raw_meta.get("target_files", [])
                print(f"[ROUTER] Ollama synthesized specification ({len(synthesized_prompt)} chars), target files: {target_files}", flush=True)
    except Exception as e:
        print(f"[ROUTER] Ollama synthesis notice: {e}", flush=True)

    # 4. AST Workspace Pre-Reader (Compresses workspace files at 0 cloud tokens)
    real_targets = []
    ws_root = Path(WORKSPACE_DIR).resolve()
    for tf in target_files:
        clean_tf = tf.strip().lstrip("/")
        full_p = (ws_root / clean_tf).resolve()
        if full_p.exists() and full_p.is_file():
            real_targets.append(clean_tf)

    if not real_targets:
        real_targets = discover_relevant_files(synthesized_prompt)

    print(f"[ROUTER] Validated target files for AST pre-read: {real_targets}", flush=True)
    ws_context = read_target_files(real_targets) if real_targets else ""

    # 5. Dispatch to agy (Gemini Orchestrator) with Division of Labor Guardrails
    guarded_prompt = apply_guardrails(synthesized_prompt, ws_context)
    selected_agent = agent_id or "coder"
    agy_output = execute_agy(guarded_prompt, agent_id=selected_agent)
    if agy_output is not None:
        from ast_compressor import get_stats
        s = get_stats()
        saved_str = f" | AST Tokens Saved: {s.get('tokens_saved', 0)} ({s.get('savings_percentage', 0)}%)" if s.get('total_requests', 0) > 0 else ""
        badge = f"*[Agent Task: {selected_agent.capitalize()} ({AGY_MODEL}){saved_str}]*\n\n"
        return badge + agy_output

    # 6. Automatic Fallback to Local Ollama with Direct Workspace Disk Persistence
    print(f"[ROUTER] agy failed or quota exhausted; invoking Ollama fallback (model={OLLAMA_MODEL})", flush=True)
    fallback_out = call_ollama_generation(synthesized_prompt)
    saved_files = []
    if fallback_out:
        try:
            saved_files = auto_persist_code_blocks(fallback_out, WORKSPACE_DIR, default_target_files=target_files)
        except Exception as e:
            print(f"[ROUTER] Notice in auto_persist_code_blocks (fallback): {e}", flush=True)
    saved_badge = f"\n\n*[Files saved to workspace: {', '.join(saved_files)}]*" if saved_files else ""
    return "*[Fallback: Local Ollama]*" + saved_badge + "\n\n" + fallback_out

def generate_stream_response(req: ChatCompletionRequest):
    """Format response as live SSE stream with periodic keep-alive pings to prevent client timeout."""
    def event_stream():
        chunk_id = f"chatcmpl-{int(time.time())}"
        created_ts = int(time.time())

        # 1. Immediately yield initial chunk with role so client detects stream start
        role_chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": req.model,
            "choices": [{
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None
            }]
        }
        yield f"data: {json.dumps(role_chunk)}\n\n"

        # 2. Worker thread runs pipeline asynchronously
        res_q = queue.Queue()

        def worker():
            try:
                out = process_chat_request(req)
                res_q.put(("ok", out))
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
                # SSE comment keeps TCP connection alive through client/proxy timeouts
                yield ": keep-alive\n\n"

        if content is None:
            try:
                status, payload = res_q.get(timeout=2.0)
                content = payload if status == "ok" else f"*[Pipeline Error]*\n\n{payload}"
            except queue.Empty:
                content = "*[Router Notice: Processing finished with no output]*"

        # 3. Stream content progressively in lines
        lines = content.splitlines(keepends=True)
        if not lines:
            lines = [content]

        for line in lines:
            c_chunk = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created_ts,
                "model": req.model,
                "choices": [{
                    "index": 0,
                    "delta": {"content": line},
                    "finish_reason": None
                }]
            }
            yield f"data: {json.dumps(c_chunk)}\n\n"

        # 4. Stop chunk
        stop_chunk = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created_ts,
            "model": req.model,
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }]
        }
        yield f"data: {json.dumps(stop_chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )

@app.post("/v1/chat/completions")
def completions(req: ChatCompletionRequest):
    if req.stream:
        return generate_stream_response(req)
    content = process_chat_request(req)
    return package_response(req.model, content)
