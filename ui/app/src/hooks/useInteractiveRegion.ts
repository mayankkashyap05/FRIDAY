import { useCallback, useEffect, useMemo, useRef } from "react";

/**
 * Windows click-through is all-or-nothing per HWND: while the stage window
 * ignores mouse events the *whole* overlay is transparent to the desktop, and
 * while it accepts them it swallows clicks over its entire (invisible) area.
 *
 * So the renderer tracks a reference count of live interactive surfaces — the
 * orb disc, the cards, the activity capsule, the type-to-Siri pill, the setup
 * card — and flips the window between modes only when that count crosses zero.
 * A naive per-element toggle would drop input as soon as the pointer travelled
 * from a card to the orb (leave fires before enter).
 */

let interactiveCount = 0;
let lastReported: boolean | null = null;

function report(interactive: boolean): void {
  if (lastReported === interactive) return;
  lastReported = interactive;
  window.fridayAPI?.setInteractive(interactive);
}

export function acquireInteraction(): void {
  interactiveCount += 1;
  report(true);
}

export function releaseInteraction(): void {
  interactiveCount = Math.max(0, interactiveCount - 1);
  if (interactiveCount === 0) report(false);
}

/** Test hook: forget the shared counter between cases. */
export function __resetInteractiveRegions(): void {
  interactiveCount = 0;
  lastReported = null;
}

export interface InteractiveRegion {
  /** Spread onto any element that must receive mouse input. */
  regionProps: {
    onPointerEnter: () => void;
    onPointerLeave: () => void;
    className: string;
  };
  /** True while the pointer is inside this region. */
  /** Force the window interactive while a surface holds keyboard focus. */
  hold: () => () => void;
  isInside: () => boolean;
}

export interface InteractiveRegionOptions {
  /** Notify Python (drives the reading-grace timer). */
  onChange?: (inside: boolean, isAnyInside: boolean) => void;
  /** Keep the window interactive regardless of hover (typing, setup). */
  pinned?: boolean;
}

export function useInteractiveRegion(
  options: InteractiveRegionOptions = {},
): InteractiveRegion {
  const { onChange, pinned = false } = options;
  const insideRef = useRef(false);
  const onChangeRef = useRef(onChange);
  onChangeRef.current = onChange;

  const notify = useCallback((inside: boolean) => {
    insideRef.current = inside;
    onChangeRef.current?.(inside, interactiveCount > 0);
  }, []);

  const onPointerEnter = useCallback(() => {
    if (insideRef.current) return;
    acquireInteraction();
    notify(true);
  }, [notify]);

  const onPointerLeave = useCallback(() => {
    if (!insideRef.current) return;
    releaseInteraction();
    notify(false);
  }, [notify]);

  const hold = useCallback(() => {
    acquireInteraction();
    return () => releaseInteraction();
  }, []);

  // A pinned surface (open text field / setup card) holds the window open for
  // as long as it is mounted, even with the pointer parked elsewhere.
  useEffect(() => {
    if (!pinned) return;
    acquireInteraction();
    return () => releaseInteraction();
  }, [pinned]);

  // Never leave the window stuck in click-through-off if this unmounts
  // mid-hover.
  useEffect(
    () => () => {
      if (insideRef.current) {
        insideRef.current = false;
        releaseInteraction();
      }
    },
    [],
  );

  return useMemo(
    () => ({
      regionProps: {
        onPointerEnter,
        onPointerLeave,
        className: "interactive hit-surface",
      },
      hold,
      isInside: () => insideRef.current,
    }),
    [onPointerEnter, onPointerLeave, hold],
  );
}
