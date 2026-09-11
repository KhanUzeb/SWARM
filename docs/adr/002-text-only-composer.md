# ADR 002 - Text-only composer, resilient chat runtime

Date: 2026-09-11.

## Decision

The composer stays **text-only**. A Groq-Whisper speech-to-text backend
was built, then removed before release: no local model under the
150 MB budget was reliable in this environment, and a cloud STT call
in the send path added a failure mode (and a permission prompt) to the
highest-traffic control in the product. Voice may return when a small
local model can run inside the existing venv with no new services.

In exchange, chat runtime fallbacks were hardened instead:

- **Stop:** per-channel task registry + `POST /api/channels/{id}/stop`;
  partial streams persist with a `[reply cut off — stopped]` marker.
- **Resume:** cutoff markers (both stop and provider-death variants)
  are retryable; the UI labels them Resume.
- **Inference outage:** a pinned model that 404s falls back to the
  agent default once (`model_fallback: true`); 429/5xx keep the
  existing single retry; provider-level fallback across connected
  providers is unchanged.
- **Network outage:** failed sends queue in a per-channel outbox
  (localStorage) and flush automatically on reconnect; nothing typed
  is dropped.

## Consequences

- No microphone, audio upload, or STT endpoints exist; `SPEC.md`
  documents the stop/retry/outbox contract instead.
- Every agent reply carries the resolved `messages.model`, so model
  issues are diagnosable from the bubble tag rather than hidden.
