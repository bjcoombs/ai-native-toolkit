# orders-api

The orders API accepts, prices and settles customer orders for the storefront.
It is a TypeScript service built on a small HTTP framework and a Postgres store.
Orders move through the states placed, priced, paid, shipped and settled.
Every state change is written to the audit log before the response returns.
The pricing engine applies promotions in priority order and never stacks two.
Settlement runs nightly and reconciles payments against the ledger service.
