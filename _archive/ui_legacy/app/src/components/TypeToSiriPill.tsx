import { CornerDownLeft, Sparkles, X } from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { useEffect, useRef, useState } from "react";

import { useInteractiveRegion } from "../hooks/useInteractiveRegion";

export interface TypeToSiriPillProps {
  open: boolean;
  onSubmit: (text: string) => void;
  onClose: () => void;
  onInteraction?: (inside: boolean) => void;
  /** Report keyboard focus so Python can hold the window interactive. */
  onTyping?: (typing: boolean) => void;
  placeholder?: string;
}

/**
 * The keyboard entry point: click the orb, type, Enter.
 *
 * While open the stage window is *pinned* interactive (a text field cannot work
 * in click-through mode), and Esc returns it to click-through immediately. The
 * pill springs directly above the orb, and focus lights an iridescent halo
 * behind the glass — opacity only, so the layout never jitters.
 */
export function TypeToSiriPill({
  open,
  onSubmit,
  onClose,
  onInteraction,
  onTyping,
  placeholder = "Ask FRIDAY…",
}: TypeToSiriPillProps) {
  const [value, setValue] = useState("");
  const [focused, setFocused] = useState(false);
  const inputRef = useRef<HTMLInputElement | null>(null);
  const region = useInteractiveRegion({
    pinned: open,
    onChange: (inside) => onInteraction?.(inside),
  });

  // `open` is the only trigger: `onTyping` is an inline callback from the
  // stage, so depending on it would re-run this effect on every render.
  const onTypingRef = useRef(onTyping);
  onTypingRef.current = onTyping;

  useEffect(() => {
    if (!open) {
      setValue("");
      setFocused(false);
      onTypingRef.current?.(false);
      return;
    }
    const timer = window.setTimeout(() => inputRef.current?.focus(), 40);
    return () => window.clearTimeout(timer);
  }, [open]);

  const submit = () => {
    const text = value.trim();
    if (!text) {
      onClose();
      return;
    }
    onSubmit(text);
    setValue("");
    onClose();
  };

  const hasText = value.trim().length > 0;

  return (
    <AnimatePresence>
      {open ? (
        <motion.div
          key="type-to-siri"
          className="pointer-events-none relative w-[min(430px,86vw)]"
          initial={{ opacity: 0, y: 10, scale: 0.96 }}
          animate={{ opacity: 1, y: 0, scale: 1 }}
          exit={{ opacity: 0, y: 8, scale: 0.97 }}
          transition={{ type: "spring", stiffness: 460, damping: 32 }}
        >
          {/* Iridescent halo: pure opacity fade, no layout involvement. */}
          <motion.span
            aria-hidden
            data-testid="type-pill-glow"
            className="siri-iridescent pointer-events-none absolute -inset-[2px] rounded-full"
            initial={false}
            animate={{ opacity: focused ? 0.55 : 0 }}
            transition={{ duration: 0.24, ease: "easeOut" }}
          />
          <motion.form
            {...region.regionProps}
            className={`${region.regionProps.className} glass glass-pill no-drag relative flex h-10 w-full items-center gap-2 pl-3.5 pr-2`}
            onSubmit={(event) => {
              event.preventDefault();
              submit();
            }}
            role="search"
            aria-label="Type to FRIDAY"
          >
            <Sparkles
              size={14}
              strokeWidth={2.2}
              className={focused ? "shrink-0 text-siri-cyan" : "shrink-0 text-siri-cyan/70"}
              aria-hidden
            />
            <input
              ref={inputRef}
              value={value}
              onChange={(event) => {
                setValue(event.target.value);
                onTyping?.(event.target.value.length > 0);
              }}
              onFocus={() => setFocused(true)}
              onBlur={() => setFocused(false)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  event.preventDefault();
                  onClose();
                }
              }}
              placeholder={placeholder}
              aria-label="Message FRIDAY"
              className="min-w-0 flex-1 bg-transparent text-[13px] font-medium text-siri-text outline-none placeholder:text-white/35"
              spellCheck={false}
              autoComplete="off"
            />
            <motion.button
              type="submit"
              aria-label="Send"
              whileTap={{ scale: 0.9 }}
              transition={{ type: "spring", stiffness: 420, damping: 24 }}
              className={`focus-ring grid size-7 shrink-0 place-items-center rounded-full ${
                hasText
                  ? "bg-siri-cyan text-black shadow-[0_0_14px_rgba(6,247,255,0.45)]"
                  : "bg-white/[0.07] text-white/70 hover:bg-white/[0.14] hover:text-white"
              }`}
            >
              <CornerDownLeft size={13} strokeWidth={2.4} />
            </motion.button>
            <motion.button
              type="button"
              aria-label="Close"
              whileTap={{ scale: 0.9 }}
              transition={{ type: "spring", stiffness: 420, damping: 24 }}
              className="focus-ring grid size-7 shrink-0 place-items-center rounded-full text-white/45 hover:bg-white/10 hover:text-white"
              onClick={onClose}
            >
              <X size={13} strokeWidth={2.3} />
            </motion.button>
          </motion.form>
        </motion.div>
      ) : null}
    </AnimatePresence>
  );
}

export default TypeToSiriPill;
