import { useEffect } from "react";

/** Tab trap for modal surfaces: keep focus inside while open. */
export function trapTab(container, e) {
  if (e.key !== "Tab") return;
  const nodes = container.querySelectorAll(
    "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])",
  );
  const list = Array.from(nodes).filter((n) => n.offsetParent !== null || n === document.activeElement);
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
  useEffect(() => {
    if (!active) return undefined;
    const el = ref.current;
    if (!el) return undefined;
    function onKey(e) {
      trapTab(el, e);
    }
    el.addEventListener("keydown", onKey);
    return () => el.removeEventListener("keydown", onKey);
  }, [ref, active]);
}
