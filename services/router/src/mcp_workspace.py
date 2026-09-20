#!/usr/bin/env python3
"""
Lightweight zero-dependency Model Context Protocol (MCP) server
exposing workspace file-write and execution tools to Antigravity (agy).
"""

import sys
import json
import os
import re
import time
import subprocess
from pathlib import Path

WORKSPACE_ROOT = os.getenv("WORKSPACE_DIR", "/workspace")

# Safe file-based logger to prevent stdio transport corruption in agy
MCP_LOG_FILE = Path("/tmp/mcp_workspace.log")


def log_mcp(msg: str):
    try:
        with open(MCP_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass


# Tool-level session state for circuit breakers
_local_assistant_status = {
    "failures": 0,
    "last_error": None
}


def normalize_path(target_path: str) -> Path:
    p = Path(target_path)
    if not p.is_absolute():
        p = Path(WORKSPACE_ROOT) / p
    resolved = p.resolve()
    try:
        ws_root = Path(WORKSPACE_ROOT).resolve()
        if resolved.is_relative_to(ws_root):
            rel = resolved.relative_to(ws_root)
            parts = rel.parts
            if len(parts) > 1:
                sub_dir = ws_root / parts[0]
                if not sub_dir.exists():
                    ws_env_name = Path(os.getenv("WORKSPACE_PATH", "")).name if os.getenv("WORKSPACE_PATH") else ""
                    if parts[0] in (ws_root.name, ws_env_name):
                        return (ws_root / Path(*parts[1:])).resolve()
    except Exception:
        pass
    return resolved


def handle_replace_file_content(args: dict) -> dict:
    target_path = args.get("path") or args.get("TargetFile") or args.get("target_file")
    target_content = args.get("TargetContent") or args.get("target_content") or args.get("old_content")
    replacement_content = args.get("ReplacementContent") or args.get("replacement_content") or args.get("new_content") or ""

    if not target_path or target_content is None:
        return {"content": [{"type": "text", "text": "Error: missing 'path' or 'TargetContent'"}], "isError": True}

    resolved = normalize_path(target_path)
    if not resolved.exists():
        return {"content": [{"type": "text", "text": f"Error: file not found at {resolved}"}], "isError": True}

    file_text = resolved.read_text(encoding="utf-8")
    if target_content not in file_text:
        return {"content": [{"type": "text", "text": f"Error: TargetContent not found in {resolved.name}"}], "isError": True}

    new_text = file_text.replace(target_content, replacement_content, 1)
    resolved.write_text(new_text, encoding="utf-8")
    return {"content": [{"type": "text", "text": f"Successfully updated {resolved.name}"}]}


def handle_write_to_file(args: dict) -> dict:
    target_path = args.get("path") or args.get("TargetFile") or args.get("target_file")
    content = args.get("content") or args.get("CodeContent") or args.get("code_content") or ""

    if not target_path:
        return {"content": [{"type": "text", "text": "Error: missing required 'path' parameter"}], "isError": True}

    resolved = normalize_path(target_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")

    return {"content": [{"type": "text", "text": f"Successfully created and wrote {len(content)} bytes to {resolved}"}]}


def handle_delete_file(args: dict) -> dict:
    raw_paths = args.get("paths") or args.get("path") or args.get("TargetFile") or args.get("target_file")
    if not raw_paths:
        return {"content": [{"type": "text", "text": "Error: missing required 'path' or 'paths' parameter"}], "isError": True}

    if isinstance(raw_paths, str):
        items = [p.strip() for p in raw_paths.replace(",", " ").split() if p.strip()]
    elif isinstance(raw_paths, list):
        items = [str(p).strip() for p in raw_paths if p]
    else:
        items = [str(raw_paths).strip()]

    workspace_root = Path(WORKSPACE_ROOT).resolve()
    deleted, errors = [], []

    for item in items:
        try:
            resolved = normalize_path(item)
            if not resolved.is_relative_to(workspace_root):
                errors.append(f"Security Error: {item} is outside workspace")
                continue
            if not resolved.exists():
                continue
            if resolved.is_file() or resolved.is_symlink():
                resolved.unlink()
                deleted.append(resolved.name)
            elif resolved.is_dir():
                import shutil
                shutil.rmtree(resolved)
                deleted.append(f"{resolved.name}/")
        except Exception as e:
            errors.append(f"Failed to delete {item}: {e}")

    msg_parts = []
    if deleted:
        msg_parts.append(f"Successfully deleted: {', '.join(deleted)}")
    if errors:
        msg_parts.append(f"Errors: {'; '.join(errors)}")
    if not msg_parts:
        msg_parts.append("No files required deletion (already absent).")

    return {"content": [{"type": "text", "text": "\n".join(msg_parts)}]}


def handle_run_command(args: dict) -> dict:
    cmd_str = args.get("command") or args.get("CommandLine") or args.get("cmd") or ""
    cwd = args.get("cwd") or WORKSPACE_ROOT
    if not cmd_str:
        return {"content": [{"type": "text", "text": "Error: missing required 'command' parameter"}], "isError": True}

    lowered_cmd = cmd_str.strip().lower()

    # Strict token preservation: block test runners
    test_block_pattern = (
        r"\b(mvn|\./mvnw|mvnw|gradle|\./gradlew|gradlew)\b.*?\b(test|verify|check|--tests|-dtest|surefire|failsafe)\b|"
        r"\b(junit|testng)\b|"
        r"\b(npm|yarn|pnpm|bun)\b.*?\b(test|run\s+test|t)\b|"
        r"\b(jest|vitest|mocha|jasmine|karma|cypress|playwright|ava)\b|"
        r"\bnpx\s+(jest|vitest|mocha|cypress|playwright)\b|"
        r"\b(pytest|unittest)\b|"
        r"\bpython\d*\b.*?\b-m\s+(unittest|pytest)\b|"
        r"\b(go|cargo|dotnet)\b\s+test\b"
    )
    if re.search(test_block_pattern, lowered_cmd):
        return {
            "content": [{
                "type": "text",
                "text": f"Execution Blocked: Executing tests ('{cmd_str}') is strictly prohibited to preserve token quota. Instruct user to run tests locally under Next Steps."
            }],
            "isError": True
        }

    # Strict sandboxing: block exploratory system searches outside /workspace
    traversal_pattern = (
        r"\b(find|grep|cat|ls)\s+.*?(?:/|/home|/root|/app|~|\.\.)|"
        r"\b(history|\.bash_history)\b|"
        r"(?:/home/appuser/\.gemini|/\.gemini|\bbrain\b)"
    )
    if re.search(traversal_pattern, lowered_cmd):
        return {
            "content": [{
                "type": "text",
                "text": f"Execution Blocked: Exploratory system search ('{cmd_str}') outside /workspace is strictly prohibited."
            }],
            "isError": True
        }

    # Circuit breaker: Block shell-based file write attempts to evade guardrails after failure
    if _local_assistant_status.get("failures", 0) > 0:
        if any(w in lowered_cmd for w in ["cat <<", "echo ", "tee ", "printf "]) and any(w in lowered_cmd for w in [">", ">>"]):
            return {
                "content": [{
                    "type": "text",
                    "text": "Execution Blocked: Shell file-write workarounds are blocked because the local assistant circuit breaker is active."
                }],
                "isError": True
            }

    env = os.environ.copy()
    env["CI"] = "true"
    env["DEBIAN_FRONTEND"] = "noninteractive"
    env["PAGER"] = "cat"
    safe_cwd = WORKSPACE_ROOT if not cwd or not str(cwd).startswith(WORKSPACE_ROOT) else cwd

    try:
        proc = subprocess.run(
            cmd_str, shell=True, executable="/bin/bash", stdin=subprocess.DEVNULL,
            capture_output=True, text=True, cwd=safe_cwd, env=env, timeout=30
        )
        output = proc.stdout or ""
        if proc.stderr:
            output += f"\n[stderr]\n{proc.stderr}"
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"

        MAX_OUTPUT = 64 * 1024
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + "\n... [output truncated]"

        return {"content": [{"type": "text", "text": output or "(command finished with no output)"}]}
    except subprocess.TimeoutExpired:
        return {"content": [{"type": "text", "text": "Execution timed out (30s limit)."}], "isError": True}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Execution error: {e}"}], "isError": True}


def retrieve_local_workspace_context(query: str, max_chars: int = 4000) -> str:
    """Fast zero-cost local code retrieval from /workspace for keywords mentioned in query."""
    root = Path(WORKSPACE_ROOT)
    if not root.exists():
        return ""

    words = re.findall(r'[A-Za-z0-9_]{4,}', query)
    stop_words = {
        "what", "where", "when", "which", "with", "from", "that", "this", "have", "test",
        "case", "code", "file", "mock", "should", "using", "class", "method", "into",
        "true", "false", "please", "write", "create", "implement", "verify", "check"
    }
    candidates = [w for w in words if w.lower() not in stop_words]
    if not candidates:
        return ""

    SKIP_DIRS = {".git", "node_modules", "target", "build", ".gradle", "venv", "env", "__pycache__", "dist"}
    snippets, total_len = [], 0

    for p in root.rglob("*"):
        if p.is_dir() or any(part in SKIP_DIRS or part.startswith(".") for part in p.parts):
            continue
        if p.suffix.lower() not in (".java", ".py", ".ts", ".js", ".json", ".yml", ".yaml"):
            continue

        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        matched = [f"{idx+1}: {line.strip()}" for idx, line in enumerate(lines) if any(c in line for c in candidates)]
        if matched:
            block = f"--- File: {p.relative_to(root)} ---\n" + "\n".join(matched[:8]) + "\n"
            if total_len + len(block) > max_chars:
                break
            snippets.append(block)
            total_len += len(block)

    return ("## Retrieved Workspace Code Snippets:\n" + "\n".join(snippets)) if snippets else ""


def handle_ask_local_assistant(args: dict) -> dict:
    query = args.get("query") or args.get("prompt") or args.get("question") or ""
    target_file = args.get("target_file") or args.get("path") or args.get("file_path") or args.get("TargetFile")
    context = args.get("context") or args.get("code") or ""
    if not query:
        return {"content": [{"type": "text", "text": "Error: missing required 'query' parameter"}], "isError": True}

    if _local_assistant_status.get("failures", 0) > 0:
        return {
            "content": [{
                "type": "text",
                "text": f"CIRCUIT BREAKER ACTIVE: Local assistant already failed ({_local_assistant_status.get('last_error')}). Conclude response now."
            }],
            "isError": True
        }

    if not context:
        auto_snippets = retrieve_local_workspace_context(query)
        if auto_snippets:
            context = auto_snippets[:1500]

    ollama_url = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
    ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:1.5b")
    ollama_timeout = int(os.getenv("OLLAMA_TIMEOUT", "180"))

    system_prompt = (
        "You are an expert autonomous software engineer. "
        "Generate complete, production-grade, functional code without omissions or placeholders.\n"
    )
    if target_file:
        system_prompt += f"Implementing exact file: {target_file}. Return strictly the code for this file."

    full_prompt = f"{system_prompt}\n\nContext:\n{context}\n\nQuery:\n{query}" if context else f"{system_prompt}\n\nQuery:\n{query}"

    import urllib.request
    req_body = json.dumps({
        "model": ollama_model,
        "prompt": full_prompt,
        "stream": True,
        "keep_alive": "24h",
        "options": {"num_predict": 768, "num_ctx": 2048, "temperature": 0.2}
    }).encode("utf-8")

    req = urllib.request.Request(f"{ollama_url}/api/generate", data=req_body, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req, timeout=ollama_timeout) as resp:
            parts = []
            for line in resp:
                if not line:
                    continue
                try:
                    chunk = json.loads(line.decode("utf-8"))
                    delta = chunk.get("response", "")
                    if delta:
                        parts.append(delta)
                    if chunk.get("done", False):
                        break
                except Exception:
                    continue

            answer = "".join(parts).strip()

            if target_file:
                code_to_write = answer
                m = re.search(r"```[a-zA-Z0-9_\-\./]*\n(.*?)```", answer, re.DOTALL)
                if m:
                    code_to_write = m.group(1).strip()
                else:
                    if code_to_write.startswith("```"):
                        code_to_write = re.sub(r"^```[a-zA-Z0-9_\-\./]*\n", "", code_to_write)
                    if code_to_write.endswith("```"):
                        code_to_write = code_to_write[:-3].rstrip()

                resolved = normalize_path(target_file)
                resolved.parent.mkdir(parents=True, exist_ok=True)
                resolved.write_text(code_to_write, encoding="utf-8")
                line_count = len(code_to_write.splitlines())
                log_mcp(f"Ollama generated and wrote {resolved} ({len(code_to_write)} bytes, {line_count} lines)")
                return {
                    "content": [{
                        "type": "text",
                        "text": f"[Local Ollama (0 Cloud Tokens)]: Successfully generated and saved '{target_file}' ({line_count} lines, {len(code_to_write)} bytes) directly to disk in /workspace."
                    }]
                }

            return {"content": [{"type": "text", "text": f"[Local Ollama ({ollama_model} | 0 Cloud Tokens)]:\n{answer}"}]}
    except Exception as e:
        _local_assistant_status["failures"] += 1
        _local_assistant_status["last_error"] = str(e)
        return {
            "content": [{
                "type": "text",
                "text": f"CIRCUIT BREAKER: Local assistant failed ({e}). Conclude your task immediately and report under Next Steps."
            }],
            "isError": True
        }


def handle_trace_symbol(args: dict) -> dict:
    symbol = (args.get("symbol") or args.get("name") or "").strip()
    if not symbol:
        return {"content": [{"type": "text", "text": "Error: missing required 'symbol' parameter"}], "isError": True}

    root = Path(WORKSPACE_ROOT)
    definitions, usages = [], []
    SKIP_DIRS = {".git", "node_modules", "target", "build", "venv", "env", "__pycache__", "dist"}

    for p in root.rglob("*"):
        if p.is_dir() or any(part in SKIP_DIRS or part.startswith(".") for part in p.parts):
            continue
        ext = p.suffix.lower()
        if ext not in (".java", ".py", ".ts", ".js", ".tsx", ".jsx"):
            continue

        try:
            rel_p = str(p.relative_to(root)).replace("\\", "/")
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception:
            continue

        # Scan for usages
        for idx, line in enumerate(lines, 1):
            if symbol in line:
                s_line = line.strip()
                if not any(s_line.startswith(d) for d in ("class ", "interface ", "def ", "public class ", "export class ")):
                    usages.append(f"{rel_p}:{idx}: {s_line[:90]}")

        # Scan for declarations
        if ext == ".py":
            import ast
            try:
                tree = ast.parse("\n".join(lines), filename=str(p))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
                        definitions.append(f"Python def {symbol}() -> {rel_p}:{node.lineno}")
                    elif isinstance(node, ast.ClassDef) and node.name == symbol:
                        definitions.append(f"Python class {symbol} -> {rel_p}:{node.lineno}")
            except Exception:
                pass
        elif ext == ".java":
            pattern = re.compile(r'\b(class|interface|record|enum|void|[\w<>\[\]]+)\s+(' + re.escape(symbol) + r')\b')
            for idx, line in enumerate(lines, 1):
                if pattern.search(line):
                    definitions.append(f"Java {symbol} -> {rel_p}:{idx}: {line.strip()[:80]}")
        elif ext in (".ts", ".js", ".tsx", ".jsx"):
            pattern = re.compile(r'\b(class|interface|type|function|const|let)\s+(' + re.escape(symbol) + r')\b')
            for idx, line in enumerate(lines, 1):
                if pattern.search(line):
                    definitions.append(f"{ext[1:].upper()} {symbol} -> {rel_p}:{idx}: {line.strip()[:80]}")

    out_lines = [f"=== Symbol Trace: '{symbol}' ==="]
    out_lines.append("## Definitions & Signatures:" if definitions else "## Definitions: (None found)")
    out_lines.extend(f"- {d}" for d in definitions[:15])
    out_lines.append(f"## Usages ({len(usages)} total, showing top 10):" if usages else "## Usages: (None found)")
    out_lines.extend(f"- {u}" for u in usages[:10])

    return {"content": [{"type": "text", "text": "\n".join(out_lines)}]}


TOOLS = [
    {
        "name": "write_to_file",
        "description": "Create or write content to a file in the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path in workspace"},
                "content": {"type": "string", "description": "Exact content to write"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "write_file",
        "description": "Alias for write_to_file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to write"},
                "content": {"type": "string", "description": "The content to write"}
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "replace_file_content",
        "description": "Replace a target text chunk in an existing workspace file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path to modify"},
                "target_content": {"type": "string", "description": "Existing text to replace"},
                "replacement_content": {"type": "string", "description": "New replacement text"}
            },
            "required": ["path", "target_content", "replacement_content"]
        }
    },
    {
        "name": "run_command",
        "description": "Execute a shell command inside the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to execute"}
            },
            "required": ["command"]
        }
    },
    {
        "name": "ask_local_assistant",
        "description": "Generate code or seed data using local zero-cost Ollama. If target_file is provided, writes output to disk at 0 cloud tokens.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Specification or prompt for local Ollama"},
                "target_file": {"type": "string", "description": "Optional file path in /workspace where code should be saved directly"},
                "context": {"type": "string", "description": "Optional reference code or schemas"}
            },
            "required": ["query"]
        }
    },
    {
        "name": "trace_symbol",
        "description": "Trace symbol definitions and usages across workspace files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {"type": "string", "description": "Class, interface, function, or method name to trace"}
            },
            "required": ["symbol"]
        }
    },
    {
        "name": "delete_file",
        "description": "Delete obsolete files or directories in the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {"description": "File path or list of file paths to delete"}
            }
        }
    }
]


def process_message(msg: dict) -> dict:
    req_id = msg.get("id")
    method = msg.get("method")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "workspace-tools", "version": "1.0.0"}
            }
        }

    if method == "notifications/initialized":
        return None

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        if isinstance(tool_args, str):
            try:
                tool_args = json.loads(tool_args)
            except Exception:
                tool_args = {}
        elif not isinstance(tool_args, dict):
            tool_args = {}

        try:
            if tool_name in ("write_to_file", "write_file"):
                res = handle_write_to_file(tool_args)
            elif tool_name in ("replace_file_content", "replace_content"):
                res = handle_replace_file_content(tool_args)
            elif tool_name == "run_command":
                res = handle_run_command(tool_args)
            elif tool_name == "ask_local_assistant":
                res = handle_ask_local_assistant(tool_args)
            elif tool_name == "trace_symbol":
                res = handle_trace_symbol(tool_args)
            elif tool_name in ("delete_file", "remove_file"):
                res = handle_delete_file(tool_args)
            else:
                res = {"content": [{"type": "text", "text": f"Unknown tool: {tool_name}"}], "isError": True}
        except Exception as e:
            res = {"content": [{"type": "text", "text": f"Tool execution error: {e}"}], "isError": True}

        return {"jsonrpc": "2.0", "id": req_id, "result": res}

    if req_id is not None:
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}
    return None


def main():
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = process_message(req)
            if resp is not None:
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        except Exception as e:
            log_mcp(f"MCP loop error: {e}")
            try:
                err_resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": f"Parse error: {e}"}}
                sys.stdout.write(json.dumps(err_resp) + "\n")
                sys.stdout.flush()
            except Exception:
                pass


if __name__ == "__main__":
    main()
