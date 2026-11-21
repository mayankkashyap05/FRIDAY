import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";

export interface LiveTranscriptProps {
  text: string;
  /** Keep the last utterance on screen a little longer while speaking. */
  holdMs?: number;
  speaking?: boolean;
}

/**
 * One line of live speech, centred above the orb, fading out by itself.
 *
 * Deliberately not a transcript *panel*: the full history lives in the
 * inspector, and the overlay must stay as quiet as Siri.
 */
export function LiveTranscript({ text, holdMs = 2600, speaking = false }: LiveTranscriptProps) {
  const [shown, setShown] = useState("");

  useEffect(() => {
    if (!text) return;
    setShown(text.trim());
    const ttl = speaking ? Math.max(holdMs, 4000) : holdMs;
    const timer = window.setTimeout(() => setShown(""), ttl);
    return () => window.clearTimeout(timer);
  }, [text, holdMs, speaking]);

  return (
    <div className="pointer-events-none flex h-[34px] w-full items-end justify-center">
      <AnimatePresence>
        {shown ? (
          <motion.p
            key={shown}
            className="max-w-[460px] truncate rounded-full bg-black/45 px-3 py-1 text-[12.5px] font-medium tracking-[-0.005em] text-white/92 backdrop-blur-md hairline"
            initial={{ opacity: 0, y: 6, filter: "blur(4px)" }}
            animate={{ opacity: 1, y: 0, filter: "blur(0px)" }}
            exit={{ opacity: 0, y: -4, filter: "blur(3px)" }}
            transition={{ duration: 0.26, ease: "easeOut" }}
            title={shown}
          >
            {shown}
          </motion.p>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

export default LiveTranscript;
