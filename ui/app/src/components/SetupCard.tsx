import { KeyRound } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";

import { useInteractiveRegion } from "../hooks/useInteractiveRegion";

export interface SetupCardProps {
  open: boolean;
  onSubmitKey: (key: string) => void;
  onInteraction?: (inside: boolean) => void;
}

/**
 * First-run gate. Shown when the backend reports `setup.visible` (no Gemini key
 * on disk). It cannot be dismissed: FRIDAY cannot think without a key, and a
 * silent overlay would look like a crash.
 */
export function SetupCard({ open, onSubmitKey, onInteraction }: SetupCardProps) {
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const region = useInteractiveRegion({
    pinned: open,
    onChange: (inside) => onInteraction?.(inside),
  });

  if (!open) return null;

  const submit = () => {
    const trimmed = key.trim();
    if (trimmed.length < 12) {
      setError("That key looks too short.");
      return;
    }
    setError("");
    onSubmitKey(trimmed);
  };

  return (
    <motion.section
      {...region.regionProps}
      className={`${region.regionProps.className} glass no-drag w-[min(430px,88vw)] px-5 pb-4 pt-4`}
      initial={{ opacity: 0, y: 14, scale: 0.96 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      transition={{ type: "spring", stiffness: 380, damping: 30 }}
      role="dialog"
      aria-modal="true"
      aria-label="FRIDAY setup"
    >
      <header className="mb-2 flex items-center gap-2">
        <span className="grid size-7 place-items-center rounded-full bg-siri-cyan/15 text-siri-cyan">
          <KeyRound size={14} strokeWidth={2.2} />
        </span>
        <div>
          <h2 className="text-[13.5px] font-semibold tracking-[-0.01em] text-siri-text">
            Connect FRIDAY
          </h2>
          <p className="text-[10.5px] font-medium uppercase tracking-[0.08em] text-faint">
            one-time setup
          </p>
        </div>
      </header>

      <p className="mb-3 text-[12px] leading-relaxed text-white/70">
        Paste a Google AI Studio key to let FRIDAY think, speak and see. It is
        stored locally in <code className="rounded bg-white/10 px-1">api_keys.json</code>{" "}
        and never leaves this machine.
      </p>

      <form
        className="flex items-center gap-2"
        onSubmit={(event) => {
          event.preventDefault();
          submit();
        }}
      >
        <input
          type="password"
          value={key}
          onChange={(event) => setKey(event.target.value)}
          placeholder="AIza…"
          aria-label="Gemini API key"
          autoFocus
          spellCheck={false}
          autoComplete="off"
          className="h-9 min-w-0 flex-1 rounded-full bg-white/[0.06] px-3.5 text-[12.5px] font-medium tracking-wide text-siri-text outline-none hairline placeholder:text-white/30 focus:bg-white/[0.1]"
        />
        <button
          type="submit"
          className="focus-ring h-9 shrink-0 rounded-full bg-siri-cyan px-4 text-[12px] font-semibold text-black hover:bg-white"
        >
          Save
        </button>
      </form>

      {error ? (
        <p className="mt-2 text-[11px] font-medium text-siri-red">{error}</p>
      ) : (
        <p className="mt-2 text-[10.5px] text-white/35">
          aistudio.google.com/apikey → “Create API key”
        </p>
      )}
    </motion.section>
  );
}

export default SetupCard;
