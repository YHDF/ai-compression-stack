#!/usr/bin/env python3
"""
AST-based Code Compressor and Token Optimization Engine.
Prunes comments, docstrings, and redundant whitespace to minimize token usage.
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
        if (node.body and isinstance(node.body[0], ast.Expr) and
                isinstance(node.body[0].value, ast.Constant) and
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


class Skeletonizer(ast.NodeTransformer):
    """AST Transformer to replace function/method bodies with '...' (signatures only)."""
    def visit_FunctionDef(self, node):
        node.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
        return node

    def visit_AsyncFunctionDef(self, node):
        node.body = [ast.Expr(value=ast.Constant(value=Ellipsis))]
        return node


def compress_python_to_skeleton(code: str) -> str:
    """Extract structural interface skeleton (classes, signatures, types) at ~80% token savings."""
    try:
        tree = ast.parse(code)
        tree = DocstringRemover().visit(tree)
        tree = Skeletonizer().visit(tree)
        ast.fix_missing_locations(tree)
        return ast.unparse(tree)
    except Exception:
        return compress_python_code(code)


def compress_python_code(code: str) -> str:
    """Minify Python code using AST parsing and unparsing."""
    try:
        tree = ast.parse(code)
        tree = DocstringRemover().visit(tree)
        ast.fix_missing_locations(tree)
        return ast.unparse(tree)
    except Exception:
        return strip_generic_whitespace(code)


def compress_json(text: str) -> str:
    """Minify JSON by removing indentation and whitespace."""
    try:
        return json.dumps(json.loads(text), separators=(',', ':'))
    except Exception:
        return text.strip()


def strip_generic_whitespace(text: str) -> str:
    """Strip redundant whitespace, blank lines, and empty lines."""
    return "\n".join(line.rstrip() for line in text.splitlines() if line.strip())


def compress_c_family_code(code: str) -> str:
    """Strip comments and collapse whitespace in C-family languages (JS, TS, C, Java, Go, Rust)."""
    pattern = r'(\'\'\'|"""|\'([^\'\\]*(\\.[^\'\\]*)*)\'|"([^"\\]*(\\.[^"\\]*)*)"|`([^`\\]*(\\.[^`\\]*)*)`)|(/\*[\s\S]*?\*/|//[^\r\n]*)'
    try:
        stripped = re.sub(pattern, lambda m: m.group(1) or "", code)
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        result = "\n".join(lines)
        result = re.sub(r'\s*([\{\}\;,])\s*\n\s*', r'\1 ', result)
        return re.sub(r'\n{2,}', '\n', result).strip()
    except Exception:
        return strip_generic_whitespace(code)


def compress_log_file(content: str, max_lines: int = 100) -> str:
    """Compress application logs by keeping errors and filtering repetitive framework frames."""
    try:
        lines = [l.rstrip() for l in content.splitlines() if l.strip()]
        framework_prefixes = ("at org.springframework.", "at org.apache.", "at jakarta.", "at javax.", "at sun.", "at java.base/")
        filtered = [
            l for l in lines
            if any(k in l for k in ("ERROR", "FATAL", "Exception", "Caused by:", "FAIL"))
            or (l.strip().startswith("at ") and not any(l.strip().startswith(p) for p in framework_prefixes))
            or not l.strip().startswith("at ")
        ]
        if len(filtered) > max_lines:
            omitted = len(filtered) - max_lines
            filtered = filtered[:max_lines - 20] + [f"... [{omitted} log lines omitted to conserve tokens] ..."] + filtered[-20:]
        return "\n".join(filtered)
    except Exception:
        return strip_generic_whitespace(content)


def compress_html_xml(content: str) -> str:
    """Strip HTML/XML comments and collapse whitespace."""
    try:
        return "\n".join(l.strip() for l in re.sub(r'<!--[\s\S]*?-->', '', content).splitlines() if l.strip())
    except Exception:
        return strip_generic_whitespace(content)


def compress_css(content: str) -> str:
    """Strip CSS comments and redundant indentation."""
    try:
        return "\n".join(l.strip() for l in re.sub(r'/\*[\s\S]*?\*/', '', content).splitlines() if l.strip())
    except Exception:
        return strip_generic_whitespace(content)


def compress_shell_script(content: str) -> str:
    """Strip shell comments while preserving the shebang line."""
    try:
        return "\n".join(
            l.strip() if i == 0 and l.strip().startswith("#!") else l.rstrip()
            for i, l in enumerate(content.splitlines())
            if l.strip() and (i == 0 and l.strip().startswith("#!") or not l.strip().startswith("#"))
        )
    except Exception:
        return strip_generic_whitespace(content)


def compress_yaml(content: str) -> str:
    """Strip comment-only lines in YAML while preserving indentation."""
    try:
        return "\n".join(l.rstrip() for l in content.splitlines() if l.strip() and not l.strip().startswith("#"))
    except Exception:
        return strip_generic_whitespace(content)


def compress_sql(content: str) -> str:
    """Strip single-line and multi-line SQL comments."""
    try:
        no_block = re.sub(r'/\*[\s\S]*?\*/', '', content)
        no_line = re.sub(r'--[^\r\n]*', '', no_block)
        return "\n".join(l.strip() for l in no_line.splitlines() if l.strip())
    except Exception:
        return strip_generic_whitespace(content)


def compress_tabular_data(content: str, max_rows: int = 15) -> str:
    """Compact CSV/TSV data; truncate huge datasets to a representative sample."""
    lines = [l.strip() for l in content.splitlines() if l.strip()]
    if len(lines) <= max_rows:
        return "\n".join(lines)
    omitted = len(lines) - max_rows
    return "\n".join([lines[0]] + lines[1:max_rows] + [f"... [{omitted} rows omitted to conserve tokens; total {len(lines)} rows]"])


def compress_markdown(content: str) -> str:
    """Strip HTML comments and excessive blank lines from Markdown."""
    try:
        return re.sub(r'\n{3,}', '\n\n', re.sub(r'<!--[\s\S]*?-->', '', content)).strip()
    except Exception:
        return strip_generic_whitespace(content)


def compress_aggressive_fallback(text: str, max_lines: int = 150) -> str:
    """Universal fallback compressor for unknown formats: strips comments, halves indentation."""
    try:
        cleaned = []
        for i, raw_line in enumerate(text.splitlines()):
            stripped = raw_line.strip()
            if not stripped or (stripped.startswith(("#", "//", ";", "%")) and not (i == 0 and stripped.startswith("#!"))):
                continue
            line = re.sub(r'([-=~*#_]){4,}', r'\1\1\1', raw_line)
            leading = len(line) - len(line.lstrip(" "))
            cleaned.append((" " * (leading // 2 if leading > 1 else leading)) + re.sub(r'[ \t]{2,}', ' ', line.lstrip(" ")))

        if len(cleaned) > max_lines:
            omitted = len(cleaned) - 120
            cleaned = cleaned[:80] + [f"... [{omitted} lines omitted to preserve token budget] ..."] + cleaned[-40:]
        return "\n".join(cleaned)
    except Exception:
        return strip_generic_whitespace(text)


def compress_code_snippet(content: str, filename: str = "", skeleton_only: bool = False) -> Tuple[str, int, int]:
    """Compress code based on extension. Returns: (compressed_text, orig_tokens, comp_tokens)."""
    orig_tokens = estimate_tokens(content)
    ext = os.path.splitext(filename.lower())[1]

    if ext == ".py":
        compressed = compress_python_to_skeleton(content) if skeleton_only else compress_python_code(content)
    elif ext == ".json":
        compressed = compress_json(content)
    elif ext in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".c", ".cpp", ".cc", ".h", ".java", ".go", ".rs", ".cs", ".php"):
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
    elif ext == ".log":
        compressed = compress_log_file(content)
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
    return {
        "total_requests": GlobalStats.total_requests,
        "original_tokens": orig,
        "compressed_tokens": GlobalStats.total_compressed_tokens,
        "tokens_saved": saved,
        "savings_percentage": round((saved / orig * 100) if orig > 0 else 0.0, 2)
    }
