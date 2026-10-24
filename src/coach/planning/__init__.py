"""Planning engine, interference engine and adaptive loop (spec/functional/planning.spec.md).

Produces immutable plan versions (ADR-010). Proposals are always evaluated by the
policy engine before they are persisted (ADR-017).
"""
