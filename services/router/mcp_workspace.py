#!/usr/bin/env python3
"""
Lightweight zero-dependency Model Context Protocol (MCP) server
exposing workspace file-write and execution tools to Antigravity (agy).
"""

import sys
import json
import os
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

def handle_ask_local_assistant(args: dict) -> dict:
    query = args.get("query") or args.get("prompt") or args.get("question") or ""
    context = args.get("context") or args.get("code") or ""
    if not query:
        return {"content": [{"type": "text", "text": "Error: missing required 'query' parameter"}], "isError": True}

    ollama_url = os.getenv("OLLAMA_URL", "http://ollama:11434").rstrip("/")
    ollama_model = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:0.5b")
    ollama_timeout = int(os.getenv("OLLAMA_TIMEOUT", "45"))

    system_prompt = (
        "You are an embedded repository intelligence engine assisting a principal software engineer. "
        "Provide direct, high-density technical analysis, interface contracts, symbol traces, or implementation facts. "
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
                import re
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

            return {"content": [{"type": "text", "text": f"[Local Ollama (0 Cloud Tokens)]:\n{answer}"}]}
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Local assistant query failed: {e}"}], "isError": True}

def handle_trace_symbol(args: dict) -> dict:
    symbol = (args.get("symbol") or args.get("name") or "").strip()
    if not symbol:
        return {"content": [{"type": "text", "text": "Error: missing required 'symbol' parameter"}], "isError": True}

    import ast
    root = Path(WORKSPACE_ROOT)
    definitions = []
    usages = []

    # Scan python files in workspace (skip venv, git, cache)
    for p in root.rglob("*.py"):
        if any(part.startswith(".") or part in ("venv", "env", "__pycache__", "node_modules") for part in p.parts):
            continue
        try:
            rel_p = str(p.relative_to(root))
            content = p.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        lines = content.splitlines()
        # Find usage lines
        for idx, line in enumerate(lines, 1):
            if symbol in line:
                s_line = line.strip()
                if not (s_line.startswith("def ") or s_line.startswith("class ")):
                    usages.append(f"{rel_p}:{idx}: {s_line[:80]}")

        # AST parse for definitions
        try:
            tree = ast.parse(content, filename=str(p))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol:
                    args_list = [a.arg for a in node.args.args]
                    definitions.append(f"Function in {rel_p}:{node.lineno} -> def {symbol}({', '.join(args_list)})")
                elif isinstance(node, ast.ClassDef) and node.name == symbol:
                    bases = [getattr(b, "id", "...") for b in node.bases]
                    definitions.append(f"Class in {rel_p}:{node.lineno} -> class {symbol}({', '.join(bases)})")
        except Exception:
            pass

    out_lines = [f"=== Symbol Trace: '{symbol}' ==="]
    if definitions:
        out_lines.append("## Definitions:")
        out_lines.extend(f"- {d}" for d in definitions)
    else:
        out_lines.append("## Definitions: (No explicit def/class found in workspace)")

    if usages:
        out_lines.append(f"## Usages ({len(usages)} total, showing up to 10):")
        out_lines.extend(f"- {u}" for u in usages[:10])
    else:
        out_lines.append("## Usages: (No calls/references found)")

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
        "description": "Query local zero-cost Ollama assistant for architecture, call references, or logic summaries without consuming cloud tokens.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "The exact question or search request to ask local Ollama"
                },
                "context": {
                    "type": "string",
                    "description": "Optional code snippet or context to evaluate"
                }
            },
            "required": ["query"]
        }
    },
    {
        "name": "trace_symbol",
        "description": "Trace Python symbol definitions (functions, classes) and callers across workspace files using fast AST parsing.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "symbol": {
                    "type": "string",
                    "description": "The function or class name to trace across the workspace"
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
