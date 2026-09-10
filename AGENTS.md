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

