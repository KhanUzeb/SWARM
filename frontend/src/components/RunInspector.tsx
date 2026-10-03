import * as React from "react";
import { ChevronDown, Terminal, TriangleAlert, Clock } from "lucide-react";
import { Lamp } from "./Flap";
import {
  buildSteps,
  fmtDuration,
  parseAuditLine,
  runDuration,
  usageTotals,
  type AuditLine,
  type WorkEvent,
  type WorkSession,
} from "../work/inspectorModel";

type RunInspectorProps = {
  session?: WorkSession | null;
  events?: WorkEvent[] | null;
  /** Tool arguments, in emission order, from the backend's audit lines. */
  audit?: AuditLine[] | null;
  model?: string;
  /** Opens the panel already expanded (a failed run). */
  defaultOpen?: boolean;
};

/**
 * The run inspector: one bot reply, printed as the ordered steps that
 * produced it. It reads the same `work_events` log the work rail replays,
 * so what it shows is the durable record — not a second opinion about it.
 *
 * Design notes (AGENTS.md 8a):
 *  · Continuous execution is a printed line on a roll, not a card per step.
 *  · Each step carries a lamp, so "running" and "finished" are told apart by
 *    light, not by tinting the row.
 *  · Progressive disclosure is a toggle, not persistent chrome: an
 *    unremarkable reply shows one quiet line of machine register.
 *  · Nothing here invents a number. Tokens and cost are not persisted by
 *    the backend, and the panel says so in those words.
 */
export function RunInspector({
  session,
  events,
  audit,
  model,
  defaultOpen,
}: RunInspectorProps) {
  const list = events || [];
  const steps = React.useMemo(() => buildSteps(list, audit || []), [list, audit]);
  const [open, setOpen] = React.useState(!!defaultOpen);
  const hadFailure = React.useMemo(() => steps.some((s) => s.failure), [steps]);

  // A failed reply is not hidden behind a toggle — the reason is what you came for.
  React.useEffect(() => {
    if (hadFailure) setOpen(true);
  }, [hadFailure]);

  const duration = React.useMemo(() => runDuration(session, list), [session, list]);
  const usage = React.useMemo(() => usageTotals(list), [list]);
  const tools = steps.filter((s) => s.kind === "tool").length;
  const approvals = steps.filter((s) => s.kind === "approval" && s.label === "Approval requested").length;
  const handoffs = steps.filter((s) => s.kind === "handoff").length;

  if (steps.length === 0) return null;

  return (
    <div className="inspector">
      <button
        type="button"
        className="trace-toggle"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
      >
        {hadFailure ? (
          <TriangleAlert size={12} className="shrink-0" aria-hidden="true" />
        ) : (
          <Terminal size={12} className="shrink-0" aria-hidden="true" />
        )}
        <span>
          Run
          {tools > 0 ? `, ${tools} tool${tools === 1 ? "" : "s"}` : ""}
          {approvals > 0 ? `, ${approvals} approval${approvals === 1 ? "" : "s"}` : ""}
          {handoffs > 0 ? `, ${handoffs} handoff${handoffs === 1 ? "" : "s"}` : ""}
        </span>
        {model && <span className="inspector-model">{model}</span>}
        {duration != null && (
          <span className="inspector-elapsed">
            <Clock size={11} aria-hidden="true" />
            {fmtDuration(duration)}
          </span>
        )}
        <span className="inspector-count">{steps.length} steps</span>
        <ChevronDown
          size={12}
          aria-hidden="true"
          style={{ transform: open ? "rotate(180deg)" : "none", transition: "transform 110ms" }}
        />
      </button>

      {open && (
        <div className="inspector-body">
          <ol className="inspector-steps">
            {steps.map((step) => (
              <InspectorRow key={step.key} step={step} />
            ))}
          </ol>
          <Footer usage={usage} duration={duration} />
        </div>
      )}
    </div>
  );
}

function InspectorRow({ step }: { step: ReturnType<typeof buildSteps>[number] }) {
  const hasDetail = !!(step.args || step.output);
  return (
    <li className={`inspector-step${step.failure ? " is-failure" : ""}`}>
      <span className="inspector-step-head">
        <span className="inspector-seq">{String(step.seq).padStart(3, "0")}</span>
        <span className="inspector-lamp">
          <Lamp tone={step.tone} title={step.label} />
        </span>
        <span className="inspector-step-label">{step.label}</span>
        {step.detail && <span className="inspector-step-detail">{step.detail}</span>}
        {step.ms != null && <span className="inspector-step-ms">{fmtDuration(step.ms)}</span>}
        {hasDetail && (
          <details className="inspector-step-more" open={step.failure || undefined}>
            <summary>
              {step.failure ? "Show what happened" : "Show call"}
              <ChevronDown size={11} aria-hidden="true" />
            </summary>
            {step.args != null && (
              <Register label="call" value={step.args} tone="hold" />
            )}
            {step.output != null && (
              <Register label={step.failure ? "error" : "result"} value={step.output} tone="default" />
            )}
          </details>
        )}
      </span>
      {step.output != null && step.failure && !hasDetail && (
        <Register label="error" value={step.output} tone="hold" />
      )}
    </li>
  );
}

/**
 * Machine register: one printed argument or result line, in Departure Mono,
 * because this is what the machine actually said — not prose.
 */
function Register({
  label,
  value,
  tone,
}: {
  label: string;
  value: string;
  tone: "hold" | "default";
}) {
  const text = String(value || "").trim();
  if (!text) return null;
  return (
    <span className={`inspector-register${tone === "hold" ? " is-hold" : ""}`}>
      <span className="inspector-register-label">{label}</span>
      <span className="inspector-register-value">{text}</span>
    </span>
  );
}

function Footer({
  usage,
  duration,
}: {
  usage: { prompt: number; completion: number } | null;
  duration: number | null;
}) {
  return (
    <p className="inspector-foot">
      {usage ? (
        <span className="inspector-foot-reg">
          {usage.prompt} in, {usage.completion} out
        </span>
      ) : (
        <span className="inspector-foot-reg">tokens and cost are not recorded yet</span>
      )}
      {duration != null && <span className="inspector-foot-reg">{fmtDuration(duration)} wall</span>}
    </p>
  );
}

/**
 * Convenience for callers that hold raw channel messages rather than parsed
 * audit lines: the backend writes one `system` line per tool call, so the
 * arguments are recoverable from the same roll the reply is printed on.
 */
export function auditLinesFrom(messages: Array<{ body?: string }> | null | undefined): AuditLine[] {
  const out: AuditLine[] = [];
  for (const m of messages || []) {
    const line = parseAuditLine(m?.body || "");
    if (line) out.push(line);
  }
  return out;
}