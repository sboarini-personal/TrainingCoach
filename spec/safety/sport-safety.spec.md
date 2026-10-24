# Sport safety

Owner of SAFE-MED-001 to SAFE-MED-005. Master spec §10. Takes precedence over every other
requirement except security (spec precedence).

## 1. Scope (SAFE-MED-001)

The system **may**: analyse sports performance and training load, identify trends, adapt
training, recommend sports recovery, suggest professional evaluation.

The system **must not**: diagnose disease or injuries, prescribe medication, provide clinical
rehabilitation, guarantee performance, or interpret dangerous symptoms as harmless.

Wording rules (checked by agent evaluations): no statement names a probable medical cause;
no statement says a symptom is "normal", "nothing to worry about" or similar; no dosage of
any drug or supplement is given.

## 2. Red flags (SAFE-MED-002)

| Code | Red flag |
|---|---|
| RF_CHEST_PAIN | chest pain or pressure |
| RF_FAINTING | fainting or near-fainting |
| RF_BREATHING | unusual breathing difficulty |
| RF_NEURO | sudden neurological symptoms (for example confusion, speech problems, facial droop, numbness on one side, severe sudden headache, vision loss) |
| RF_STRENGTH_LOSS | sudden loss of strength |
| RF_SEVERE_ACUTE_PAIN | severe acute pain (sudden onset, intensity 7 or more of 10) |
| RF_PALPITATIONS | palpitations or irregular heartbeat with dizziness (added, conservative, pending review) |

Detection sources, combined fail-safe (any one activates the hold):

1. Structured inputs: check-in or activity pain intensity of 7 or more with acute onset; an
   explicit red-flag question in onboarding or check-in.
2. Deterministic phrase detection in athlete messages and notes (Spanish and English lists,
   versioned), run before the LLM call.
3. LLM classification flag returned with the response.

## 3. SAFETY_HOLD effects (SAFE-MED-003, SAFE-MED-004)

When SAFETY_HOLD activates:

- the policy engine denies any recommendation, proposal or activation that includes
  intensive training (POL-SAFETY-HOLD). This is code, not a prompt instruction;
- the coach states the safety reason, recommends appropriate healthcare evaluation, and for
  acute severe symptoms advises contacting local emergency services;
- the coach does not diagnose or speculate about causes;
- `safety.hold.activated` is emitted and audited.

AT-SAFE-001 covers activation, the policy denial and the response content.

## 4. Release (SAFE-MED-005, PROPOSED)

- The hold is released only by an explicit athlete action (for example confirming that a
  healthcare professional cleared them to train), recorded and audited.
- Neither the agent nor any automatic rule releases a hold.
- After release, training resumes conservatively (the next plan version starts with reduced
  intensity and a re-evaluation date).

## 5. Pain that is not a red flag

Localised pain below the red-flag threshold: no hold. The policy engine restricts load on
the affected region for the next sessions, the coach suggests professional evaluation if pain
persists beyond 7 days or worsens, and pain reports feed the adaptation loop.

## 6. Performance claims

No guarantee of performance outcomes (FR-GOL-005).
