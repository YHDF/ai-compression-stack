#!/usr/bin/env python3
"""
AST-based Code Compressor and Token Optimization Engine.
Prunes comments, docstrings, redundant whitespace, and structures
code to minimize token consumption before sending context to LLMs.
"""

import os
import ast
import json
import re
from typing import Tuple, Dict, Any

class GlobalStats:
    total_requests: int = 0
    total_original_chars: int = 0
    total_compressed_chars: int = 0
    total_original_tokens: int = 0
    total_compressed_tokens: int = 0
    tokens_saved: int = 0

def estimate_tokens(text: str) -> int:
    """Rough estimation of token count (~4 characters per token)."""
    return max(1, len(text) // 4)

class DocstringRemover(ast.NodeTransformer):
    """AST Transformer to strip docstrings from modules, classes, and functions."""
    def _strip_docstring(self, node):
        if (node.body and 
            isinstance(node.body[0], ast.Expr) and 
            isinstance(node.body[0].value, (ast.Str, ast.Constant)) and
            isinstance(getattr(node.body[0].value, 'value', None), str)):
            node.body.pop(0)
        return node

    def visit_Module(self, node):
        self.generic_visit(node)
        return self._strip_docstring(node)

    def visit_ClassDef(self, node):
        self.generic_visit(node)
        return self._strip_docstring(node)

    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        return self._strip_docstring(node)

    def visit_AsyncFunctionDef(self, node):
        self.generic_visit(node)
        return self._strip_docstring(node)

def compress_python_code(code: str) -> str:
    """Minify Python code using AST parsing and unparsing."""
    try:
        tree = ast.parse(code)
        transformer = DocstringRemover()
        tree = transformer.visit(tree)
        ast.fix_missing_locations(tree)
        # ast.unparse removes comments, docstrings, and redundant blank lines
        compressed = ast.unparse(tree)
        return compressed
    except Exception:
        # Fallback to regex-based comment/whitespace stripping
        return strip_generic_whitespace(code)

def compress_json(text: str) -> str:
    """Minify JSON by removing indentation and whitespace."""
    try:
        data = json.loads(text)
        return json.dumps(data, separators=(',', ':'))
    except Exception:
        return text.strip()

def compress_aggressive_fallback(text: str, max_lines: int = 150) -> str:
    """
    Aggressive universal fallback compressor for unrecognized file formats:
    - Strips full-line comments (#, //, ;, %) common across configs, scripts, and DSLs.
    - Halves indentation whitespace (e.g. 4 spaces -> 2 spaces) while strictly preserving hierarchy.
    - Collapses repetitive divider runs (e.g., ---------- or ==========) to 3 characters.
    - Compresses excessive internal whitespace and eliminates blank lines.
    - Applies a head/tail budget window for files exceeding max_lines.
    """
    try:
        lines = text.splitlines()
        cleaned = []
        for i, raw_line in enumerate(lines):
            stripped = raw_line.strip()
            if not stripped:
                continue
            # Preserve shebang
            if i == 0 and stripped.startswith("#!"):
                cleaned.append(stripped)
                continue
            # Strip full-line comments
            if stripped.startswith(("#", "//", ";", "%")):
                continue

            # Collapse repetitive divider characters
            line = re.sub(r'([-=~*#_]){4,}', r'\1\1\1', raw_line)

            # Compress 4-space / 8-space indentation to 2-space / 4-space indentation
            leading_spaces = len(line) - len(line.lstrip(" "))
            content_part = line.lstrip(" ")
            # Collapse multiple internal spaces
            content_part = re.sub(r'[ \t]{2,}', ' ', content_part)

            indent = " " * (leading_spaces // 2) if leading_spaces > 1 else (" " * leading_spaces)
            cleaned.append(indent + content_part)

        if len(cleaned) > max_lines:
            head = cleaned[:80]
            tail = cleaned[-40:]
            omitted = len(cleaned) - 120
            cleaned = head + [f"... [{omitted} lines omitted to preserve token budget] ..."] + tail

        return "\n".join(cleaned)
    except Exception:
        return strip_generic_whitespace(text)

def strip_generic_whitespace(text: str) -> str:
    """Strip redundant whitespace, blank lines, and empty lines."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)

def compress_c_family_code(code: str) -> str:
    """Strip comments and redundant whitespace from C-style languages (JS, TS, C, C++, Java, Go, Rust, C#, PHP)."""
    pattern = r'(\'\'\'|"""|\'([^\'\\]*(\\.[^\'\\]*)*)\'|"([^"\\]*(\\.[^"\\]*)*)"|`([^`\\]*(\\.[^`\\]*)*)`)|(/\*[\s\S]*?\*/|//[^\r\n]*)'
    def replacer(match):
        if match.group(1):
            return match.group(1)
        return ""
    try:
        stripped = re.sub(pattern, replacer, code)
        lines = [line.rstrip() for line in stripped.splitlines() if line.strip()]
        return "\n".join(lines)
    except Exception:
        return strip_generic_whitespace(code)

def compress_html_xml(content: str) -> str:
    """Strip HTML/XML comments and collapse multi-line whitespace."""
    try:
        no_comments = re.sub(r'<!--[\s\S]*?-->', '', content)
        lines = [line.strip() for line in no_comments.splitlines() if line.strip()]
        return "\n".join(lines)
    except Exception:
        return strip_generic_whitespace(content)

def compress_css(content: str) -> str:
    """Strip CSS comments and redundant indentation."""
    try:
        no_comments = re.sub(r'/\*[\s\S]*?\*/', '', content)
        lines = [line.strip() for line in no_comments.splitlines() if line.strip()]
        return "\n".join(lines)
    except Exception:
        return strip_generic_whitespace(content)

def compress_shell_script(content: str) -> str:
    """Strip shell comments while preserving the shebang line."""
    try:
        lines = content.splitlines()
        result = []
        for i, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if i == 0 and stripped.startswith("#!"):
                result.append(stripped)
            elif stripped.startswith("#"):
                continue
            else:
                result.append(line.rstrip())
        return "\n".join(result)
    except Exception:
        return strip_generic_whitespace(content)

def compress_yaml(content: str) -> str:
    """Strip comment-only lines in YAML while strictly preserving indentation."""
    try:
        lines = content.splitlines()
        result = []
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            result.append(line.rstrip())
        return "\n".join(result)
    except Exception:
        return strip_generic_whitespace(content)

def compress_sql(content: str) -> str:
    """Strip single-line and multi-line SQL comments."""
    try:
        no_block = re.sub(r'/\*[\s\S]*?\*/', '', content)
        no_line = re.sub(r'--[^\r\n]*', '', no_block)
        lines = [line.strip() for line in no_line.splitlines() if line.strip()]
        return "\n".join(lines)
    except Exception:
        return strip_generic_whitespace(content)

def compress_tabular_data(content: str, max_rows: int = 15) -> str:
    """Compact CSV/TSV data; truncate huge datasets to a representative sample with schema header."""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if len(lines) <= max_rows:
        return "\n".join(lines)
    header = lines[0]
    sample = lines[1:max_rows]
    omitted = len(lines) - max_rows
    sample.append(f"... [{omitted} rows omitted to conserve tokens; total {len(lines)} rows]")
    return "\n".join([header] + sample)

def compress_markdown(content: str) -> str:
    """Strip HTML comments and excessive blank lines from Markdown."""
    try:
        no_comments = re.sub(r'<!--[\s\S]*?-->', '', content)
        # Collapse 3+ newlines into 2
        return re.sub(r'\n{3,}', '\n\n', no_comments).strip()
    except Exception:
        return strip_generic_whitespace(content)

def compress_code_snippet(content: str, filename: str = "") -> Tuple[str, int, int]:
    """
    Compress a code snippet based on its file extension.
    Returns: (compressed_text, orig_tokens, comp_tokens)
    """
    orig_tokens = estimate_tokens(content)
    lower_fn = filename.lower()
    ext = os.path.splitext(lower_fn)[1]

    if ext == ".py":
        compressed = compress_python_code(content)
    elif ext == ".json":
        compressed = compress_json(content)
    elif ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".c", ".cpp", ".cc", ".h", ".hpp", ".java", ".go", ".rs", ".cs", ".php", ".vue", ".svelte"):
        compressed = compress_c_family_code(content)
    elif ext in (".html", ".htm", ".xml", ".svg"):
        compressed = compress_html_xml(content)
    elif ext in (".css", ".scss", ".sass", ".less"):
        compressed = compress_css(content)
    elif ext in (".sh", ".bash", ".zsh"):
        compressed = compress_shell_script(content)
    elif ext in (".yaml", ".yml"):
        compressed = compress_yaml(content)
    elif ext == ".sql":
        compressed = compress_sql(content)
    elif ext in (".csv", ".tsv"):
        compressed = compress_tabular_data(content)
    elif ext in (".md", ".mdx", ".txt"):
        compressed = compress_markdown(content)
    else:
        compressed = compress_aggressive_fallback(content)

    comp_tokens = estimate_tokens(compressed)

    GlobalStats.total_requests += 1
    GlobalStats.total_original_chars += len(content)
    GlobalStats.total_compressed_chars += len(compressed)
    GlobalStats.total_original_tokens += orig_tokens
    GlobalStats.total_compressed_tokens += comp_tokens
    GlobalStats.tokens_saved += max(0, orig_tokens - comp_tokens)

    return compressed, orig_tokens, comp_tokens

def get_stats() -> Dict[str, Any]:
    """Retrieve cumulative AST compression statistics."""
    saved = GlobalStats.tokens_saved
    orig = GlobalStats.total_original_tokens
    ratio = (saved / orig * 100) if orig > 0 else 0.0

    return {
        "total_requests": GlobalStats.total_requests,
        "original_tokens": orig,
        "compressed_tokens": GlobalStats.total_compressed_tokens,
        "tokens_saved": saved,
        "savings_percentage": round(ratio, 2)
    }
