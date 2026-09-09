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
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b").strip()

# Initialize Gemini client only if a non-empty key is provided
gemini_client = None
if GEMINI_API_KEY and GEMINI_API_KEY != "your_key_here":
    try:
        from google import genai
        gemini_client = genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        print(f"Notice: Gemini client not initialized: {e}")

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
    try:
        payload = {
            "model": OLLAMA_MODEL,
            "prompt": prompt,
            "stream": False
        }
        resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=120)
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
            "headroom_proxy": HEADROOM_PROXY
        }
    )

def compress_prompt(prompt: str) -> str:
    """Strip AST/JSON bloat via Headroom proxy before execution."""
    try:
        payload = {
            "messages": [
                {"role": "user", "content": prompt}
            ]
        }
        res = requests.post(f"{HEADROOM_PROXY}/v1/compress", json=payload, timeout=15)
        if res.status_code == 200:
            data = res.json()
            compressed = data.get("compressed_prompt") or data.get("content")
            if compressed:
                return compressed
    except Exception as e:
        print(f"Headroom compression bypassed: {e}")
    return prompt

def execute_agy(prepared_prompt: str) -> Optional[str]:
    """Execute agy CLI non-interactively using Python subprocess with 180s timeout."""
    agy_path = shutil.which("agy") or "/usr/local/bin/agy"
    if not os.path.exists(agy_path) and not shutil.which("agy"):
        print(f"agy executable not found at {agy_path}")
        return None

    cmd = [agy_path, "--dangerously-skip-permissions", "-p", prepared_prompt]
    env = os.environ.copy()
    env["HOME"] = "/root"

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=180,
            env=env
        )
        output = proc.stdout.strip()
        stderr_lower = proc.stderr.lower() if proc.stderr else ""
        stdout_lower = output.lower()

        if proc.returncode != 0:
            print(f"agy exited with code {proc.returncode}: {proc.stderr}")
            return None

        if "quota exceeded" in stdout_lower or "quota exceeded" in stderr_lower or "rate limit" in stdout_lower:
            print("agy reported quota or rate limit exceeded")
            return None

        return output if output else "Task completed successfully."
    except subprocess.TimeoutExpired:
        print("agy execution timed out (180s)")
        return None
    except Exception as e:
        print(f"agy subprocess execution error: {e}")
        return None

@app.post("/v1/chat/completions")
def completions(req: ChatCompletionRequest):
    last_msg = next((m for m in reversed(req.messages) if m.role == "user"), None)
    if not last_msg:
        return package_response(req.model, "No prompt received.")

    prompt_text, parts, has_image = parse_parts(last_msg.content)

    # 1. Multimodal / Vision Bypass
    if has_image:
        if gemini_client and parts:
            try:
                res = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=parts)
                out = "*[Vision Routed: Gemini 2.5 Flash]*\n\n" + (res.text or "")
                return package_response(req.model, out)
            except Exception as e:
                print(f"Gemini vision request failed: {e}")
        # Fallback to local Ollama if no Gemini key or request failed
        gen_out = call_ollama_generation(f"[Image content attached] {prompt_text}")
        return package_response(req.model, "*[Fallback: Local Ollama]*\n\n" + gen_out)

    # 2. Local Distillation (Ollama)
    structured_schema = {
        "type": "object",
        "properties": {
            "is_complex_agent": {
                "type": "boolean",
                "description": "True if prompt involves multi-file refactoring, broad codebase edits, or agent-level actions. False for standard questions, single snippets, or algorithms."
            },
            "distilled_prompt": {
                "type": "string",
                "description": "The distilled and focused instruction."
            },
            "file_dependencies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of affected or referenced files."
            }
        },
        "required": ["is_complex_agent", "distilled_prompt"]
    }

    meta = {"is_complex_agent": False, "distilled_prompt": prompt_text, "file_dependencies": []}
    try:
        ollama_res = requests.post(
            f"{OLLAMA_URL}/api/generate",
            json={
                "model": OLLAMA_MODEL,
                "prompt": f"Analyze this user coding request. Classify whether it requires an interactive complex agent or is a simple task. Distill the prompt and isolate file dependencies:\n\n{prompt_text}",
                "format": structured_schema,
                "stream": False
            },
            timeout=15
        )
        if ollama_res.status_code == 200:
            resp_json = ollama_res.json()
            raw_meta = json.loads(resp_json.get("response", "{}"))
            if isinstance(raw_meta, dict):
                meta.update(raw_meta)
    except Exception as e:
        print(f"Ollama distillation fallback: {e}")

    is_complex = meta.get("is_complex_agent", False)
    distilled_prompt = meta.get("distilled_prompt") or prompt_text

    # 3 & 4. Complex Agent Task -> Headroom Compression -> agy Execution
    if is_complex:
        compressed_prompt = compress_prompt(distilled_prompt)
        agy_output = execute_agy(compressed_prompt)
        if agy_output is not None:
            out = "*[Agent Task: Headroom + Antigravity]*\n\n" + agy_output
            return package_response(req.model, out)

        # Automatic fallback: if agy exits non-zero or exceeds quota, fallback to Ollama
        fallback_out = call_ollama_generation(distilled_prompt)
        return package_response(req.model, "*[Fallback: Local Ollama]*\n\n" + fallback_out)

    # Simple Task: Use Gemini 2.5 Flash if client available, otherwise route to local Ollama
    if gemini_client:
        try:
            res = gemini_client.models.generate_content(
                model="gemini-2.5-flash",
                contents=distilled_prompt
            )
            out = "*[Simple Task: Gemini 2.5 Flash]*\n\n" + (res.text or "")
            return package_response(req.model, out)
        except Exception as e:
            print(f"Gemini simple generation error: {e}")

    # Fallback / Default local Ollama (qwen2.5-coder:7b)
    ollama_out = call_ollama_generation(distilled_prompt)
    return package_response(req.model, "*[Simple Task: Local Ollama]*\n\n" + ollama_out)
