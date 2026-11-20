# happy-doc-vs-doc-setting

Holds `/doc-contradictions` to finding the simplest conflict between two documents: two pages state different values for the same setting.

- **Input:** [`tests/fixtures/doc-vs-doc-setting/`](../../../skills/doc-contradictions/tests/fixtures/doc-vs-doc-setting/README.md). It is authored for this case and is not drawn from a recorded run.
- **Planted contradiction:** `docs/configuration.md:13` sets `query_timeout_seconds` to 30, and `docs/troubleshooting.md:9` says it is 120. No code file defines the setting, so the case can only be passed doc-against-doc.
- **Rubric:** [`graders/criteria.md`](./graders/criteria.md).
