import os, time, json, base64, requests
from typing import List, Union, Dict, Any
from fastapi import FastAPI
from pydantic import BaseModel
from google import genai
from google.genai import types

app = FastAPI()
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://ollama:11434")
HEADROOM_PROXY = os.getenv("HEADROOM_PROXY", "http://headroom:8787")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5-coder:7b")

gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None

class ChatMessage(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]

class ChatCompletionRequest(BaseModel):
    model: str = "auto-router"
    messages: List[ChatMessage]

def parse_parts(content):
    text, parts = "", []
    if isinstance(content, str):
        text = content
        parts.append(types.Part.from_text(text=text))
    elif isinstance(content, list):
        for item in content:
            if item.get("type") == "text":
                text += item.get("text", "") + "\n"
                parts.append(types.Part.from_text(text=item.get("text", "")))
            elif item.get("type") == "image_url":
                url = item["image_url"]["url"]
                if "base64," in url:
                    header, data = url.split("base64,", 1)
                    mime = header.split(";")[0].replace("data:", "")
                    parts.append(types.Part.from_bytes(data=base64.b64decode(data), mime_type=mime))
                else:
                    r = requests.get(url, timeout=10)
                    parts.append(types.Part.from_bytes(data=r.content, mime_type=r.headers.get("Content-Type", "image/png")))
    return text, parts

@app.get("/healthz")
def healthz():
    return {"status": "ok"}

@app.post("/v1/chat/completions")
def completions(req: ChatCompletionRequest):
    last_msg = next((m for m in reversed(req.messages) if m.role == "user"), None)
    if not last_msg:
        return {"choices": [{"message": {"role": "assistant", "content": "No prompt received."}}]}

    prompt_text, parts = parse_parts(last_msg.content)
    has_image = any(not hasattr(p, "text") or p.text is None for p in parts)

    if has_image:
        res = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=parts)
        out = "*[Vision Routed: Gemini 2.5 Flash]*\n\n" + res.text
        return package_response(req.model, out)

    classify_prompt = f"Analyze task. Return JSON {{"is_complex_agent": bool, "distilled_prompt": string}}\nPrompt: {prompt_text}"
    try:
        ollama_res = requests.post(f"{OLLAMA_URL}/api/generate", json={"model": OLLAMA_MODEL, "prompt": classify_prompt, "format": "json", "stream": False}, timeout=15).json()
        meta = json.loads(ollama_res["response"])
    except Exception:
        meta = {"is_complex_agent": False, "distilled_prompt": prompt_text}

    if not meta.get("is_complex_agent", False):
        res = gemini_client.models.generate_content(model="gemini-2.5-flash", contents=meta.get("distilled_prompt", prompt_text))
        out = "*[Simple Task: Gemini 2.5 Flash]*\n\n" + res.text
        return package_response(req.model, out)

    try:
        comp_res = requests.post(f"{HEADROOM_PROXY}/v1/compress", json={"messages": [{"role": "user", "content": meta.get("distilled_prompt", prompt_text)}]}, timeout=15).json()
        prepared_prompt = comp_res.get("compressed_prompt", meta.get("distilled_prompt"))
    except Exception:
        prepared_prompt = meta.get("distilled_prompt")

    agent_run = gemini_client.create(agent="antigravity-preview-05-2026", input=prepared_prompt, environment="remote")
    out = "*[Agent Task: Headroom + Antigravity]*\n\n" + agent_run.output_text
    return package_response(req.model, out)

def package_response(model_name: str, content: str):
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model_name,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}]
    }
