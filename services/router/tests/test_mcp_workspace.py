#!/usr/bin/env python3
"""
Unit and Integration Tests for MCP Workspace Server & Local Ollama Assistant (mcp_workspace.py).
Verifies local code retrieval, ask_local_assistant, trace_symbol, test execution interceptors, and guardrail enforcement.
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

from mcp_workspace import (
    normalize_path,
    handle_write_to_file,
    handle_replace_file_content,
    handle_delete_file,
    handle_run_command,
    retrieve_local_workspace_context,
    handle_ask_local_assistant,
    handle_trace_symbol,
    process_message
)
from app import apply_guardrails


class TestMCPWorkspace(unittest.TestCase):

    def setUp(self):
        # Setup temporary workspace directory for testing
        self.test_workspace = Path("/tmp/test_workspace_mcp") if os.name != 'nt' else Path(os.getenv("TEMP", "C:\\Temp")) / "test_workspace_mcp"
        self.test_workspace.mkdir(parents=True, exist_ok=True)
        os.environ["WORKSPACE_DIR"] = str(self.test_workspace)
        import mcp_workspace
        import app
        mcp_workspace._local_assistant_status["failures"] = 0
        mcp_workspace._local_assistant_status["last_error"] = None
        self.patcher = patch.object(mcp_workspace, 'WORKSPACE_ROOT', str(self.test_workspace.resolve()))
        self.patcher.start()
        self.app_patcher = patch.object(app, 'WORKSPACE_DIR', str(self.test_workspace.resolve()))
        self.app_patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.app_patcher.stop()
        # Cleanup temporary files
        if self.test_workspace.exists():
            import shutil
            shutil.rmtree(self.test_workspace, ignore_errors=True)

    def test_normalize_path_security(self):
        p = normalize_path("services/auth/user.py")
        self.assertTrue(str(p).startswith(str(self.test_workspace.resolve())))

    def test_handle_write_and_delete_file(self):
        file_path = "src/components/button.tsx"
        content = "export const Button = () => <button>Click</button>;"
        
        # Test write_to_file
        res_write = handle_write_to_file({"path": file_path, "content": content})
        self.assertNotIn("isError", res_write)
        created_file = self.test_workspace / file_path
        self.assertTrue(created_file.exists())
        self.assertEqual(created_file.read_text(encoding="utf-8"), content)

        # Test delete_file
        res_del = handle_delete_file({"path": file_path})
        self.assertNotIn("isError", res_del)
        self.assertFalse(created_file.exists())

    def test_handle_run_command_blocks_test_runners(self):
        # Verify strict token preservation: test execution commands must be blocked
        blocked_commands = [
            # Java Ecosystem
            "mvn test",
            "mvn verify",
            "./mvnw test",
            "mvn test -Dtest=UserControllerTest",
            "gradle test",
            "./gradlew test --tests UserServiceTest",

            # JS / TS Ecosystem
            "npm test",
            "npm run test:unit",
            "yarn test",
            "pnpm test",
            "bun test",
            "jest",
            "npx jest",
            "vitest",
            "npx vitest run",
            "mocha",
            "npx playwright test",
            "npx cypress run",

            # Python, Go, Rust, .NET
            "pytest",
            "python -m unittest discover",
            "go test ./...",
            "cargo test",
            "dotnet test"
        ]
        for cmd in blocked_commands:
            res = handle_run_command({"command": cmd})
            self.assertTrue(res.get("isError"), f"Command '{cmd}' should have been blocked")
            self.assertIn("Execution Blocked", res["content"][0]["text"])

    def test_retrieve_local_workspace_context(self):
        # Create sample workspace files
        py_file = self.test_workspace / "token_counter.py"
        py_file.write_text("class TokenCounter:\n    def count_tokens(self, text: str) -> int:\n        return len(text) // 4\n", encoding="utf-8")

        java_file = self.test_workspace / "UserService.java"
        java_file.write_text("public class UserService {\n    public User findUserById(Long id) {\n        return null;\n    }\n}\n", encoding="utf-8")

        # Search for TokenCounter keyword
        context = retrieve_local_workspace_context("How does TokenCounter work?")
        self.assertIn("token_counter.py", context)
        self.assertIn("class TokenCounter", context)

        # Search for UserService keyword
        context_java = retrieve_local_workspace_context("Explain findUserById in UserService")
        self.assertIn("UserService.java", context_java)
        self.assertIn("findUserById", context_java)

    @patch("urllib.request.urlopen")
    def test_handle_ask_local_assistant_ollama_mock(self, mock_urlopen):
        # Mock local Ollama HTTP response
        mock_response = MagicMock()
        mock_response.__iter__.return_value = [b'{"response": "Here is the drafted mock function:\\n```python\\ndef mock_user():\\n    return {\\"id\\": 1}\\n```", "done": true}\n']
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        res = handle_ask_local_assistant({"query": "Draft a mock user helper function"})
        self.assertNotIn("isError", res)
        answer_text = res["content"][0]["text"]
        self.assertIn("Local Ollama", answer_text)
        self.assertIn("mock_user", answer_text)

    def test_handle_trace_symbol_python_and_java(self):
        # Create Python file with symbol definition
        py_file = self.test_workspace / "order_service.py"
        py_file.write_text("class OrderService:\n    def process_order(self, order_id: str):\n        pass\n", encoding="utf-8")

        # Create Java file with symbol definition
        java_file = self.test_workspace / "OrderController.java"
        java_file.write_text("public class OrderController {\n    @Autowired\n    private OrderService orderService;\n}\n", encoding="utf-8")

        # Trace OrderService symbol
        res = handle_trace_symbol({"symbol": "OrderService"})
        text = res["content"][0]["text"]
        self.assertIn("Symbol Trace: 'OrderService'", text)
        self.assertIn("order_service.py", text)
        self.assertIn("OrderController.java", text)

    def test_process_message_mcp_protocol(self):
        # Test MCP initialize
        init_req = {"id": 1, "method": "initialize"}
        res_init = process_message(init_req)
        self.assertEqual(res_init["result"]["protocolVersion"], "2024-11-05")

        # Test tools/list
        list_req = {"id": 2, "method": "tools/list"}
        res_list = process_message(list_req)
        tool_names = [t["name"] for t in res_list["result"]["tools"]]
        self.assertIn("ask_local_assistant", tool_names)
        self.assertIn("trace_symbol", tool_names)
        self.assertIn("run_command", tool_names)
        self.assertIn("write_to_file", tool_names)

    def test_apply_guardrails_tech_lead_protocol(self):
        prompt = "Add JWT token validation to AuthManager"
        guarded = apply_guardrails(prompt, "## Workspace Pre-Read Context:\nFile: auth.py")
        
        # Verify Tech Lead directives, tool delegation, and test prohibitions are enforced
        self.assertIn("TECH LEAD", guarded)
        self.assertIn("ask_local_assistant", guarded)
        self.assertIn("trace_symbol", guarded)
        self.assertIn("Absolute Test Prohibition", guarded)
        self.assertIn("1-TURN CONVERGENCE", guarded)

    @patch("urllib.request.urlopen")
    def test_ask_local_assistant_circuit_breaker_on_failure(self, mock_urlopen):
        import mcp_workspace
        # Simulate an Ollama timeout / network error
        mock_urlopen.side_effect = Exception("HTTP 504 Gateway Timeout")

        # First call fails and activates circuit breaker
        res = handle_ask_local_assistant({"query": "Draft a handler"})
        self.assertTrue(res.get("isError"))
        self.assertIn("CIRCUIT BREAKER", res["content"][0]["text"])
        self.assertEqual(mcp_workspace._local_assistant_status["failures"], 1)

        # Subsequent call is immediately blocked without reaching network
        res2 = handle_ask_local_assistant({"query": "Draft a handler retry"})
        self.assertTrue(res2.get("isError"))
        self.assertIn("CIRCUIT BREAKER ACTIVE", res2["content"][0]["text"])

    def test_write_and_replace_unhandcuffed_large_files(self):
        # Claude is now unhandcuffed: large files (>30 lines) are written without error
        large_code = "\n".join([f"line_{i} = {i}" for i in range(50)])
        res_write = handle_write_to_file({"path": "large_file.py", "content": large_code})
        self.assertNotIn("isError", res_write)
        created = self.test_workspace / "large_file.py"
        self.assertTrue(created.exists())
        self.assertEqual(len(created.read_text(encoding="utf-8").splitlines()), 50)

        # Large replace is also unhandcuffed
        res_replace = handle_replace_file_content({
            "path": "large_file.py",
            "TargetContent": "line_0 = 0",
            "ReplacementContent": "line_0 = 'replaced_val'\nline_0_extra = True"
        })
        self.assertNotIn("isError", res_replace)
        self.assertIn("replaced_val", created.read_text(encoding="utf-8"))

    def test_run_command_sandbox_blocks_exploratory_traversals(self):
        # find / and root searches must be blocked
        res_find = handle_run_command({"command": "find / -name '*domain*.csv'"})
        self.assertTrue(res_find.get("isError"))
        self.assertIn("Exploratory system search", res_find["content"][0]["text"])

        # History inspection must be blocked
        res_hist = handle_run_command({"command": "cat ~/.bash_history"})
        self.assertTrue(res_hist.get("isError"))
        self.assertIn("Exploratory system search", res_hist["content"][0]["text"])

        # Brain inspection must be blocked
        res_brain = handle_run_command({"command": "ls /home/appuser/.gemini/antigravity-cli/brain/"})
        self.assertTrue(res_brain.get("isError"))
        self.assertIn("Exploratory system search", res_brain["content"][0]["text"])

    @patch("subprocess.run")
    def test_agy_max_turns_parameter(self, mock_sub):
        from app import execute_agy
        mock_sub.return_value = MagicMock(returncode=0, stdout="Done", stderr="")
        with patch("shutil.which", return_value="/bin/agy"), patch("os.path.exists", return_value=True), patch("os.access", return_value=True):
            execute_agy("Test prompt")
            mock_sub.assert_called_once()
            called_cmd = mock_sub.call_args[0][0]
            self.assertIn("--max-turns", called_cmd)
            idx = called_cmd.index("--max-turns")
            self.assertEqual(called_cmd[idx + 1], "2")

    @patch("requests.post")
    @patch("app.call_ollama_generation")
    @patch("app.execute_agy")
    def test_local_first_triage_forced_local(self, mock_agy, mock_ollama, mock_post):
        import json
        from app import process_chat_request, ChatCompletionRequest, ChatMessage
        mock_ollama.return_value = "### [data/test.csv]\ncol1,col2\nval1,val2"
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "response": json.dumps({
                "complexity": "trivial",
                "target_files": ["data/test.csv"],
                "beautified_prompt": "Create data/test.csv"
            })
        }
        mock_post.return_value = mock_resp

        # Explicit @local prefix triggers Ollama triage directly
        req = ChatCompletionRequest(
            model="coder",
            messages=[ChatMessage(role="user", content="@local generate sample domain data for data/test.csv")]
        )
        res = process_chat_request(req)
        mock_agy.assert_not_called()
        mock_ollama.assert_called_once()
        self.assertIn("Local Triage: Ollama", res)


    @patch("requests.post")
    @patch("app.call_ollama_generation")
    @patch("app.execute_agy")
    def test_coding_prompt_routes_directly_to_tech_lead_agy(self, mock_agy, mock_ollama, mock_post):
        import json
        from app import process_chat_request, ChatCompletionRequest, ChatMessage
        # Mock Ollama triage response
        mock_resp = MagicMock(status_code=200)
        mock_resp.json.return_value = {
            "response": json.dumps({
                "complexity": "trivial",
                "target_files": ["calc.py"],
                "beautified_prompt": "Write calc.py"
            })
        }
        mock_post.return_value = mock_resp

        # Mock agy resolving it cleanly as Tech Lead
        mock_agy.return_value = "Implemented calc.py properly."

        req = ChatCompletionRequest(
            model="coder",
            messages=[ChatMessage(role="user", content="Write a calc.py script to add two numbers")]
        )
        res = process_chat_request(req)

        # agy MUST have been called directly because it is a coding task
        mock_agy.assert_called_once()
        mock_ollama.assert_not_called()
        self.assertIn("Agent Task: Coder", res)

    def test_run_command_subshell_and_chain_blocking(self):
        # Chained commands attempting to sneak past test runner blocks
        chained_cmds = [
            "echo hello; pytest",
            "ls && npm test",
            "echo 'data' | pytest",
            "true || cargo test"
        ]
        for cmd in chained_cmds:
            res = handle_run_command({"command": cmd})
            self.assertTrue(res.get("isError"), f"Chained command '{cmd}' should have been blocked")
            self.assertIn("Execution Blocked", res["content"][0]["text"])

    def test_path_traversal_protection(self):
        # Traversal attempts outside workspace must be blocked safely
        res_del = handle_delete_file({"path": "../../etc/passwd"})
        self.assertTrue("Security Error" in str(res_del) or "No files" in str(res_del))

    @patch("urllib.request.urlopen")
    def test_ask_local_assistant_with_target_file(self, mock_urlopen):
        # Mock local Ollama generating CSV seed data
        mock_response = MagicMock()
        mock_response.__iter__.return_value = [b'{"response": "col1,col2\\nval1,val2\\nval3,val4", "done": true}\n']
        mock_response.__enter__.return_value = mock_response
        mock_urlopen.return_value = mock_response

        target_csv = "data/domains/test_domains.csv"
        res = handle_ask_local_assistant({
            "query": "Generate sample domain CSV data",
            "target_file": target_csv
        })
        self.assertNotIn("isError", res)
        self.assertIn("Successfully generated and saved", res["content"][0]["text"])
        saved_file = self.test_workspace / target_csv
        self.assertTrue(saved_file.exists())
        self.assertIn("val1,val2", saved_file.read_text(encoding="utf-8"))

    @patch("app.call_ollama_generation")
    @patch("subprocess.run")
    def test_agy_process_failure_fallback(self, mock_sub, mock_ollama):
        from app import process_chat_request, ChatCompletionRequest, ChatMessage
        # Mock agy exiting with code 1 (or 429 quota exhausted)
        mock_sub.return_value = MagicMock(returncode=1, stdout="", stderr="429 Resource exhausted: quota exceeded")
        mock_ollama.return_value = "Ollama fallback response"

        req = ChatCompletionRequest(
            model="coder",
            messages=[ChatMessage(role="user", content="Fix the authentication handler in auth.py")]
        )
        res = process_chat_request(req)
        # Should cleanly fall back to local Ollama without crashing
        mock_ollama.assert_called_once()
        self.assertIn("Fallback: Local Ollama", res)

    @patch("app.call_ollama_generation")
    @patch("subprocess.run")
    def test_agy_timeout_fallback(self, mock_sub, mock_ollama):
        import subprocess
        from app import process_chat_request, ChatCompletionRequest, ChatMessage
        # Mock agy timing out
        mock_sub.side_effect = subprocess.TimeoutExpired(cmd="agy", timeout=180)
        mock_ollama.return_value = "Ollama timeout fallback"

        req = ChatCompletionRequest(
            model="coder",
            messages=[ChatMessage(role="user", content="Refactor the user model in user.py")]
        )
        res = process_chat_request(req)
        mock_ollama.assert_called_once()
        self.assertIn("Fallback: Local Ollama", res)

    def test_auto_persistence_regex_extractor(self):
        from app import auto_persist_code_blocks
        ollama_output = (
            "Here is the implementation:\n"
            "### [src/utils/math_helper.py]\n"
            "```python\n"
            "def add_numbers(x: int, y: int) -> int:\n"
            "    return x + y\n"
            "```\n"
        )
        saved = auto_persist_code_blocks(ollama_output, str(self.test_workspace))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0], "src/utils/math_helper.py")
        persisted_file = self.test_workspace / "src" / "utils" / "math_helper.py"
        self.assertTrue(persisted_file.exists())
        self.assertIn("def add_numbers", persisted_file.read_text(encoding="utf-8"))

    @patch("app.process_chat_request")
    def test_sse_keep_alive_ping_emission(self, mock_process):
        import time
        from app import generate_stream_response, ChatCompletionRequest, ChatMessage
        # Mock slow process that sleeps 2.2 seconds
        def slow_worker(req):
            time.sleep(2.2)
            return "Final answer from slow agent"
        mock_process.side_effect = slow_worker

        req = ChatCompletionRequest(
            model="coder",
            messages=[ChatMessage(role="user", content="Slow prompt")],
            stream=True
        )
        stream_res = generate_stream_response(req)
        import asyncio
        async def collect():
            res = []
            async for chunk in stream_res.body_iterator:
                res.append(chunk if isinstance(chunk, str) else chunk.decode("utf-8"))
            return "".join(res)
        full_stream = asyncio.run(collect())
        # Verify initial role chunk, keep-alive ping, and final content
        self.assertIn('"role": "assistant"', full_stream)
        self.assertIn(": keep-alive\n\n", full_stream)
        self.assertIn("Final answer from slow agent", full_stream)
        self.assertIn("data: [DONE]", full_stream)


if __name__ == "__main__":
    unittest.main()
