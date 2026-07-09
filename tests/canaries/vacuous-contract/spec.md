# Spec: log-scrubber (vacuous-contract fixture)

## Purpose

This fixture exists to be **rejected at freeze**. Its spec is ordinary; its
`contract.md` is the failure mode - it contains a criterion satisfiable by
*absence* (the jet-fighters pathology). The red-contract-first kill-test (B1)
must catch it: the criterion passes against the class null artifact, so the
contract is vacuous and is kicked back to authoring (canary criterion 3). No
build ships with this fixture - the assertion is entirely at the freeze gate.

## The (ordinary) deliverable it describes

`log-scrubber` is a CLI-class tool that reads an application log on stdin and
writes a cleaned log to `output.log`, dropping lines that match a redaction
pattern. A real contract for it would assert, on real input, that specific
sensitive lines are removed and specific benign lines are retained (a
do-and-observe against actual content).

## Why the shipped contract is vacuous

Instead of asserting what the tool *does* to real input, `contract.md` asserts a
property satisfiable by producing nothing at all: "no error-level lines appear in
`output.log`". An empty `output.log` - or no `output.log`, or a no-op tool that
writes nothing - satisfies it trivially. Absence is mistaken for correctness.
This is precisely the jet-fighters failure generalized: a criterion that a broken
or empty artifact passes.

## Correct fix (for reference, not shipped here)

Rewrite the criterion to a positive do-and-observe: given a fixed input log
containing both a known sensitive line and a known benign line, assert the
sensitive line is absent from `output.log` **and** the benign line is present -
a property an empty output fails.
