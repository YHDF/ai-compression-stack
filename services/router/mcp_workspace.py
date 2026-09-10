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
