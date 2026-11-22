import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";

export interface LiveTranscriptProps {
  text: string;
  /** Remain on screen this long after the last update (3500 ms by default). */
  holdMs?: number;
  speaking?: boolean;
}

/**
 * The single live line of speech, centred above the orb.
 *
 * Re-rendering on every incoming word must never remount the pill: one stable
 * `motion.div` (no `key={text}`) with `layout` glides in once and then morphs
 * its own height as additional lines stream in, so there is zero
 * unmount/remount flicker and zero token pop. `line-clamp-3` caps it at three
 * wrapped lines — the overlay stays as quiet as Siri.
 *
 * Deliberately not a transcript *panel*: the full history lives in the
 * inspector.
 */
export function LiveTranscript({ text, holdMs = 3500, speaking = false }: LiveTranscriptProps) {
  const [shown, setShown] = useState("");
  const [active, setActive] = useState(false);

  useEffect(() => {
    const clean = text.trim();
    if (!clean) {
      // Dismissal clears the line immediately (Python sends "").
      setShown("");
      setActive(false);
      return;
    }
    setShown(clean);
    setActive(true);
    // Speak longer than the hold while the utterance is still streaming.
    const ttl = speaking ? Math.max(holdMs, 4000) : holdMs;
    const timer = window.setTimeout(() => {
      setShown("");
      setActive(false);
    }, ttl);
    return () => window.clearTimeout(timer);
  }, [text, holdMs, speaking]);

  return (
    <div className="pointer-events-none flex min-h-[34px] w-full items-end justify-center">
      <AnimatePresence>
        {shown ? (
          <motion.div
            key="live-transcript"
            layout
            className="transcript-veil flex max-w-[440px] items-start gap-2 rounded-2xl px-3.5 py-1.5 text-left shadow-[0_10px_30px_-16px_rgba(0,0,0,0.8)]"
            initial={{ opacity: 0, y: 6, filter: "blur(4px)", scale: 0.98 }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)", scale: 1 }}
            exit={{ opacity: 0, y: -4, filter: "blur(3px)", scale: 0.99 }}
            transition={{ type: "spring", stiffness: 380, damping: 30 }}
            role="status"
            aria-live="polite"
            title={shown}
          >
            <motion.span
              aria-hidden
              data-testid="transcript-pulse"
              data-active={active ? "true" : "false"}
              className="mt-[6px] size-1.5 shrink-0 rounded-full"
              style={{ backgroundColor: "#06F7FF" }}
              animate={
                active
                  ? { opacity: [0.35, 1, 0.35], scale: [0.85, 1.15, 0.85] }
                  : { opacity: 0.45, scale: 1 }
              }
              transition={
                active
                  ? { duration: 1.15, repeat: Infinity, ease: "easeInOut" }
                  : { duration: 0.2 }
              }
            />
            <p className="line-clamp-3 whitespace-pre-wrap break-words text-[12.5px] font-medium leading-[1.4] tracking-[-0.005em] text-white/92">
              {shown}
            </p>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

export default LiveTranscript;
