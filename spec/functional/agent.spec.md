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

Precision (P13.3; `coach.agent.coach`, `coach.agent.validation`):

- **Order (review of P13.4).** The local steps run first: secret redaction, then the red-flag
  phrase rules, and only then the consent and length checks. Safety comes before privacy
  here because nothing leaves the machine; without AI_PROCESSING nothing is stored or sent,
  and the chat says how to grant it. A message has at most 8000 characters.
- **Red flags answer in code.** When the phrase rules find a red flag, SAFETY_HOLD is
  activated, the answer is the fixed safety notice (safety spec 3), the message is not sent to
  the model (it could only speculate about causes), and an `AgentDecision` SAFETY_RESPONSE is
  recorded (action `ACTIVATE_SAFETY_HOLD`, the red flags as reasons, no model). Both turns are
  kept in the conversation, unless AI_PROCESSING is not granted or the message is too long:
  then the hold, the notice and the decision happen and nothing is stored.
- **Context block.** The athlete's turn starts with a block written by the application:
  `<context>{"today", "timezoneSet", "language", "lifecycle", "conditions"}</context>`.
  Volatile facts travel with the message, so the system prompt and the tools stay the same
  (and cached) for the whole conversation.
- **Tools.** The model may call tools for at most 8 rounds per message, the correction pass
  included; past the budget its tool calls get `TOOL_BUDGET_EXHAUSTED` and the exchange is
  closed in code. A tool call cut off by the output limit or a refusal gets
  `OUTPUT_TRUNCATED`, and an empty refusal is stored as `[no answer]`: every stored tool use
  has its result and every turn has content. A tool result has at most 128 KiB
  (`RESULT_TOO_LARGE`) and one message's exchange at most 1 MiB (`EXCHANGE_TOO_LARGE`); a
  failing tool answers `TOOL_FAILED` (its exception type is logged, never its message).
- **Output validation rules 1.0.0.** The sources of a turn are its tool results, the context
  block and the athlete's message. From a tool result only data counts: values, display
  forms, dates and data text; identifiers, evidence, instants (`...At`), versions and the
  commands the tools give are left out, so their digits never pass as data, and in the answer
  identifiers and `coach ...` commands are not numbers. Every number of the answer must be one of their numbers
  at the precision written (5.2 matches 5.23; the display forms of the tools, such as 45:00,
  4:24/km, 10.23 km or 85%, count). Spanish and English writing are both read ("10.000",
  "5,2"); list markers and digits joined to a word on their left (VO2max, Z2) are not
  numbers. An answer with untraced numbers goes back to the model once, with a note that
  names them; if they remain, the answer is shown with a warning that names them
  (outcome NOT_VERIFIED). An incomplete answer (output limit) is checked and warned the same
  way. Numbers from earlier turns must be read again: a conversation can span days.
- **Shown after validation.** The answer is shown once validated, never while it arrives
  (FR-AGT-004 over the streaming display of section 7); each data read is shown as a
  progress line meanwhile.
- **Outcomes.** ANSWERED, NOT_VERIFIED, SAFETY_HOLD, REFUSED (the model refused: a fixed
  text), INCOMPLETE (output limit or tool budget). Each reply emits `agent.reply.sent`
  (outcome and counts), each decision `agent.decision.recorded` (its kind).
- **Failure.** If anything fails during the exchange (the model, a store), the exchange is
  not stored; the decisions of tools that already acted are recorded with a fixed
  explanation, and a red flag reported through a tool still shows its notice. Sending the
  message again can repeat a write a tool already made (a DRAFT goal, a PROPOSED version):
  the athlete sees it and can reject it.

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

Precision (P13.6, decision D-16):

- **Scenarios.** `tests/eval_scenarios.py` holds one scenario per category (plus goal
  presentation, AT-GOL-001): an athlete's synthetic world and message. Its judge names every
  rule a turn broke: the wording rules, untraced numbers, forbidden tool calls, onboarding
  answers recorded that were not asked, a goal not LIKELY presented without its realistic and
  minimum alternatives, secrets sent.
- **Wording rules 1.0.0** (`tests/eval_checks.py`): phrase rules over the casefolded text
  without accents, Spanish and English, for a probable medical cause or diagnosis, a symptom
  called harmless, any drug or dosage, a guarantee, a provider action the product cannot take
  and, with SAFETY_HOLD active, intensive training; causal claims for insight texts. They
  judge answers; no control depends on them.
- **Simulated.** `tests/agent_evals` runs every scenario with a scripted good model, which
  must break no rule, and a scripted bad one, which must be caught by the product or by the
  judge. They run with the test suite and send nothing.
- **Live, on request.** `tools/agent_evals_live.py` runs the same scenarios against the real
  model in throw-away synthetic installations: without `--yes` it only prints a token
  estimate; with it, the key comes from the athlete's secret store and the results are written
  as codes to `reports/agent-evals-live.json`. Only with the athlete's authorization, after the
  estimate (D-16).

## 7. Prompts and models

- System prompts are versioned files in the repository; `promptVersion` is their semantic
  version.
- The LLM is reached through an `LLMProvider` port (ADR-018). The default adapter targets
  Anthropic Claude models; the model ID is configuration and is recorded per decision.
- If the LLM is unavailable, deterministic features (ingestion, metrics, plans already
  generated, reports' data parts) keep working.

Precision (P13; decision D-14; `coach.application.llm`, `coach.integrations.llm`):

- **Messages.** The port speaks the Messages API shape as plain JSON (roles, content blocks).
  Every content block the model returns is kept unchanged, so a stored conversation can be
  sent again exactly (section 9).
- **Versions are locked.** Each prompt version and the tool set version name exactly one
  content (`tests/unit/test_agent_versions.py` keeps their SHA-256): changing a prompt means a
  new prompt file, changing a tool means a new `TOOLS_VERSION`, so the versions recorded on
  conversations, decisions and AI texts identify what was used.
- **Model and effort.** The default model is Claude Opus 5.5 (`claude-opus-5-5`), with effort
  `medium`. Both are configuration (`COACH_LLM_MODEL`, `COACH_LLM_EFFORT`), and the model is
  recorded on every decision.
- **Request.** It streams the answer, so long turns never hit an HTTP timeout (the chat
  shows the answer once it is validated, section 4). Thinking is left at the model's default (adaptive, its text
  omitted), so no reasoning is shown or stored. The stable prefix (system prompt and tools) is
  cached.
- **Server-side fallback.** Anthropic's fallback for a declined request is enabled
  (`fallbacks: "default"`), and the answering model is recorded.
- **API key.** The key is read from the OS secret store (`coach secret set llm-api-key`) when
  a reply is needed and given only to the SDK client. It never reaches the agent, a prompt, a
  log or an error (ADR-001).
- **Errors.** Errors become codes without their details: `NO_API_KEY`, `AUTHENTICATION`,
  `RATE_LIMITED`, `UNAVAILABLE`, `REQUEST_INVALID`.

## 8. User interface (decided in P13)

The primary UI is an in-application chat with its own agent loop. An MCP-host UI (for example
Claude Desktop) would send athlete messages to the LLM before this application could redact
secrets or screen red flags, which breaks steps 2 and 3 of the pipeline; it may only be added
as a secondary, read-mostly interface.

Decision D-13 (P13): the chat runs in the terminal, as `coach chat` (an interactive session)
and `coach chat "question"` (one question). A local web UI is not built in V1.

## 9. Conversations (decision D-15, P13)

Conversations are stored in the data directory (`Conversation`, contract
[../contracts/conversation.schema.json](../contracts/conversation.schema.json)) so a chat can
continue another day.

- **What a turn holds.** Each turn keeps its content blocks exactly as they were sent or
  received. The athlete's text has already passed secret redaction (section 4), so a
  disclosed secret is never stored. Tool results are the data the tools returned.
- **Append-only.** A conversation is append-only: no earlier turn is ever edited or removed,
  because the model's reasoning blocks are valid only when the history is replayed unchanged.
  Thinking text is omitted by the provider, so no chain-of-thought is stored.
- **Binding.** A conversation is bound to the prompt version, tool set version and model it
  started with, and to the consents granted while its turns were added (`consents`): its
  history may hold their data, so a revoked consent starts a new conversation (privacy spec
  1: revocation stops new processing at once). A change of any of them, or not enough room
  left for one more exchange (400 turns, 2 MiB), starts a new one.
- **Privacy.** Conversations are personal data under the privacy inventory and the deletion
  workflow (P15).
