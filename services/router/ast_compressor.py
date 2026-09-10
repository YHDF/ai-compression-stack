#!/usr/bin/env python3
"""
AST-based Code Compressor and Token Optimization Engine.
Prunes comments, docstrings, redundant whitespace, and structures
code to minimize token consumption before sending context to LLMs.
"""

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

def strip_generic_whitespace(text: str) -> str:
    """Strip redundant whitespace, blank lines, and empty lines."""
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)

def compress_code_snippet(content: str, filename: str = "") -> Tuple[str, int, int]:
    """
    Compress a code snippet based on its file extension.
    Returns: (compressed_text, orig_tokens, comp_tokens)
    """
    orig_tokens = estimate_tokens(content)
    lower_fn = filename.lower()

    if lower_fn.endswith(".py"):
        compressed = compress_python_code(content)
    elif lower_fn.endswith(".json"):
        compressed = compress_json(content)
    else:
        compressed = strip_generic_whitespace(content)

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
