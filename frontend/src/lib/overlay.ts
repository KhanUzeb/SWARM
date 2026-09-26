/**
 * Overlays are `position: fixed`, so their coordinates are ours to compute.
 * A menu that forgets to place itself does not degrade — it renders one
 * viewport-height below the fold and the item you aimed at never appears.
 * One placement rule, shared by every floating surface in the app.
 */
export interface OverlayPlacement {
  top: number;
  left: number;
  maxHeight: number;
}

export interface PlaceOverlayOptions {
  align?: "left" | "right";
  gap?: number;
  padding?: number;
  /** Below this much room the overlay flips above its anchor. */
  minHeight?: number;
  maxHeight?: number;
}

const VIEWPORT_MARGIN = 8;

export function placeOverlay(
  anchor: DOMRect,
  overlay: HTMLElement,
  options: PlaceOverlayOptions = {},
): OverlayPlacement {
  const {
    align = "left",
    gap = 6,
    padding = VIEWPORT_MARGIN,
    minHeight = 120,
    maxHeight = 420,
  } = options;

  const width = overlay.offsetWidth || Math.max(overlay.scrollWidth, 190);
  const height = overlay.offsetHeight;
  const viewportWidth = document.documentElement.clientWidth;
  const viewportHeight = document.documentElement.clientHeight;

  const roomBelow = viewportHeight - anchor.bottom - gap - padding;
  const roomAbove = anchor.top - gap - padding;
  const below = roomBelow >= Math.min(height, minHeight) || roomBelow >= roomAbove;
  const room = Math.max(96, Math.min(maxHeight, below ? roomBelow : roomAbove));
  const shown = Math.min(height, room);

  const left = align === "right" ? anchor.right - width : anchor.left;
  const top = below ? anchor.bottom + gap : anchor.top - gap - shown;

  return {
    top: Math.min(Math.max(top, padding), Math.max(padding, viewportHeight - shown - padding)),
    left: Math.min(Math.max(left, padding), Math.max(padding, viewportWidth - width - padding)),
    maxHeight: room,
  };
}
