#!/usr/bin/env python3
"""
Unit and Integration Tests for AST Code Compressor & Token Optimization Engine (ast_compressor.py).
Validates compression ratios, syntax safety, context preservation, and edge cases across all supported languages.
"""

import unittest
from ast_compressor import (
    compress_python_code,
    compress_python_to_skeleton,
    compress_json,
    compress_c_family_code,
    compress_log_file,
    compress_html_xml,
    compress_css,
    compress_shell_script,
    compress_yaml,
    compress_sql,
    compress_tabular_data,
    compress_markdown,
    compress_aggressive_fallback,
    compress_code_snippet,
    estimate_tokens,
    GlobalStats,
    get_stats
)


class TestASTCompressor(unittest.TestCase):

    def test_estimate_tokens(self):
        sample = "abcd" * 10  # 40 chars -> ~10 tokens
        self.assertEqual(estimate_tokens(sample), 10)
        self.assertEqual(estimate_tokens("a"), 1)  # Minimum 1 token

    def test_compress_python_code(self):
        python_code = '''
"""
This is a module level docstring.
It should be stripped by AST compressor.
"""

# Regular comment outside AST
def calculate_total(price: float, tax_rate: float = 0.05) -> float:
    """
    Calculate the total price including tax.
    :param price: Item price
    :param tax_rate: Tax percentage
    :return: Final price
    """
    tax = price * tax_rate
    return price + tax

class OrderManager:
    """Class docstring to strip."""
    def __init__(self):
        """Init docstring to strip."""
        self.items = []
'''
        compressed = compress_python_code(python_code)
        self.assertNotIn("This is a module level docstring", compressed)
        self.assertNotIn("Calculate the total price including tax", compressed)
        self.assertNotIn("Class docstring to strip", compressed)
        self.assertIn("def calculate_total", compressed)
        self.assertIn("class OrderManager", compressed)

    def test_compress_python_syntax_error_fallback(self):
        invalid_python = "def broken_func(:\n    # comment\n    return 42"
        compressed = compress_python_code(invalid_python)
        self.assertIn("def broken_func", compressed)

    def test_compress_json(self):
        json_data = '''{
            "name": "ai-compression-stack",
            "version": "1.0.0",
            "features": [
                "ast-compression",
                "auto-discovery"
            ],
            "settings": {
                "active": true
            }
        }'''
        compressed = compress_json(json_data)
        self.assertEqual(compressed, '{"name":"ai-compression-stack","version":"1.0.0","features":["ast-compression","auto-discovery"],"settings":{"active":true}}')

    def test_compress_c_family_code_aggressive(self):
        java_code = '''
// Controller implementation
public class UserController {

    /* Constructor injection
       for userService */
    @Autowired
    private UserService userService;

    public ResponseEntity<User> getUser(Long id) {
        return userService.findById(id);
    }
}
'''
        compressed = compress_c_family_code(java_code)
        self.assertNotIn("Controller implementation", compressed)
        self.assertNotIn("Constructor injection", compressed)
        self.assertIn("public class UserController{", compressed)
        # Verify brace newline collapsing
        self.assertIn("private UserService userService;", compressed)

    def test_compress_log_file_spring_boot(self):
        log_content = '''2026-09-12 16:00:00.123 ERROR 1 --- [main] c.e.demo.UserService : Exception processing user
java.lang.NullPointerException: User ID cannot be null
\tat com.example.demo.UserService.getUser(UserService.java:42)
\tat org.springframework.web.servlet.FrameworkServlet.processRequest(FrameworkServlet.java:1014)
\tat org.springframework.web.servlet.FrameworkServlet.doPost(FrameworkServlet.java:918)
\tat jakarta.servlet.http.HttpServlet.service(HttpServlet.java:590)
\tat sun.reflect.NativeMethodAccessorImpl.invoke0(Native Method)
Caused by: java.lang.IllegalArgumentException: Invalid argument passed
\tat com.example.demo.UserRepository.find(UserRepository.java:18)
'''
        compressed = compress_log_file(log_content)
        self.assertIn("java.lang.NullPointerException: User ID cannot be null", compressed)
        self.assertIn("Caused by: java.lang.IllegalArgumentException", compressed)
        self.assertIn("com.example.demo.UserService.getUser", compressed)
        self.assertIn("com.example.demo.UserRepository.find", compressed)
        # Framework reflection frames should be filtered out
        self.assertNotIn("org.springframework.web.servlet.FrameworkServlet", compressed)
        self.assertNotIn("sun.reflect.NativeMethodAccessorImpl", compressed)

    def test_compress_html_xml(self):
        html_code = '''
<!DOCTYPE html>
<html>
    <!-- Header banner comment -->
    <head>
        <title>Test Page</title>
    </head>
    <body>
        <!-- Main container -->
        <h1>Hello World</h1>
    </body>
</html>
'''
        compressed = compress_html_xml(html_code)
        self.assertNotIn("Header banner comment", compressed)
        self.assertNotIn("Main container", compressed)
        self.assertIn("<h1>Hello World</h1>", compressed)

    def test_compress_css(self):
        css_code = '''
/* Global theme variables */
:root {
    --primary-color: #007bff; /* Primary brand color */
}

/* Card layout container */
.card {
    padding: 16px;
    margin: 8px;
}
'''
        compressed = compress_css(css_code)
        self.assertNotIn("Global theme variables", compressed)
        self.assertNotIn("Card layout container", compressed)
        self.assertIn("--primary-color: #007bff;", compressed)
        self.assertIn(".card {", compressed)

    def test_compress_shell_script(self):
        shell_code = '''#!/bin/bash
# System startup script
echo "Starting services..."
# Check disk space
df -h # inline shell comment
'''
        compressed = compress_shell_script(shell_code)
        self.assertTrue(compressed.startswith("#!/bin/bash"))
        self.assertNotIn("System startup script", compressed)
        self.assertNotIn("Check disk space", compressed)
        self.assertIn('echo "Starting services..."', compressed)

    def test_compress_yaml(self):
        yaml_code = '''
# Application Configuration
server:
  port: 8080 # Host port
  # Logging settings
  logging:
    level: DEBUG
'''
        compressed = compress_yaml(yaml_code)
        self.assertNotIn("# Application Configuration", compressed)
        self.assertNotIn("# Logging settings", compressed)
        self.assertIn("server:", compressed)
        self.assertIn("  port: 8080 # Host port", compressed)
        self.assertIn("    level: DEBUG", compressed)

    def test_compress_sql(self):
        sql_code = '''
-- Create user table query
/* Multi-line header comment
   Author: DBA */
SELECT id, username, email
FROM users
WHERE status = 'ACTIVE'; -- Filter active users only
'''
        compressed = compress_sql(sql_code)
        self.assertNotIn("Create user table query", compressed)
        self.assertNotIn("Multi-line header comment", compressed)
        self.assertIn("SELECT id, username, email", compressed)
        self.assertIn("FROM users", compressed)

    def test_compress_tabular_data(self):
        rows = ["id,name,role"] + [f"{i},user_{i},developer" for i in range(1, 30)]
        csv_data = "\n".join(rows)
        compressed = compress_tabular_data(csv_data, max_rows=5)
        self.assertIn("id,name,role", compressed)
        self.assertIn("... [25 rows omitted to conserve tokens; total 30 rows]", compressed)

    def test_compress_markdown(self):
        md_code = '''# Title

<!-- HTML comment in markdown -->

Paragraph 1 text.


Paragraph 2 text after blank lines.
'''
        compressed = compress_markdown(md_code)
        self.assertNotIn("HTML comment in markdown", compressed)
        self.assertNotIn("\n\n\n", compressed)
        self.assertIn("# Title", compressed)

    def test_compress_aggressive_fallback(self):
        txt_code = '''#!/usr/bin/env python
# Header comment
==================================================
        def indented_func():
            # Deeply indented comment
            return True
--------------------------------------------------
'''
        compressed = compress_aggressive_fallback(txt_code)
        self.assertTrue(compressed.startswith("#!/usr/bin/env python"))
        self.assertNotIn("Header comment", compressed)
        self.assertIn("===", compressed)  # Divider collapsed to 3 chars
        self.assertNotIn("==================================================", compressed)

    def test_compress_code_snippet_dispatcher(self):
        python_snippet = '"""Module docstring"""\ndef foo():\n    pass'
        compressed, orig_tok, comp_tok = compress_code_snippet(python_snippet, filename="script.py")
        self.assertNotIn("Module docstring", compressed)
        self.assertGreaterEqual(orig_tok, comp_tok)

        log_snippet = "2026-09-12 ERROR NullPointerException\n\tat org.springframework.web.servlet.FrameworkServlet.doPost"
        comp_log, orig_l_tok, comp_l_tok = compress_code_snippet(log_snippet, filename="app.log")
        self.assertIn("NullPointerException", comp_log)
        self.assertNotIn("org.springframework", comp_log)

        stats = get_stats()
        self.assertGreater(stats["total_requests"], 0)
        self.assertIn("savings_percentage", stats)

    def test_compress_python_to_skeleton(self):
        from ast_compressor import compress_python_to_skeleton
        full_code = (
            "class UserService:\n"
            "    def __init__(self, db: Database):\n"
            "        self.db = db\n"
            "        self.cache = {}\n\n"
            "    def get_user(self, user_id: int) -> dict:\n"
            "        if user_id in self.cache:\n"
            "            return self.cache[user_id]\n"
            "        user = self.db.find_one({'id': user_id})\n"
            "        self.cache[user_id] = user\n"
            "        return user\n"
        )
        skeleton = compress_python_to_skeleton(full_code)
        self.assertIn("class UserService", skeleton)
        self.assertIn("def get_user(self, user_id: int) -> dict:", skeleton)
        # Function body should be collapsed to Ellipsis (...)
        self.assertIn("...", skeleton)
        self.assertNotIn("self.db.find_one", skeleton)
        self.assertLess(len(skeleton), len(full_code))

    def test_compress_binary_and_non_utf8_files(self):
        # Binary data decoded with replace should pass through safely without crashing
        binary_data = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        try:
            text = binary_data.decode("utf-8", errors="replace")
            res, orig_tok, comp_tok = compress_code_snippet(text, filename="image.png")
            self.assertGreater(orig_tok, 0)
        except Exception as e:
            self.fail(f"Compressor should not crash on binary data: {e}")

    def test_ast_skeletonizer_complex_python_features(self):
        complex_py = (
            "@dataclass\n"
            "class Config:\n"
            "    host: str = 'localhost'\n\n"
            "class AsyncService:\n"
            "    @property\n"
            "    def is_active(self) -> bool:\n"
            "        return True\n\n"
            "    async def fetch_data(self, url: str) -> dict:\n"
            "        return {'status': 200}\n"
        )
        skeleton = compress_python_to_skeleton(complex_py)
        self.assertIn("@dataclass", skeleton)
        self.assertIn("class Config", skeleton)
        self.assertIn("class AsyncService", skeleton)
        self.assertIn("async def fetch_data", skeleton)
        self.assertIn("...", skeleton)

    def test_ast_compressor_empty_and_whitespace_files(self):
        empty_res, _, _ = compress_code_snippet("", filename="empty.py")
        self.assertEqual(empty_res, "")

        ws_res, _, _ = compress_code_snippet("   \n\n   \t\n", filename="ws.py")
        self.assertEqual(ws_res, "")


if __name__ == "__main__":
    unittest.main()
