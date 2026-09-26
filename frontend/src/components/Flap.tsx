import { useEffect, useRef, useState } from "react";

type Tone = "plain" | "unlit" | "go" | "hold" | "amber" | "reverse";

const TONE_CLASS: Record<Tone, string> = {
  plain: "",
  unlit: "flap-unlit",
  go: "flap-go",
  hold: "flap-hold",
  amber: "flap-amber",
  reverse: "flap-reverse",
};

/** One flap's travel, in ms. Matches --set-type in styles.css. */
const SET_TYPE = 190;

function prefersReducedMotion() {
  return (
    typeof window !== "undefined" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

type FlapProps = {
  value: string;
  tone?: Tone;
  large?: boolean;
  title?: string;
  className?: string;
};

/**
 * A split-flap card. The face is split at the centre line, which is what
 * makes it a flap rather than a label. When `value` changes, the two halves
 * rotate through the split so the card is briefly on its black reverse — the
 * state change lands in peripheral vision before it is read.
 *
 * The first render never animates: content is visible by default, and a
 * module only travels when its state actually changes.
 */
export function Flap({
  value,
  tone = "plain",
  large = false,
  title,
  className = "",
}: FlapProps) {
  const [shown, setShown] = useState(value);
  const [prev, setPrev] = useState<string | null>(null);
  const [turn, setTurn] = useState<"idle" | "out" | "in">("idle");
  const timers = useRef<number[]>([]);

  useEffect(() => {
    if (value === shown) return;
    if (prefersReducedMotion()) {
      setShown(value);
      return;
    }
    setPrev(shown);
    setTurn("out");
    const a = window.setTimeout(() => {
      setShown(value);
      setTurn("in");
    }, SET_TYPE);
    const b = window.setTimeout(() => {
      setTurn("idle");
      setPrev(null);
    }, SET_TYPE * 2);
    timers.current = [a, b];
    return () => {
      timers.current.forEach(clearTimeout);
      timers.current = [];
    };
  }, [value, shown]);

  // While turning, the top half carries the old face on the way out and the
  // new face on the way in; the bottom half carries the opposite.
  const topText = turn === "out" ? (prev ?? shown) : shown;
  const botText = turn === "out" ? shown : (prev ?? shown);

  const classes = [
    "flap",
    "flap-flip",
    TONE_CLASS[tone],
    large ? "flap-lg" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  // The two halves are absolutely positioned, so they contribute no width.
  // An in-flow sizer with the same text gives the card its intrinsic width.
  return (
    <span className={classes} data-turn={turn} title={title ?? value}>
      <span className="flap-sizer">{value}</span>
      <span className="half half-t" aria-hidden="true">
        <i>{topText}</i>
      </span>
      <span className="half half-b" aria-hidden="true">
        <i>{botText}</i>
      </span>
    </span>
  );
}

/**
 * The lamp: a small light in the housing. Not a coloured dot on a rounded
 * row — a square of light with a defined off state, so "nothing is wrong"
 * and "nothing is happening" are visually different.
 */
export function Lamp({
  tone = "off",
  title,
}: {
  tone?: "go" | "hold" | "amber" | "off";
  title?: string;
}) {
  const color =
    tone === "go"
      ? "var(--go-lit)"
      : tone === "hold"
        ? "var(--hold-lit)"
        : tone === "amber"
          ? "var(--lamp-hold)"
          : "var(--ink-3)";
  return (
    <span
      aria-hidden="true"
      title={title}
      style={{ width: 6, height: 6, flexShrink: 0, background: color, display: "inline-block" }}
    />
  );
}
