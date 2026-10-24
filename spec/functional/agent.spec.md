# AI coach

Owner of FR-AGT-001 to FR-AGT-004. Master spec §3, §54, §60, §65, §66. Implemented in
Phase P13. Tool contracts: [../contracts/tools.spec.md](../contracts/tools.spec.md).

## 1. Responsibilities

The AI coach **may**: converse, run the onboarding dialogue (questions chosen by the
deterministic planner), interpret evidence, identify patterns, reason about trade-offs,
propose plans and adaptations, explain recommendations.

The AI coach **must not** be responsible for: authentication, authorization, secrets,
deterministic calculation, unit conversion, deduplication, persistence integrity, ownership
checks, schema validation, consent enforcement or provider security. Those are code.

## 2. Tools only (FR-AGT-001)

- The agent reaches data and actions only through the narrow typed tools of
  `contracts/tools.spec.md`.
- Prohibited: arbitrary SQL, HTTP, URLs, shell, secret reading, whole-database reads,
  arbitrary code execution.
- Package rule: `coach.agent` cannot import `coach.integrations` or `coach.infrastructure`
  (import-linter contract, ADR-001).

## 3. Identity binding (FR-AGT-002)

Tool inputs never contain `athleteId`. The application binds every tool call to the athlete
of the authenticated session. A prompt-injected request for another athlete's data has no
parameter to express it (AT-IDOR-002).

## 4. Message pipeline

For each athlete message, in this order, all deterministic:

1. **Consent check:** AI_PROCESSING consent must be granted (PRIV-CON-003).
2. **Secret detection:** detected credentials are redacted before persistence and before
   the LLM call; the athlete is warned and told to rotate (SEC-SEC-005).
3. **Red-flag detection:** a hit activates SAFETY_HOLD before the LLM call (SAFE-MED-002).
4. **Context assembly:** minimal data needed for the request, preferring derived summaries
   over raw streams; external text wrapped as untrusted data (SEC-INJ-001).
5. **LLM call** with typed tools.
6. **Output validation:** numbers in the response must trace to tool results in this turn
   or cited evidence (FR-AGT-004). Proposals go to the policy engine.

The LLM may also flag possible red flags; a flag from either source activates SAFETY_HOLD
(fail-safe).

## 5. Decision audit (FR-AGT-003)

Significant decisions (plan proposals, adaptations, load reductions, safety responses) are
stored as `AgentDecision`: `decisionId`, `timestamp`, `promptVersion`, `specVersion`
(`coach.SPEC_VERSION`), `modelId` when available, `toolCalls[]` and `evidenceRefs[]`,
`proposedAction`, `policyDecision`, `userFacingExplanation`.

Private chain-of-thought is **not** stored. Evidence and outcome are.

## 6. No fabrication (FR-AGT-004)

- The agent never invents metrics, tool results, provider capabilities or athlete data.
- When data is missing it says so and lists what is missing.
- Agent evaluations (tests/agent_evals) check fabricated numbers, fabricated tool results,
  evidence traceability, medical boundaries, onboarding behaviour, missing data handling,
  unsafe recommendations, prompt injection through external data and unsupported Garmin
  capabilities.

## 7. Prompts and models

- System prompts are versioned files in the repository; `promptVersion` is their semantic
  version.
- The LLM is reached through an `LLMProvider` port (ADR-018). The default adapter targets
  Anthropic Claude models; the model ID is configuration and is recorded per decision.
- If the LLM is unavailable, deterministic features (ingestion, metrics, plans already
  generated, reports' data parts) keep working.

## 8. User interface (decision deferred to P13)

The primary UI is an in-application chat with its own agent loop. An MCP-host UI (for example
Claude Desktop) would send athlete messages to the LLM before this application could redact
secrets or screen red flags, which breaks steps 2 and 3 of the pipeline; it may only be added
as a secondary, read-mostly interface.
