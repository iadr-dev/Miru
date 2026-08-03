# ADR 0004 — M5F canonical package immutability

## Status
Accepted.

## Context
M5F is the Mode A / Level 1 canonical, preserved market context that everything downstream
trusts. If it could be mutated, provenance and reproducibility would break, and the
"safe artifact handoff" would be untrustworthy.

## Decision
`context/canonical.py` treats the canonical package (M5F) as **append-only / versioned**.
Existing canonical packages are never mutated in place; new context creates a new version.

## Consequences
- Reproducible reasoning: a journal entry references an exact M5F version.
- No accidental corruption of preserved history.
- Storage grows with versions (acceptable; canonical volume is bounded).
- A governance test asserts M5F is never mutated.
