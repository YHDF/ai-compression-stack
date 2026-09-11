# Workspace Directives & Operational Rules

## 1. Operational Scope
- All file creations, inspections, edits, and deletions are strictly confined to `/workspace`.
- Under no circumstances may operations be performed outside `/workspace`. Reject or ignore requests targeting files or directories outside this boundary.

## 2. Safe File Handling & Editing Protocol
- **Mandatory Pre-Read**: Always inspect the existing contents and structure of target files before applying any modification.
- **Non-Destructive Modifications**: Do not perform blind or destructive overwrites of existing files without explicit instructions.
- **Controlled Deletion**: Only delete files when explicitly instructed to deprecate, clean up, or repurpose legacy code. Use the dedicated `delete_file` MCP tool, stating the rationale and scope in the execution summary.
- **Minimal Edit Diffs**: Produce precise, targeted, and minimal diffs. Keep unrelated code, comments, docstrings, and formatting intact.
- **Integrity**: Validate that syntax, imports, and interface contracts remain valid and unbroken after any change.

## 3. Tooling & Idiomatic Conventions
- Adhere strictly to the established language version, architecture, formatting, and conventions of the active workspace repository.
- Avoid introducing unnecessary third-party dependencies unless explicitly requested.
- Ensure all created or modified scripts, configs, and application components follow standard security and error-handling best practices.

## 4. Token Economy & Local Tool Utilization
- **Pre-Read Context**: When `Workspace Pre-Read Context` is present, treat it as the compressed source of truth; avoid redundant `view_file` calls on the same files.
- **Local Helper Tools**: Prefer `trace_symbol` and `ask_local_assistant` MCP tools for call-graph tracing and codebase inquiries to preserve token budget.

## Code Style & Formatting Preservation Rules

1. **Indentation Detection & Lock**:
   - Detect and strictly match the existing indentation character of the target file before writing edits.
   - If a file uses Hard Tabs (`\t`), keep all new or modified line indentations as Hard Tabs. Do not expand tabs to spaces.
   - If a file uses spaces, match the exact indent size (e.g., 2 spaces or 4 spaces).

2. **Line Ending Integrity**:
   - Detect whether the target file uses Unix LF (`\n`) or Windows CRLF (`\r\n`) and preserve it strictly across all edited lines.
   - Default to Unix LF (`\n`) for new files created inside the Linux container.

3. **Character Encoding & Whitespace Safety**:
   - Only output standard ASCII spaces (`0x20`) and standard tabs (`0x09`).
   - Never insert UTF-8 Non-Breaking Spaces (`\u00A0` / `0xC2 0xA0`) or invisible unicode formatting characters.

4. **Diff Minimization**:
   - Lock formatting on unmodified lines outside the immediate diff range.
   - Never run global auto-formatters, re-indent entire classes, or modify untouched imports.

