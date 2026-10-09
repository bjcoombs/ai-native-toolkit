# CLAUDE.md

Guidance for every contributor, human or agent, working in this repository.

## Project overview

The orders API accepts, prices and settles customer orders for the storefront.
It is a TypeScript service built on a small HTTP framework and a Postgres store.
Orders move through the states placed, priced, paid, shipped and settled.
Every state change is written to the audit log before the response returns.
The pricing engine applies promotions in priority order and never stacks two.
Settlement runs nightly and reconciles payments against the ledger service.

## Directory structure

```
orders-api/
├── src/
│   ├── app.ts
│   ├── routes/
│   │   └── index.ts
│   ├── pricing/
│   └── settlement/
├── tests/
├── docs/
└── package.json
```

## About this project

The orders API accepts, prices and settles customer orders for the storefront.
It is a TypeScript service built on a small HTTP framework and a Postgres store.
Orders move through the states placed, priced, paid, shipped and settled.
Every state change is written to the audit log before the response returns.

## Naming

- Use clear, consistent conventions for naming in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for naming in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for naming in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for naming in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for naming in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for naming in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for naming in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for naming in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for naming in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for naming in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for naming in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for naming in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for naming in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for naming in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for naming in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for naming in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for naming in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for naming in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Errors

- Prefer clear, consistent conventions for errors in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for errors in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for errors in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for errors in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for errors in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for errors in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for errors in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for errors in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for errors in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for errors in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for errors in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for errors in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for errors in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for errors in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for errors in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for errors in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for errors in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for errors in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Logging

- Choose clear, consistent conventions for logging in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for logging in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for logging in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for logging in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for logging in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for logging in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for logging in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for logging in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for logging in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for logging in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for logging in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for logging in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for logging in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for logging in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for logging in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for logging in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for logging in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for logging in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Testing

- Default to clear, consistent conventions for testing in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for testing in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for testing in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for testing in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for testing in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for testing in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for testing in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for testing in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for testing in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for testing in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for testing in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for testing in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for testing in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for testing in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for testing in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for testing in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for testing in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for testing in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Security

- Match clear, consistent conventions for security in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for security in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for security in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for security in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for security in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for security in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for security in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for security in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for security in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for security in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for security in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for security in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for security in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for security in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for security in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for security in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for security in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for security in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Performance

- Add clear, consistent conventions for performance in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for performance in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for performance in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for performance in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for performance in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for performance in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for performance in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for performance in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for performance in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for performance in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for performance in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for performance in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for performance in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for performance in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for performance in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for performance in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for performance in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for performance in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Reviews

- Run clear, consistent conventions for reviews in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for reviews in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for reviews in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for reviews in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for reviews in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for reviews in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for reviews in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for reviews in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for reviews in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for reviews in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for reviews in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for reviews in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for reviews in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for reviews in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for reviews in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for reviews in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for reviews in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for reviews in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Documentation

- Use clear, consistent conventions for documentation in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for documentation in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for documentation in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for documentation in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for documentation in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for documentation in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for documentation in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for documentation in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for documentation in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for documentation in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for documentation in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for documentation in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for documentation in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for documentation in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for documentation in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for documentation in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for documentation in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for documentation in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Dependencies

- Prefer clear, consistent conventions for dependencies in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for dependencies in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for dependencies in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for dependencies in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for dependencies in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for dependencies in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for dependencies in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for dependencies in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for dependencies in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for dependencies in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for dependencies in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for dependencies in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for dependencies in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for dependencies in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for dependencies in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for dependencies in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for dependencies in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for dependencies in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Refactoring

- Choose clear, consistent conventions for refactoring in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for refactoring in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for refactoring in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for refactoring in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for refactoring in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for refactoring in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for refactoring in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for refactoring in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for refactoring in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for refactoring in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for refactoring in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for refactoring in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for refactoring in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for refactoring in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for refactoring in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for refactoring in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for refactoring in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for refactoring in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Apis

- Default to clear, consistent conventions for APIs in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for APIs in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for APIs in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for APIs in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for APIs in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for APIs in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for APIs in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for APIs in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for APIs in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for APIs in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for APIs in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for APIs in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for APIs in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for APIs in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for APIs in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for APIs in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for APIs in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for APIs in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Data access

- Match clear, consistent conventions for data access in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for data access in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for data access in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for data access in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for data access in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for data access in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for data access in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for data access in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for data access in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for data access in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for data access in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for data access in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for data access in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for data access in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for data access in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for data access in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for data access in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for data access in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Caching

- Add clear, consistent conventions for caching in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for caching in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for caching in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for caching in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for caching in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for caching in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for caching in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for caching in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for caching in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for caching in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for caching in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for caching in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for caching in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for caching in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for caching in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for caching in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for caching in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for caching in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Configuration

- Run clear, consistent conventions for configuration in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for configuration in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for configuration in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for configuration in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for configuration in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for configuration in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for configuration in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for configuration in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for configuration in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for configuration in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for configuration in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for configuration in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for configuration in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for configuration in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for configuration in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for configuration in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for configuration in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for configuration in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Observability

- Use clear, consistent conventions for observability in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for observability in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for observability in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for observability in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for observability in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for observability in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for observability in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for observability in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for observability in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for observability in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for observability in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for observability in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for observability in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for observability in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for observability in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for observability in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for observability in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for observability in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Accessibility

- Prefer clear, consistent conventions for accessibility in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for accessibility in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for accessibility in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for accessibility in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for accessibility in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for accessibility in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for accessibility in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for accessibility in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for accessibility in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for accessibility in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for accessibility in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for accessibility in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for accessibility in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for accessibility in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for accessibility in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for accessibility in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for accessibility in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for accessibility in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Concurrency

- Choose clear, consistent conventions for concurrency in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for concurrency in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for concurrency in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for concurrency in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for concurrency in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for concurrency in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for concurrency in every module (rule 7) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for concurrency in every module (rule 8) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for concurrency in every module (rule 9) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for concurrency in every module (rule 10) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for concurrency in every module (rule 11) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for concurrency in every module (rule 12) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for concurrency in every module (rule 13) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for concurrency in every module (rule 14) because consistency over cleverness keeps the codebase maintainable.
- Choose clear, consistent conventions for concurrency in every module (rule 15) because consistency over cleverness keeps the codebase maintainable.
- Default to clear, consistent conventions for concurrency in every module (rule 16) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for concurrency in every module (rule 17) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for concurrency in every module (rule 18) because consistency over cleverness keeps the codebase maintainable.

## Releases

- Default to clear, consistent conventions for releases in every module (rule 1) because consistency over cleverness keeps the codebase maintainable.
- Match clear, consistent conventions for releases in every module (rule 2) because consistency over cleverness keeps the codebase maintainable.
- Add clear, consistent conventions for releases in every module (rule 3) because consistency over cleverness keeps the codebase maintainable.
- Run clear, consistent conventions for releases in every module (rule 4) because consistency over cleverness keeps the codebase maintainable.
- Use clear, consistent conventions for releases in every module (rule 5) because consistency over cleverness keeps the codebase maintainable.
- Prefer clear, consistent conventions for releases in every module (rule 6) because consistency over cleverness keeps the codebase maintainable.
