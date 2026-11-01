# ADR-036: The AI coach: a deterministic pipeline around the model, narrow tools, validated answers

- Status: Proposed (P13, 2026-11-01; athlete decisions D-13 to D-16)
- Date: 2026-11-01
- Requirements: FR-AGT-001 to FR-AGT-004, SEC-AUTHZ-002, SEC-INJ-001, SEC-INJ-002,
  PRIV-CON-003, SEC-SEC-005, SAFE-MED-001 to SAFE-MED-003, FR-RPT-001 to FR-RPT-003,
  FR-INS-001, FR-ONB-001, FR-ONB-002, FR-GOL-004
- Related: ADR-001 (no credentials for the AI), ADR-006 (external data is not instructions),
  ADR-008 (deterministic calculations), ADR-017 (policy engine as final gate), ADR-018 (LLM
  provider abstraction)

## Context

P13 adds the AI coach: a chat in the terminal (D-13) with Claude Opus 5.5 as the configurable
model (D-14), stored conversations (D-15) and agent evaluations that are simulated unless the
athlete asks for real-model runs (D-16). The coach must talk, interpret and explain without
becoming responsible for anything the specification gives to code: numbers, permissions,
consent, safety, ownership, plan content and activation.

## Decision

1. **A pipeline in code around one model exchange** (`coach.agent.coach`). Secret redaction
   and the red-flag phrase rules run first and locally; a red flag activates SAFETY_HOLD and is
   answered with the fixed notice, without calling the model. Then AI_PROCESSING consent, then
   the exchange, then output validation. A context block written by the application carries
   the volatile facts (today, language, conditions), so the system prompt and the tools stay
   fixed and cached for a conversation.
2. **Narrow typed tools, bound to the session** (`coach.agent.tools`, tool set 1.0.0): closed
   JSON Schemas, no athlete, URL, path or secret parameter, every call in the session's owner
   scope; typed errors without messages; one observability event per call. Reads send
   minimised data (an age, never the birth date; no streams), numbers as quantities with unit,
   evidence and a display form computed in code, external text as `untrusted_text`. Activity
   reads need the ACTIVITIES consent and recovery metrics their categories, because sending
   data to the model is processing.
3. **The coach asks, the code decides.** Plans come from the deterministic generator or the
   adaptive loop through the full policy set (`proposeTrainingPlan`); the model never writes
   plan content and nothing activates a version but the athlete. Writes are allow-listed,
   audited as the agent's and limited to values the athlete stated. External actions stop at
   CAPABILITY_UNAVAILABLE or CONFIRMATION_REQUIRED. The model can flag a red flag
   (`flagSafetyConcern`); only the athlete releases a hold.
4. **Answers are validated before they are shown.** Output validation rules 1.0.0 trace every
   number to the turn's tool data, the context block or the athlete's message (identifiers,
   instants and commands do not count); untraced numbers go back to the model once and are
   then named in a warning. The answer is therefore shown after validation, not streamed to
   the screen (streaming is still used for transport).
5. **Decisions and conversations are records.** Significant decisions (plan proposals,
   adaptations, load reductions, safety responses) become `AgentDecision`s with the turn's
   tool calls, evidence and the answer shown, also when the exchange fails after a tool acted.
   Conversations are append-only, replayed exactly (reasoning blocks included, their text
   omitted by the provider) and bound to the prompt, the tool set, the model and the consents
   their history was built with: a revoked consent starts a new conversation.
6. **AI-written texts from computed data** (`coach.agent.narratives`, prompt narrative 1.0.0):
   report narratives (never stored) and insight readings (stored with the prompt and model),
   written without tools from the data part, validated the same way; no recommendation is
   stored while SAFETY_HOLD or RECOVERY_BLOCK is active.
7. **Versions are locked and evaluations are judged in code.** A prompt or the tool set never
   changes under the same version (a test keeps their hashes). Evaluation scenarios and a
   judge (wording rules 1.0.0 and the pipeline's own guarantees) run simulated with every
   test run; `tools/agent_evals_live.py` runs them against the real model only on request,
   after a token estimate.
8. **Layering.** `coach.agent` is a layer below the CLI and the API, above the application;
   it still never imports integrations or infrastructure (import contract).

## Consequences

- The chat waits for the whole answer before showing it, with progress lines for data reads.
- A follow-up question about a number read in an earlier turn makes the model read it again:
  numbers from earlier turns do not count as sources.
- Sending a message again after a failure can repeat a write a tool already made (a DRAFT goal,
  a PROPOSED version); the athlete sees and can reject it.
- The wording rules and the red-flag phrases are initial lists, pending review; the product's
  controls do not depend on the wording rules.
- Changing a prompt or a tool needs a new version, which starts new conversations.
