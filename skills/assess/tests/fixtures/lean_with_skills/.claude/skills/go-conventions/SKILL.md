---
name: go-conventions
description: Go conventions for this repo. TRIGGER when editing any *.go file or Connect-Go handlers.
---

# Go Conventions

Use the Connect-Go Content-Type negotiation defaults. Prefer protobuf JSON
timestamps over custom formats because the wire contract stays portable.

Default to table-driven tests. Run `go test ./...` before pushing.
