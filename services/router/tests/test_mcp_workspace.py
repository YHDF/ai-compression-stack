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
        self.patcher = patch.object(mcp_workspace, 'WORKSPACE_ROOT', str(self.test_workspace.resolve()))
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
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
        mock_response.read.return_value = b'{"response": "Here is the drafted mock function:\\n```python\\ndef mock_user():\\n    return {\\"id\\": 1}\\n```"}'
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

    def test_apply_guardrails_mandates_local_ollama(self):
        prompt = "Add JWT token validation to AuthManager"
        guarded = apply_guardrails(prompt, "## Workspace Pre-Read Context:\nFile: auth.py")
        
        # Verify mandatory local inquiries & zero search loops are enforced
        self.assertIn("ask_local_assistant", guarded)
        self.assertIn("trace_symbol", guarded)
        self.assertIn("Absolute Test Prohibition", guarded)
        self.assertIn("Zero Search Loops", guarded)


if __name__ == "__main__":
    unittest.main()
