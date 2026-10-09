# CLAUDE.md

Repo-specific rules for agents editing orders-api.

## Commands

Run these from the repository root. Each one is what CI runs.

```bash
npm test            # unit tests (vitest)
npm run lint        # eslint, zero warnings allowed
npm run typecheck   # tsc --noEmit
npm run build       # emits dist/
make check          # lint + test, the pre-push gate
```

Format before committing: `make fmt`.

## Where things live

- HTTP handlers: `src/routes/index.ts`.
- Application wiring: `src/app.ts`.
- Tests sit beside the code they cover in `tests/`, e.g. `tests/app.test.ts`.

## Rules

- Use the existing route table in `src/routes/index.ts` rather than
  registering handlers ad hoc, because the table drives the OpenAPI export.
- Prefer a new test file over growing an existing one past 300 lines.
- Add a test with every behaviour change; `npm test` must pass.
- Default to returning typed errors instead of throwing from a handler.

## Before opening a PR

1. `make check` passes.
2. `npm run typecheck` passes.
3. The diff touches only files the task names.

## Gotchas

- `npm run build` writes to `dist/`, which is gitignored; never commit it.
- The pricing engine never stacks two promotions; keep it that way.
- Settlement is nightly, so a change there needs a reconciliation test.

## Out of scope

- Do not edit generated OpenAPI files by hand.
- Do not add runtime dependencies without a reason in the PR body.

## Verifying a change

- Run the narrowest test first: `npm test -- tests/app.test.ts`.
- Then the full gate: `make check`.
- A green `make check` is the definition of done.

## Contacts

- Owners are listed in the repository settings.
- Ask before changing the audit log format.
