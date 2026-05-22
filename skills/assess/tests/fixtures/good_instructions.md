# Project Guidelines

## Approach

- Use `bcrypt` for password hashing because timing-safe comparison matters.
- Prefer Postgres over SQLite for production: we need concurrent writers.
- Default to constructor injection in `src/auth/` over field injection.

## When editing `src/payments/processor.py`

- Match the existing transaction pattern in `src/payments/refund.py`.
- Add the corresponding test in `tests/payments/test_processor.py`.
- The reconciler runs every 5 minutes; idempotency is required.

## Working if

- Diffs touch only files mentioned in the task.
- New code follows the patterns in `src/auth/login.py`.
- Tests run green before opening a PR.
