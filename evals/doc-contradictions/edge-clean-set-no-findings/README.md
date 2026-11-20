# edge-clean-set-no-findings

The false-positive guard. Holds `/doc-contradictions` to reporting nothing when the docs agree with each other and with the code but say so in different words and units.

- **Input:** [`tests/fixtures/clean-set/`](../../../skills/doc-contradictions/tests/fixtures/clean-set/README.md). It is authored for this case and is not drawn from a recorded run.
- **Planted agreement:** the upload limit, the request timeout and the worker count, each stated two or three ways (words, numbers, a multiplication, a code constant). Nothing contradicts.
- **Rubric:** [`graders/criteria.md`](./graders/criteria.md).
