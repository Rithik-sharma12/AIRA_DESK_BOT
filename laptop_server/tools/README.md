# tools/ — Phase 3 (Agent engine)

Allowlisted tool registry: each tool is a Python function with a Pydantic parameter
schema, a risk level (`read_only` / `sensitive` / `destructive`), a timeout, and an
audit-log entry per call. Sensitive and destructive tools require spoken or on-screen
confirmation. No tool may run arbitrary shell or PowerShell text.
