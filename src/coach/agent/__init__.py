"""AI coach: typed tools, prompts, LLM provider adapter (spec/functional/agent.spec.md).

The agent reaches data only through application use cases exposed as narrow tools
(spec/contracts/tools.spec.md). It never imports integrations or infrastructure;
this is enforced by an import-linter contract (ADR-001).
"""
