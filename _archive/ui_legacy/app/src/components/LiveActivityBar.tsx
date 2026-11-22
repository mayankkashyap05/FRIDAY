import { X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useInteractiveRegion } from "../hooks/useInteractiveRegion";
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

/** How long the island waits after the pointer leaves before collapsing. */
const COLLAPSE_GRACE_MS = 900;
const MAX_VISIBLE_JOBS = 4;

function progressPercent(progress: number): number | null {
  if (!Number.isFinite(progress) || progress < 0) return null;
  return Math.max(0, Math.min(1, progress)) * 100;
}

/**
 * The Dynamic-Island-style capsule: one live line for whatever FRIDAY is doing,
 * plus a count when several things run at once. Disappears the instant the last
 * activity finishes.
 *
 * Two states, one spring (stiffness 420 / damping 32):
 *   collapsed — dot, spinner, title, +N, elapsed seconds, hairline progress;
 *   expanded  — up to four jobs with detail lines, per-job progress and an ×
 *               that emits `cancel_activity`. Opens on hover or click and keeps
 *               a 900 ms grace period so brushing past it does not slam it shut.
 */
export function LiveActivityBar({
  activities,
  activeCount,
  onCancel,
  onInteraction,
}: LiveActivityBarProps) {
  const active = useMemo(
    () => activities.filter((activity) => activity.active).slice(0, MAX_VISIBLE_JOBS),
    [activities],
  );
  const head = active[0];
  const [now, setNow] = useState(() => Date.now());
  const [expanded, setExpanded] = useState(false);
  const [pinned, setPinned] = useState(false);
  const collapseTimer = useRef<number | null>(null);
  // Pinned while expanded: the panel stays hittable through the 900 ms grace,
  // so a slow click on a cancel button is never swallowed by click-through.
  const region = useInteractiveRegion({ pinned: expanded, onChange: onInteraction });
  const hasActive = activeCount > 0 && active.length > 0;

  // Keep the elapsed read-out honest without a per-frame tick.
  useEffect(() => {
    if (!hasActive) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [hasActive]);

  const cancelCollapse = useCallback(() => {
    if (collapseTimer.current !== null) {
      window.clearTimeout(collapseTimer.current);
      collapseTimer.current = null;
    }
  }, []);

  useEffect(() => cancelCollapse, [cancelCollapse]);

  // The capsule cannot stay open once the last job finishes.
  useEffect(() => {
    if (hasActive) return;
    setExpanded(false);
    setPinned(false);
    cancelCollapse();
  }, [hasActive, cancelCollapse]);

  if (!hasActive || !head) return null;

  const tint = KIND_TINT[head.kind] ?? "#06F7FF";
  const elapsed = Math.max(0, Math.round(now / 1000 - head.ts));
  const extra = Math.max(0, activeCount - 1);
  const headProgress = progressPercent(head.progress);

  const open = () => {
    cancelCollapse();
    setExpanded(true);
  };

  const graceClose = () => {
    cancelCollapse();
    if (pinned) return;
    collapseTimer.current = window.setTimeout(() => {
      collapseTimer.current = null;
      setExpanded(false);
    }, COLLAPSE_GRACE_MS);
  };

  const togglePinned = () => {
    cancelCollapse();
    if (pinned) {
      setPinned(false);
      return;
    }
    setPinned(true);
    setExpanded(true);
  };

  const { regionProps } = region;

  return (
    <motion.div
      layout
      className={`${regionProps.className} glass glass-pill flex flex-col overflow-hidden px-3 text-[11px] font-medium ${
        expanded ? "w-[min(360px,86vw)] py-2" : "h-7 max-w-[340px] justify-center"
      }`}
      initial={{ opacity: 0, y: 8, scale: 0.9 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: 6, scale: 0.92 }}
      transition={{ type: "spring", stiffness: 420, damping: 32 }}
      onPointerEnter={() => {
        regionProps.onPointerEnter();
        open();
      }}
      onPointerLeave={() => {
        regionProps.onPointerLeave();
        graceClose();
      }}
      onClick={togglePinned}
      onContextMenu={(event) => {
        if (!onCancel) return;
        event.preventDefault();
        onCancel(head.aid);
      }}
      title={onCancel ? "Right-click to cancel" : undefined}
      aria-live="polite"
      data-expanded={expanded ? "true" : "false"}
    >
      <div className="flex h-7 shrink-0 items-center gap-2">
        <span className="relative grid size-2.5 shrink-0 place-items-center">
          <motion.span
            className="absolute inset-0 rounded-full opacity-60"
            style={{ backgroundColor: tint }}
            animate={{ scale: [1, 1.55, 1], opacity: [0.55, 0.12, 0.55] }}
            transition={{ duration: 1.7, repeat: Infinity, ease: "easeInOut" }}
          />
          <span
            className="relative size-1.5 rounded-full"
            style={{ backgroundColor: tint }}
          />
        </span>

        {/* Spinner ring: a thin arc that rotates with the job. */}
        <span
          aria-hidden
          className="size-3 shrink-0 rounded-full border border-white/15"
          style={{ borderTopColor: tint, borderRightColor: tint }}
        >
          <motion.span
            className="block size-full"
            animate={{ rotate: 360 }}
            transition={{ duration: 1.6, repeat: Infinity, ease: "linear" }}
          />
        </span>

        <span className="shrink-0 uppercase tracking-[0.08em] text-white/55">
          {head.kind || "task"}
        </span>
        <span className="min-w-0 flex-1 truncate text-white/90">{head.title}</span>
        {extra > 0 ? (
          <span
            data-testid="activity-extra"
            className="shrink-0 rounded-full bg-white/10 px-1.5 text-[10px] tabular-nums text-white/70"
          >
            +{extra}
          </span>
        ) : null}
        {elapsed > 0 ? (
          <span className="shrink-0 tabular-nums text-white/40">{elapsed}s</span>
        ) : null}
        <span className="relative h-[2px] w-8 shrink-0 overflow-hidden rounded-full bg-white/10">
          {headProgress !== null ? (
            <motion.span
              className="absolute inset-y-0 left-0 rounded-full"
              style={{ backgroundColor: tint }}
              initial={false}
              animate={{ width: `${headProgress}%` }}
              transition={{ type: "spring", stiffness: 220, damping: 26 }}
            />
          ) : (
            <span
              className="activity-sweep absolute inset-y-0 -left-1/2 w-1/2 rounded-full"
              style={{
                background: `linear-gradient(90deg, transparent, ${tint})`,
              }}
            />
          )}
        </span>
      </div>

      <AnimatePresence initial={false}>
        {expanded ? (
          <motion.ul
            key="island-jobs"
            initial={{ opacity: 0, height: 0 }}
            animate={{ opacity: 1, height: "auto" }}
            exit={{ opacity: 0, height: 0 }}
            transition={{ type: "spring", stiffness: 420, damping: 32 }}
            className="mt-1.5 space-y-1 overflow-hidden border-t border-white/[0.07] pt-1.5"
            aria-label="Active jobs"
          >
            {active.map((job) => {
              const jobTint = KIND_TINT[job.kind] ?? "#06F7FF";
              const percent = progressPercent(job.progress);
              const jobElapsed = Math.max(0, Math.round(now / 1000 - job.ts));
              return (
                <li
                  key={job.aid}
                  data-testid={`activity-job-${job.aid}`}
                  className="flex items-center gap-2 rounded-lg px-1 py-0.5 hover:bg-white/[0.05]"
                >
                  <span
                    className="size-1.5 shrink-0 rounded-full"
                    style={{ backgroundColor: jobTint }}
                  />
                  <div className="min-w-0 flex-1">
                    <div className="flex items-baseline gap-1.5">
                      <span className="truncate text-[11.5px] text-white/90">
                        {job.title}
                      </span>
                      <span className="shrink-0 text-[9.5px] uppercase tracking-[0.07em] text-white/35">
                        {job.kind}
                      </span>
                      {jobElapsed > 0 ? (
                        <span className="ml-auto shrink-0 tabular-nums text-[9.5px] text-white/35">
                          {jobElapsed}s
                        </span>
                      ) : null}
                    </div>
                    {job.detail ? (
                      <p className="truncate text-[10px] text-white/45">{job.detail}</p>
                    ) : null}
                    <span className="mt-1 block h-[2px] w-full overflow-hidden rounded-full bg-white/10">
                      {percent !== null ? (
                        <motion.span
                          className="block h-full rounded-full"
                          style={{ backgroundColor: jobTint }}
                          initial={false}
                          animate={{ width: `${percent}%` }}
                          transition={{ type: "spring", stiffness: 220, damping: 26 }}
                        />
                      ) : (
                        <span
                          className="activity-sweep block h-full w-1/2 rounded-full"
                          style={{
                            background: `linear-gradient(90deg, transparent, ${jobTint})`,
                          }}
                        />
                      )}
                    </span>
                  </div>
                  {onCancel ? (
                    <button
                      type="button"
                      aria-label={`Cancel ${job.title || "job"}`}
                      className="focus-ring grid size-5 shrink-0 place-items-center rounded-full text-white/40 hover:bg-white/10 hover:text-white"
                      onClick={(event) => {
                        event.stopPropagation();
                        onCancel(job.aid);
                      }}
                    >
                      <X size={11} strokeWidth={2.6} />
                    </button>
                  ) : null}
                </li>
              );
            })}
          </motion.ul>
        ) : null}
      </AnimatePresence>
    </motion.div>
  );
}

export default LiveActivityBar;
