#!/usr/bin/env python3
"""
Lightweight zero-dependency Model Context Protocol (MCP) server
exposing workspace file-write and execution tools to Antigravity (agy).
"""

import sys
import json
import os
import re
import subprocess
from pathlib import Path

WORKSPACE_ROOT = os.getenv("WORKSPACE_DIR", "/workspace")

def normalize_path(target_path: str) -> Path:
    p = Path(target_path)
    if not p.is_absolute():
        p = Path(WORKSPACE_ROOT) / p
    return p.resolve()

def handle_write_to_file(args: dict) -> dict:
    target_path = args.get("path") or args.get("TargetFile") or args.get("target_file")
    content = args.get("content") or args.get("CodeContent") or args.get("code_content") or ""
    
    if not target_path:
        return {"content": [{"type": "text", "text": "Error: missing required 'path' parameter"}], "isError": True}
    
    resolved = normalize_path(target_path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(content, encoding="utf-8")
    
    return {
        "content": [{
            "type": "text",
            "text": f"Successfully created and wrote {len(content)} bytes to {resolved}"
        }]
    }

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
    deleted = []
    errors = []

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

    # Strict token preservation: block all test runner commands (including single test methods)
    lowered_cmd = cmd_str.strip().lower()
    test_block_patterns = [
        r"\bmvn\b.*?\b(test|verify|-dtest)\b",
        r"\bgradlew?\b.*?\b(test|check|--tests)\b",
        r"\bpytest\b",
        r"\bpython\b.*?\b-m\s+(unittest|pytest)\b",
        r"\b(npm|yarn|pnpm|bun)\b.*?\b(test|run\s+test)\b",
        r"\bgo\b\s+test\b",
        r"\bcargo\b\s+test\b",
        r"\bdotnet\b\s+test\b",
    ]
    for pattern in test_block_patterns:
        if re.search(pattern, lowered_cmd):
            return {
                "content": [{
                    "type": "text",
                    "text": (
                        f"Execution Blocked: Executing tests ('{cmd_str}') is strictly prohibited to preserve "
                        "token quota and avoid flooding the context window with build/test logs. "
                        "Do not run test suites or individual test methods. "
                        "Perform code changes and static analysis, then instruct the user under 'Next Steps' to run the test locally."
                    )
                }],
                "isError": True
            }
    
    # Environment with non-interactive defaults
    env = os.environ.copy()
    env["CI"] = "true"
    env["DEBIAN_FRONTEND"] = "noninteractive"
    env["PAGER"] = "cat"

    try:
        proc = subprocess.run(
            cmd_str,
            shell=True,
            executable="/bin/bash",         # Use full bash rather than /bin/sh
            stdin=subprocess.DEVNULL,       # Prevent hanging on interactive prompts
            capture_output=True,
            text=True,
            cwd=cwd,
            env=env,
            timeout=120                     # 2 minutes for tests / installs
        )
        output = proc.stdout or ""
        if proc.stderr:
            output += f"\n[stderr]\n{proc.stderr}"
        if proc.returncode != 0:
            output += f"\n[exit code: {proc.returncode}]"

        # Cap output to 64KB to avoid blowing up the LLM context window
        MAX_OUTPUT = 64 * 1024
        if len(output) > MAX_OUTPUT:
            output = output[:MAX_OUTPUT] + "\n... [output truncated]"

        return {"content": [{"type": "text", "text": output or "(command finished with no output)"}]}
    except subprocess.TimeoutExpired:
        return {"content": [{"type": "text", "text": "Execution timed out (120s limit). If running a daemon/server, run it in background."}], "isError": True}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Execution error: {e}"}], "isError": True}

def retrieve_local_workspace_context(query: str, max_chars: int = 12000) -> str:
    """Fast zero-cost local code retrieval from /workspace for keywords mentioned in query."""
    root = Path(WORKSPACE_ROOT)
    if not root.exists():
        return ""

    # Extract potential symbol names (CamelCase, snake_case, alphanumeric terms > 3 chars)
    words = re.findall(r'[A-Za-z0-9_]{4,}', query)
    stop_words = {
        "what", "where", "when", "which", "with", "from", "that", "this", "have", "test",
        "case", "code", "file", "mock", "should", "using", "class", "method", "into",
        "true", "false", "please", "write", "create", "implement", "verify", "check"
    }
    candidates = [w for w in words if w.lower() not in stop_words]
    if not candidates:
        return ""

    SKIP_DIRS = {".git", "node_modules", "target", "build", ".gradle", "venv", "env", "__pycache__", ".idea", ".vscode", "dist"}
    snippets = []
    total_len = 0

    # Scan workspace files for matched lines
    for p in root.rglob("*"):
        if p.is_dir() or any(part in SKIP_DIRS or part.startswith(".") for part in p.parts):
            continue
        ext = p.suffix.lower()
        if ext not in (".java", ".py", ".ts", ".js", ".json", ".yml", ".yaml", ".xml"):
            continue

        try:
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        rel_p = str(p.relative_to(root)).replace("\\", "/")
        lines = content.splitlines()

        matched_line_indices = set()
        for idx, line in enumerate(lines):
            for cand in candidates:
                if cand in line:
                    for offset in range(max(0, idx - 2), min(len(lines), idx + 5)):
                        matched_line_indices.add(offset)

        if matched_line_indices:
            sorted_indices = sorted(matched_line_indices)
            groups = []
            current_group = []
            for line_idx in sorted_indices:
                if not current_group or line_idx == current_group[-1] + 1:
                    current_group.append(line_idx)
                else:
                    groups.append(current_group)
                    current_group = [line_idx]
            if current_group:
                groups.append(current_group)

            file_snippets = []
            for g in groups[:3]:
                block = "\n".join(f"{li+1}: {lines[li]}" for li in g)
                file_snippets.append(block)

            joined_blocks = "\n...\n".join(file_snippets)
            snippet_str = f"--- File: {rel_p} ---\n{joined_blocks}\n"
            if total_len + len(snippet_str) > max_chars:
                break
            snippets.append(snippet_str)
            total_len += len(snippet_str)

    if snippets:
        return "## Retrieved Workspace Code Snippets:\n" + "\n".join(snippets)
    return ""

def handle_ask_local_assistant(args: dict) -> dict:
    query = args.get("query") or args.get("prompt") or args.get("question") or ""
    context = args.get("context") or args.get("code") or ""
    if not query:
        return {"content": [{"type": "text", "text": "Error: missing required 'query' parameter"}], "isError": True}

    # Automatically retrieve local workspace snippets if no explicit context was passed
    if not context:
        auto_snippets = retrieve_local_workspace_context(query)
        if auto_snippets:
            context = auto_snippets

    ollama_url = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
    ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:1.5b")
    ollama_timeout = int(os.getenv("OLLAMA_TIMEOUT", "300"))

    system_prompt = (
        "You are an embedded repository intelligence engine assisting a principal software engineer. "
        "Provide direct, high-density technical analysis, interface contracts, symbol traces, mock patterns, or implementation code. "
        "Base your answer strictly on the provided workspace context when available. "
        "Omit conversational preambles, greetings, apologies, and stylistic commentary. "
        "Ensure any returned code snippets are minimal and unembellished, with docstrings and comments omitted."
    )
    full_prompt = f"{system_prompt}\n\n"
    if context:
        full_prompt += f"Context:\n{context}\n\n"
    full_prompt += f"Query:\n{query}"

    import urllib.request
    req_body = json.dumps({
        "model": ollama_model,
        "prompt": full_prompt,
        "stream": False
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{ollama_url}/api/generate",
        data=req_body,
        headers={"Content-Type": "application/json"}
    )

    try:
        with urllib.request.urlopen(req, timeout=ollama_timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            answer = data.get("response", "").strip()

            # Post-process answer: strip comments/docstrings from code blocks if present
            try:
                from ast_compressor import compress_python_code
                def repl(m):
                    lang = m.group(1)
                    code = m.group(2)
                    if lang in ("python", "py", ""):
                        return f"```{lang}\n{compress_python_code(code)}\n```"
                    return m.group(0)
                answer = re.sub(r"```([a-zA-Z0-9_-]*)\n(.*?)```", repl, answer, flags=re.DOTALL)
            except Exception:
                pass

            return {"content": [{"type": "text", "text": f"[Local Ollama ({ollama_model} | 0 Cloud Tokens)]:\n{answer}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Local assistant query failed: {e}"}], "isError": True}

def handle_trace_symbol(args: dict) -> dict:
    symbol = (args.get("symbol") or args.get("name") or "").strip()
    if not symbol:
        return {"content": [{"type": "text", "text": "Error: missing required 'symbol' parameter"}], "isError": True}

    root = Path(WORKSPACE_ROOT)
    definitions = []
    usages = []

    SKIP_DIRS = {".git", "node_modules", "target", "build", ".gradle", "venv", "env", "__pycache__", ".idea", ".vscode", "dist"}

    # Multi-language scan: Java, Python, TypeScript, JavaScript
    for p in root.rglob("*"):
        if p.is_dir() or any(part in SKIP_DIRS or part.startswith(".") for part in p.parts):
            continue
        ext = p.suffix.lower()
        if ext not in (".java", ".py", ".ts", ".js", ".tsx", ".jsx"):
            continue

        try:
            rel_p = str(p.relative_to(root)).replace("\\", "/")
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        lines = content.splitlines()

        # Usages scan
        for idx, line in enumerate(lines, 1):
            if symbol in line:
                s_line = line.strip()
                if not any(s_line.startswith(decl) for decl in ("class ", "interface ", "enum ", "record ", "def ", "public class ", "public interface ", "public record ")):
                    usages.append(f"{rel_p}:{idx}: {s_line[:90]}")

        # Declarations scan
        if ext == ".py":
            import ast
            try:
                tree = ast.parse(content, filename=str(p))
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
                        args_list = [a.arg for a in node.args.args]
                        definitions.append(f"Python def {symbol}({', '.join(args_list)}) -> {rel_p}:{node.lineno}")
                    elif isinstance(node, ast.ClassDef) and node.name == symbol:
                        bases = [getattr(b, "id", "...") for b in node.bases]
                        definitions.append(f"Python class {symbol}({', '.join(bases)}) -> {rel_p}:{node.lineno}")
            except Exception:
                pass
        elif ext == ".java":
            class_regex = re.compile(r'^\s*(?:@[\w()]+\s+)*(?:public|protected|private)?\s*(?:abstract|static|final|\s)*\b(class|interface|record|enum)\s+(' + re.escape(symbol) + r')\b')
            method_regex = re.compile(r'^\s*(?:@[\w()]+\s+)*(?:public|protected|private)?\s*(?:static|final|synchronized|\s)*([\w<>\[\],\s]+)\s+(' + re.escape(symbol) + r')\s*\(([^)]*)\)')
            field_regex = re.compile(r'^\s*(?:@(?:Autowired|Mock|Spy|MockitoSpyBean|MockBean|Inject|Value|Resource)[\w()"\',=\s]*\s+)?(?:public|protected|private)?\s*([\w<>\[\],\s]+)\s+(' + re.escape(symbol) + r')\s*[;=]')

            for idx, line in enumerate(lines, 1):
                m_class = class_regex.search(line)
                if m_class:
                    prev_anno = lines[idx-2].strip() if idx > 1 and lines[idx-2].strip().startswith("@") else ""
                    anno_str = f"[{prev_anno}] " if prev_anno else ""
                    definitions.append(f"Java {anno_str}{m_class.group(1)} {symbol} -> {rel_p}:{idx}")
                    continue
                m_method = method_regex.search(line)
                if m_method:
                    ret_type = m_method.group(1).strip()
                    params = m_method.group(3).strip()
                    definitions.append(f"Java method: {ret_type} {symbol}({params}) -> {rel_p}:{idx}")
                    continue
                m_field = field_regex.search(line)
                if m_field and ("@" in line or "Repository" in line or "Service" in line or "Client" in line):
                    field_type = m_field.group(1).strip()
                    definitions.append(f"Java field: {field_type} {symbol} -> {rel_p}:{idx}")
        elif ext in (".ts", ".js", ".tsx", ".jsx"):
            ts_regex = re.compile(r'^\s*(?:export\s+)?(?:default\s+)?(class|interface|type|function|const|let)\s+(' + re.escape(symbol) + r')\b')
            for idx, line in enumerate(lines, 1):
                m_ts = ts_regex.search(line)
                if m_ts:
                    definitions.append(f"{ext[1:].upper()} {m_ts.group(1)} {symbol} -> {rel_p}:{idx}")

    out_lines = [f"=== Symbol Trace: '{symbol}' (Languages: Java, Python, TypeScript) ==="]
    if definitions:
        out_lines.append("## Definitions & Signatures:")
        out_lines.extend(f"- {d}" for d in definitions[:15])
    else:
        out_lines.append("## Definitions: (No explicit class/method declaration found in workspace)")

    if usages:
        out_lines.append(f"## Usages ({len(usages)} total, showing top 10):")
        out_lines.extend(f"- {u}" for u in usages[:10])
    else:
        out_lines.append("## Usages: (No call sites found)")

    return {"content": [{"type": "text", "text": "\n".join(out_lines)}]}

TOOLS = [
    {
        "name": "write_to_file",
        "description": "Create or write content to a file in the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path (e.g. /workspace/info.json or relative path info.json)"
                },
                "content": {
                    "type": "string",
                    "description": "The exact string content to write into the file"
                }
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "write_file",
        "description": "Alias for write_to_file: create or overwrite a file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path to create"
                },
                "content": {
                    "type": "string",
                    "description": "The content to write"
                }
            },
            "required": ["path", "content"]
        }
    },
    {
        "name": "run_command",
        "description": "Execute a shell command inside the workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to execute"
                }
            },
            "required": ["command"]
        }
    },
    {
        "name": "ask_local_assistant",
        "description": "Query local zero-cost Ollama assistant (qwen2.5-coder:1.5b) grounded with workspace code retrieval for signatures, mock patterns, and code drafting without consuming cloud tokens.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The question, mock pattern request, or code drafting prompt to ask local Ollama"
                },
                "context": {
                    "type": "string",
                    "description": "Optional code snippet or context to evaluate (auto-retrieves from workspace if empty)"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "trace_symbol",
        "description": "Trace symbol definitions (classes, interfaces, methods, Spring beans) and call sites across Java, Python, and TypeScript workspace files.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "The class, interface, method, or bean name to trace across the workspace"
                }
            },
            "required": ["symbol"]
        }
    },
    {
        "name": "delete_file",
        "description": "Delete one or multiple obsolete files/directories in a single turn. Accepts a single path, a list of paths, or space-separated paths.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "description": "File path, list of file paths, or space-separated paths to delete"
                }
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
                "capabilities": {
                    "tools": {}
                },
                "serverInfo": {
                    "name": "workspace-tools",
                    "version": "1.0.0"
                }
            }
        }
    
    if method == "notifications/initialized":
        return None
    
    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": TOOLS
            }
        }
    
    if method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name")
        tool_args = params.get("arguments", {})
        
        if tool_name in ("write_to_file", "write_file"):
            res = handle_write_to_file(tool_args)
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
        
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": res
        }
    
    if req_id is not None:
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {}
        }
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
            sys.stderr.write(f"MCP error: {e}\n")
            sys.stderr.flush()

if __name__ == "__main__":
    main()
