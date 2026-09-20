# AI Compression Stack - Test Suite Reference (`TEST.md`)

This document tracks all 40 automated unit, security, and integration tests across the AI Compression Stack, detailing exactly what each test verifies, the inputs tested, and the precise assertions made.

---

## Quick Execution

Run the complete 40-test suite inside the running Docker container:

```bash
docker exec quota-router python -m unittest discover -s tests
```

Or within the `services/router` directory:

```bash
python -m unittest discover -s tests
```

**Expected Result:**
```text
Ran 40 tests in ~7.0s
OK
```

---

## Test Suite Architecture & Summary

| Test Module | File Location | Tests | Focus Area |
| :--- | :--- | :---: | :--- |
| **AST Compression & Skeletonizer** | [`services/router/tests/test_ast_compressor.py`](file:///c:/Users/EX4197/scripts/ai-compression-stack/services/router/tests/test_ast_compressor.py) | **19** | Token reduction, docstring removal, skeletonization, syntax fallback, multi-format minification, binary safety. |
| **MCP Workspace, Routing & Security** | [`services/router/tests/test_mcp_workspace.py`](file:///c:/Users/EX4197/scripts/ai-compression-stack/services/router/tests/test_mcp_workspace.py) | **21** | Tech Lead routing, local delegation, circuit breakers, sandboxing, quota failover, auto-persistence, SSE streaming. |
| **TOTAL** | — | **40** | **100% Passing** |

---

## 1. AST Compressor Module (`test_ast_compressor.py`)

Target Implementation: [`services/router/src/ast_compressor.py`](file:///c:/Users/EX4197/scripts/ai-compression-stack/services/router/src/ast_compressor.py)

### 1. `test_estimate_tokens`
- **Target Function:** `estimate_tokens(text: str) -> int`
- **Scenario:** Evaluates character-to-token heuristic estimation across varied input lengths.
- **What It Does Exactly:** 
  - Tests string `'12345678'` (8 characters) and asserts return value is `2` tokens (based on ~4 chars/token).
  - Tests a single character `'a'` and asserts return value is `1` token (guaranteeing non-empty inputs return at least 1 token).
  - Tests empty string `''` and asserts return value is `0`.

### 2. `test_compress_python_code`
- **Target Function:** `compress_python_code(code: str) -> str`
- **Scenario:** Feeds a standard Python class containing module docstrings, class docstrings, method docstrings, and inline comments.
- **What It Does Exactly:**
  - Parses code via `ast.parse()` using `DocstringRemover(ast.NodeTransformer)`.
  - Asserts that all docstrings (`"""Module level"""`, `"""Class level"""`, `"""Method level"""`) are completely stripped.
  - Asserts that comments like `# Initialize` and `# Add logic` are removed.
  - Asserts that code structure, class definition (`class Calculator`), method signature (`def add(self, a, b)`), and return logic (`return a + b`) remain 100% intact and syntactically valid.

### 3. `test_compress_python_syntax_error_fallback`
- **Target Function:** `compress_python_code(code: str) -> str`
- **Scenario:** Passes invalid Python syntax (e.g., `def broken_func(:\n print("bad")`).
- **What It Does Exactly:**
  - Traps the `SyntaxError` from `ast.parse()`.
  - Asserts that the function does not crash or raise an unhandled exception.
  - Validates fallback to whitespace line-stripping (`compress_aggressive_fallback`).

### 4. `test_compress_json`
- **Target Function:** `compress_json(text: str) -> str`
- **Scenario:** Passes a multi-line formatted JSON string with indentation and spaces.
- **What It Does Exactly:**
  - Deserializes via `json.loads()` and re-serializes with `separators=(',', ':')`.
  - Asserts output is a single-line minified string without newlines or indentation spaces.
  - Asserts invalid JSON strings gracefully fall back to regex whitespace collapsing without raising `json.JSONDecodeError`.

### 5. `test_compress_c_family_code_aggressive`
- **Target Function:** `compress_c_family_code(code: str) -> str`
- **Scenario:** Passes C/Java/TypeScript/Rust-style source code with single-line (`//`), multi-line (`/* ... */`), and Javadoc (`/** ... */`) comments, plus whitespace around braces.
- **What It Does Exactly:**
  - Strips all block and inline comments using regular expressions.
  - Collapses blank lines around `{`, `}`, and `;`.
  - Asserts output removes comment text while preserving code tokens and class structure.

### 6. `test_compress_log_file_spring_boot`
- **Target Function:** `compress_log_file(text: str) -> str`
- **Scenario:** Simulates 50+ lines of Spring Boot / Java application logs with INFO messages, an NullPointerException stack trace, and repetitive JVM internal stack lines.
- **What It Does Exactly:**
  - Filters lines: keeps lines containing `ERROR`, `Exception`, or `Caused by:`.
  - Strips repetitive internal framework lines matching patterns like `at org.springframework...` or `at sun.reflect...`.
  - Asserts reduction of log volume by >50% while preserving the exact root cause and line numbers.

### 7. `test_compress_html_xml`
- **Target Function:** `compress_html_xml(text: str) -> str`
- **Scenario:** Passes HTML/XML markup with `<!-- multi-line comments -->` and inter-tag indentation.
- **What It Does Exactly:**
  - Removes `<!-- ... -->` comment blocks.
  - Collapses whitespace between tags `> <` into `><`.
  - Asserts HTML tags and content remain intact while comment characters and indentation are stripped.

### 8. `test_compress_css`
- **Target Function:** `compress_css(text: str) -> str`
- **Scenario:** Passes CSS with `/* comments */`, newlines, and multi-space indentation.
- **What It Does Exactly:**
  - Removes block comments.
  - Minifies rules, colons, and braces into compact single lines.
  - Asserts CSS selectors and declarations remain syntactically intact with minimum token usage.

### 9. `test_compress_shell_script`
- **Target Function:** `compress_shell_script(text: str) -> str`
- **Scenario:** Passes a Bash shell script starting with `#!/bin/bash` followed by header comments, explanation comments, and commands.
- **What It Does Exactly:**
  - Identifies and strictly preserves the shebang (`#!/bin/bash` or `#!/bin/sh`) on line 1.
  - Strips all `#` comment lines below line 1.
  - Asserts executable commands remain intact.

### 10. `test_compress_yaml`
- **Target Function:** `compress_yaml(text: str) -> str`
- **Scenario:** Passes YAML with nested indentation, key-value mappings, and `# comments`.
- **What It Does Exactly:**
  - Strips comment lines without disturbing space-sensitive indentation (which would break YAML parsing).
  - Asserts that child keys retain their exact 2-space / 4-space offsets.

### 11. `test_compress_sql`
- **Target Function:** `compress_sql(text: str) -> str`
- **Scenario:** Passes SQL queries with `-- inline comments` and `/* block comments */`.
- **What It Does Exactly:**
  - Strips line and block comments.
  - Collapses redundant whitespace.
  - Asserts query keywords (`SELECT`, `FROM`, `WHERE`, `JOIN`) remain intact.

### 12. `test_compress_tabular_data`
- **Target Function:** `compress_tabular_data(text: str) -> str`
- **Scenario:** Passes a CSV dataset of 50+ rows.
- **What It Does Exactly:**
  - Retains the CSV header row.
  - Retains the first 5 sample data rows.
  - Truncates the remaining 45 rows and appends an informational summary line (`... [X rows omitted]`).
  - Asserts massive token savings while preserving the schema and sample values.

### 13. `test_compress_markdown`
- **Target Function:** `compress_markdown(text: str) -> str`
- **Scenario:** Passes Markdown documents containing HTML comments and 4+ consecutive empty lines.
- **What It Does Exactly:**
  - Strips HTML comments (`<!-- ... -->`).
  - Collapses 3+ consecutive newlines down to 2 (`\n\n`).
  - Asserts Markdown headers, lists, and code fences are preserved.

### 14. `test_compress_aggressive_fallback`
- **Target Function:** `compress_aggressive_fallback(text: str) -> str`
- **Scenario:** Passes arbitrary text from unsupported file extensions containing repetitive decorative headers (e.g. `========================`).
- **What It Does Exactly:**
  - Collapses repeated delimiter characters down to 3 characters (`===`).
  - Strips empty lines and trims leading/trailing whitespace.
  - Asserts safe token reduction for unknown file types.

### 15. `test_compress_code_snippet_dispatcher`
- **Target Function:** `compress_code_snippet(content: str, filename: str) -> dict`
- **Scenario:** Dispatches files by extension (`test.py`, `data.json`, `app.java`, `style.css`).
- **What It Does Exactly:**
  - Verifies that file extensions route to their appropriate dedicated minifier.
  - Asserts returned dictionary includes `compressed_content`, `original_tokens`, `compressed_tokens`, and `reduction_pct`.
  - Asserts global statistics counters (`TOTAL_TOKENS_SAVED`) increment properly.

### 16. `test_compress_python_to_skeleton`
- **Target Function:** `compress_python_to_skeleton(code: str) -> str`
- **Scenario:** **Core Skeletonizer Test**. Passes a Python module with complex functions, docstrings, internal calculations, loops, and return statements.
- **What It Does Exactly:**
  - Traverses AST using `Skeletonizer(ast.NodeTransformer)`.
  - Replaces all function, method, and async method bodies with `...` (Ellipsis).
  - Asserts function signatures, default arguments, and type hints are preserved while implementation details are omitted, saving up to 80% context tokens for symbol resolution.

### 17. `test_compress_binary_and_non_utf8_files`
- **Target Function:** `compress_code_snippet(content: str, filename: str) -> dict`
- **Scenario:** Passes non-UTF-8 binary data (e.g. mock PNG/JPEG header bytes).
- **What It Does Exactly:**
  - Asserts that binary files do not trigger `UnicodeDecodeError` or crash the compression pipeline.
  - Asserts safe fallback behavior.

### 18. `test_ast_skeletonizer_complex_python_features`
- **Target Function:** `compress_python_to_skeleton(code: str) -> str`
- **Scenario:** Passes Python code utilizing `@dataclass`, `@property`, `@staticmethod`, `async def`, and variable type annotations.
- **What It Does Exactly:**
  - Asserts all decorators (`@dataclass`, `@property`, `@staticmethod`) remain attached to classes and methods.
  - Asserts `async def` declarations retain the `async` keyword.
  - Asserts class-level type annotations (`name: str`, `count: int = 0`) are preserved.
  - Asserts method bodies collapse to `...`.

### 19. `test_ast_compressor_empty_and_whitespace_files`
- **Target Function:** `compress_code_snippet(content: str, filename: str) -> dict`
- **Scenario:** Edge case: passes 0-byte empty strings, strings with only spaces, and strings with only newlines.
- **What It Does Exactly:**
  - Asserts function returns cleanly without throwing index out-of-bounds or AST syntax errors.
  - Asserts `compressed_content` is an empty string and `reduction_pct` is `0.0`.

---

## 2. MCP Workspace, Routing & Security Module (`test_mcp_workspace.py`)

Target Implementations: [`services/router/src/mcp_workspace.py`](file:///c:/Users/EX4197/scripts/ai-compression-stack/services/router/src/mcp_workspace.py) and [`services/router/src/app.py`](file:///c:/Users/EX4197/scripts/ai-compression-stack/services/router/src/app.py)

### 20. `test_normalize_path_security`
- **Target Function:** `normalize_path(path: str) -> Path`
- **Scenario:** Resolves relative and absolute file paths against `/workspace`.
- **What It Does Exactly:**
  - Tests relative path `'src/main.py'` and asserts it resolves to `/workspace/src/main.py`.
  - Tests path with internal traversal `'src/../src/main.py'` and asserts canonical normalization.
  - Validates containment within the workspace boundary.

### 21. `test_handle_write_and_delete_file`
- **Target Functions:** `handle_write_to_file(args)`, `handle_delete_file(args)`
- **Scenario:** Executes the write-then-delete lifecycle of a source file via MCP tool calls.
- **What It Does Exactly:**
  - Calls `handle_write_to_file` with `path="demo.py"`, `content="print('hello')\n"`.
  - Asserts file exists on disk and reads back matching content.
  - Calls `handle_delete_file` with `path="demo.py"`.
  - Asserts file is removed from disk and returns success message.

### 22. `test_handle_run_command_blocks_test_runners`
- **Target Function:** `handle_run_command(args)`
- **Scenario:** **Token & Runaway Loop Guard**. Attempts to invoke test execution commands across all ecosystems.
- **What It Does Exactly:**
  - Tests: `mvn test`, `gradle test`, `npm test`, `pytest`, `cargo test`, `dotnet test`, `jest`.
  - Asserts that every test command is rejected with an explanatory error instructing the model to let the human developer or CI run tests outside the token-draining LLM loop.

### 23. `test_retrieve_local_workspace_context`
- **Target Function:** `retrieve_local_workspace_context(prompt: str) -> str`
- **Scenario:** Scans workspace files for keyword references matching prompt tokens at **0 cloud tokens**.
- **What It Does Exactly:**
  - Creates files with target classes (`class UserService:`, `class OrderService:`).
  - Calls `retrieve_local_workspace_context("Where is UserService defined?")`.
  - Asserts returned pre-read context includes `UserService` with line number references, while ignoring unrelated files.

### 24. `test_handle_ask_local_assistant_ollama_mock`
- **Target Function:** `handle_ask_local_assistant(args)`
- **Scenario:** Invokes local Ollama (1.5B/3B model) via HTTP mock.
- **What It Does Exactly:**
  - Mocks `urllib.request.urlopen` returning a valid Ollama JSON payload.
  - Asserts prompt is forwarded with appropriate system prompt.
  - Asserts parsed response is returned directly to the agent tool caller.

### 25. `test_handle_trace_symbol_python_and_java`
- **Target Function:** `handle_trace_symbol(args)`
- **Scenario:** Cross-language symbol tracing for Python (`def calculate_tax`, `class TaxEngine`) and Java (`public class PaymentProcessor`, `public void processOrder`).
- **What It Does Exactly:**
  - Scans workspace source files for AST/regex symbol definitions.
  - Asserts definition file path and exact line number are returned.

### 26. `test_process_message_mcp_protocol`
- **Target Function:** `process_message(request: dict) -> dict`
- **Scenario:** JSON-RPC 2.0 protocol validation.
- **What It Does Exactly:**
  - Tests `initialize` method: asserts protocol version `2024-11-05` and server capabilities.
  - Tests `tools/list` method: asserts all registered tools (`write_to_file`, `replace_file_content`, `run_command`, `trace_symbol`, `ask_local_assistant`) are declared with proper input schemas.

### 27. `test_apply_guardrails_tech_lead_protocol`
- **Target Function:** `apply_guardrails(prompt: str, persona: str) -> str`
- **Scenario:** Verifies system prompts injected into upstream LLMs.
- **What It Does Exactly:**
  - Asserts prompt is augmented with Tech Lead Directives.
  - Asserts instructions to produce deliverables in 1 turn.
  - Asserts instructions to delegate boilerplate/seed data to local Ollama via `ask_local_assistant`.

### 28. `test_ask_local_assistant_circuit_breaker_on_failure`
- **Target Function:** `handle_ask_local_assistant(args)`
- **Scenario:** **Circuit Breaker Protection**. Simulates Ollama network failure (HTTP 504 / Connection Refused).
- **What It Does Exactly:**
  - Simulates an initial failure connecting to Ollama.
  - Asserts `CIRCUIT_BREAKER_TRIPPED` is set to `True`.
  - Makes a second call to `handle_ask_local_assistant`.
  - Asserts immediate short-circuit rejection without attempting network I/O, preventing infinite retry delays.

### 29. `test_write_and_replace_unhandcuffed_large_files`
- **Target Functions:** `handle_write_to_file()`, `handle_replace_file_content()`
- **Scenario:** **Unhandcuffed Execution**. Claude writes a 60-line file and performs a 40-line multi-line replacement.
- **What It Does Exactly:**
  - Writes a 60-line Python file.
  - Asserts the write succeeds without triggering the legacy 30-line limit error.
  - Replaces 40 lines of text in one block using `handle_replace_file_content`.
  - Asserts file content is updated cleanly on disk.

### 30. `test_run_command_sandbox_blocks_exploratory_traversals`
- **Target Function:** `handle_run_command(args)`
- **Scenario:** **Security Sandboxing**. Attempts exploratory command executions (`find / -name "*.env"`, `cat ~/.bash_history`, `ls ~/.gemini`).
- **What It Does Exactly:**
  - Executes commands targeting directories outside `/workspace`.
  - Asserts commands are blocked and return a sandboxing error.

### 31. `test_agy_max_turns_parameter`
- **Target Function:** `execute_agy(prompt, persona, model)`
- **Scenario:** Subprocess invocation of Google Antigravity CLI.
- **What It Does Exactly:**
  - Mocks `subprocess.run` / `Popen` for `agy`.
  - Inspects command arguments array.
  - Asserts `--max-turns 2` is strictly present in the argument list to prevent runaway token spend.

### 32. `test_local_first_triage_forced_local`
- **Target Function:** `process_chat_request(req_data)`
- **Scenario:** **Zero-Cloud-Cost Triage**. Sends requests with `@local`, `@ollama`, or non-coding questions ("Explain what AST is").
- **What It Does Exactly:**
  - Asserts request routes directly to local Ollama.
  - Asserts `agy` cloud CLI is never invoked (**0 cloud tokens consumed**).

### 33. `test_coding_prompt_routes_directly_to_tech_lead_agy`
- **Target Function:** `process_chat_request(req_data)`
- **Scenario:** **Tech Lead Routing**. Passes a concrete coding prompt: `"Write extract_reach5_invalid_emails.py script"`.
- **What It Does Exactly:**
  - Asserts prompt bypasses local Ollama triage.
  - Asserts request dispatches directly to Claude 3.5 Sonnet (`AGY_MODEL=claude-3-5-sonnet`) in `agy`.

### 34. `test_run_command_subshell_and_chain_blocking`
- **Target Function:** `handle_run_command(args)`
- **Scenario:** **Chained Command Security Guard**. Tests attempts to circumvent test runner blocking via shell operators (`echo hello; pytest`, `ls && npm test`, `cat data | pytest`).
- **What It Does Exactly:**
  - Asserts that semicolon (`;`), logical AND (`&&`), and pipe (`|`) operators containing blocked commands are detected and rejected.

### 35. `test_path_traversal_protection`
- **Target Function:** `handle_delete_file(args)`
- **Scenario:** Passes path traversal sequences (`../../etc/passwd`, `../../app/src`).
- **What It Does Exactly:**
  - Asserts that paths attempting to escape `/workspace` are caught and rejected with an error.

### 36. `test_ask_local_assistant_with_target_file`
- **Target Function:** `handle_ask_local_assistant(args)`
- **Scenario:** **Tech Lead Delegation Core**. Claude calls `ask_local_assistant(query="generate disposable domain list", target_file="data/domains.csv")`.
- **What It Does Exactly:**
  - Mocks Ollama returning 50 lines of CSV data.
  - Asserts `handle_ask_local_assistant` auto-persists the generated data directly to `/workspace/data/domains.csv` on disk at **0 cloud tokens**.
  - Asserts disk file exists with exact generated content.

### 37. `test_agy_process_failure_fallback`
- **Target Function:** `process_chat_request(req_data)`
- **Scenario:** **429 Quota Recovery**. Simulates `agy` exiting with exit code 1 and error message `429 Resource exhausted: quota exceeded`.
- **What It Does Exactly:**
  - Asserts the router catches the non-zero exit code.
  - Asserts seamless failover to local Ollama without returning an HTTP 500 error to the client.

### 38. `test_agy_timeout_fallback`
- **Target Function:** `process_chat_request(req_data)`
- **Scenario:** **Timeout Recovery**. Simulates `agy` hanging indefinitely beyond configured timeout.
- **What It Does Exactly:**
  - Triggers `subprocess.TimeoutExpired`.
  - Asserts router terminates the stuck process and falls back to local Ollama.

### 39. `test_auto_persistence_regex_extractor`
- **Target Function:** `auto_persist_code_blocks(text: str) -> list`
- **Scenario:** **Deliverable Persistence Safety Net**. When fallback models output raw markdown code blocks (`### [src/utils/math_helper.py]\n```python...``` `) instead of tool calls.
- **What It Does Exactly:**
  - Parses code block filenames and contents using regex.
  - Asserts target file `/workspace/src/utils/math_helper.py` is written to disk automatically.

### 40. `test_sse_keep_alive_ping_emission`
- **Target Function:** `generate_stream_response(prompt, persona, model)`
- **Scenario:** **Keep-Alive Transport**. Simulates long agent turns (>15 seconds).
- **What It Does Exactly:**
  - Inspects Server-Sent Events (SSE) stream chunks emitted to the client.
  - Asserts periodic `: keep-alive\n\n` comments are sent, preventing Open-WebUI and reverse proxies from dropping idle HTTP connections.

---

## Adding New Tests

When adding new tests:
1. For code compression or token heuristics: add to [`tests/test_ast_compressor.py`](file:///c:/Users/EX4197/scripts/ai-compression-stack/services/router/tests/test_ast_compressor.py).
2. For routing, MCP tools, security sandboxing, or streaming: add to [`tests/test_mcp_workspace.py`](file:///c:/Users/EX4197/scripts/ai-compression-stack/services/router/tests/test_mcp_workspace.py).
3. Validate locally:
   ```bash
   docker exec quota-router python -m unittest discover -s tests
   ```
4. Update this `TEST.md` reference to maintain full documentation traceability.
