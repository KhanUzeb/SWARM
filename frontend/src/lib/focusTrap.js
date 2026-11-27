import { useEffect, useRef } from "react";

/** Controls a Tab can actually reach. Roving tablists keep their inactive
 *  faces in the DOM with tabIndex=-1: they must not become a hidden wrap
 *  target on Shift+Tab just because the selector matched a <button>. */
function focusable(container) {
  const nodes = container.querySelectorAll(
    "button, [href], input, select, textarea, [tabindex]",
  );
  return Array.from(nodes).filter((n) =>
    n.tabIndex >= 0
    && !n.hasAttribute("disabled")
    && !n.closest("[disabled]")
    && (n.offsetParent !== null || n === document.activeElement),
  );
}

/** Tab trap for modal surfaces: keep focus inside while open. */
export function trapTab(container, e) {
  if (e.key !== "Tab") return;
  const list = focusable(container);
  if (!list.length) return;
  const first = list[0];
  const last = list[list.length - 1];
  if (e.shiftKey && document.activeElement === first) {
    e.preventDefault();
    last.focus();
  } else if (!e.shiftKey && document.activeElement === last) {
    e.preventDefault();
    first.focus();
  }
}

export function useFocusTrap(ref, active) {
  const restoreRef = useRef(null);
  useEffect(() => {
    if (!active) return undefined;
    const el = ref.current;
    if (!el) return undefined;
    // Opening a dialog left focus on the button behind it. The trap listens on
    // the dialog, so Tab from that outside button walked the page underneath
    // the scrim; closing then dropped focus on BODY. Capture the opener, move
    // into the dialog, and hand focus back on the way out.
    const opener = document.activeElement;
    restoreRef.current = opener instanceof HTMLElement && !el.contains(opener) ? opener : null;
    focusable(el)[0]?.focus();
    function onKey(e) {
      trapTab(el, e);
    }
    el.addEventListener("keydown", onKey);
    return () => {
      el.removeEventListener("keydown", onKey);
      const previous = restoreRef.current;
      restoreRef.current = null;
      if (previous && document.contains(previous)) previous.focus();
    };
  }, [ref, active]);
}
