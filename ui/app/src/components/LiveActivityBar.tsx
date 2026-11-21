import { motion } from "motion/react";
import { useEffect, useMemo, useState } from "react";

import type { ActivityItem } from "../types/protocol";

export interface LiveActivityBarProps {
  activities: ActivityItem[];
  activeCount: number;
  onCancel?: (aid: string) => void;
  onInteraction?: (inside: boolean) => void;
}

/** Mirrors `ui.event_bus.ActivityKind`. */
const KIND_TINT: Record<string, string> = {
  task: "#06F7FF",
  vision: "#B030FF",
  code: "#1E5FFF",
  browser: "#06F7FF",
  download: "#30D158",
  recording: "#FF2D9F",
  reminder: "#FFD60A",
  generic: "#06F7FF",
};

/**
 * The Dynamic-Island-style capsule: one live line for whatever FRIDAY is doing,
 * plus a count when several things run at once. Disappears the instant the last
 * activity finishes.
 */
export function LiveActivityBar({
  activities,
  activeCount,
  onCancel,
  onInteraction,
}: LiveActivityBarProps) {
  const active = useMemo(
    () => activities.filter((activity) => activity.active).slice(0, 3),
    [activities],
  );
  const head = active[0];
  const [now, setNow] = useState(() => Date.now());
  const hasActive = activeCount > 0 && active.length > 0;

  // Keep the elapsed read-out honest without a per-frame tick.
  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasActive]);

  if (!hasActive || !head) return null;

  const tint = KIND_TINT[head.kind] ?? "#06F7FF";
  const elapsed = Math.max(0, Math.round(now / 1000 - head.ts));
  const extra = Math.max(0, activeCount - 1);

  return (
    <motion.div
      layout
      className="glass glass-pill interactive pointer-events-auto flex h-7 max-w-[340px] items-center gap-2 px-3 text-[11px] font-medium"
      initial={{ opacity: 0, y: 8, scale: 0.9 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 6, scale: 0.92 }}
      transition={{ type: "spring", stiffness: 420, damping: 30 }}
      onPointerEnter={() => onInteraction?.(true)}
      onPointerLeave={() => onInteraction?.(false)}
      onContextMenu={(event) => {
        if (!onCancel) return;
        event.preventDefault();
        onCancel(head.aid);
      }}
      title={onCancel ? "Right-click to cancel" : undefined}
      aria-live="polite"
    >
      <span className="relative grid size-2.5 place-items-center">
        <span
          className="absolute inset-0 animate-ping rounded-full opacity-60"
          style={{ backgroundColor: tint }}
        />
        <span
          className="relative size-1.5 rounded-full"
          style={{ backgroundColor: tint }}
        />
      </span>
      <span className="shrink-0 uppercase tracking-[0.08em] text-white/55">
        {head.kind || "task"}
      </span>
      <span className="min-w-0 flex-1 truncate text-white/90">{head.title}</span>
      {extra > 0 ? (
        <span className="shrink-0 rounded-full bg-white/10 px-1.5 text-[10px] tabular-nums text-white/70">
          +{extra}
        </span>
      ) : null}
      {elapsed > 0 ? (
        <span className="shrink-0 tabular-nums text-white/40">{elapsed}s</span>
      ) : null}
      <span className="relative h-[2px] w-8 shrink-0 overflow-hidden rounded-full bg-white/10">
        <span
          className="absolute inset-y-0 -left-1/2 w-1/2 rounded-full"
          style={{
            background: `linear-gradient(90deg, transparent, ${tint})`,
            animation: "activity-sweep 1.4s ease-in-out infinite",
          }}
        />
      </span>
      <style>{`@keyframes activity-sweep{0%{left:-50%}100%{left:110%}}`}</style>
    </motion.div>
  );
}

export default LiveActivityBar;
