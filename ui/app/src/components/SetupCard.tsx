import { Eye, EyeOff, KeyRound, Loader2 } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useState } from "react";

import { useInteractiveRegion } from "../hooks/useInteractiveRegion";

export interface SetupCardProps {
  open: boolean;
  onSubmitKey: (key: string) => void;
  onInteraction?: (inside: boolean) => void;
  /** Rejection text from the backend (e.g. the key was refused). */
  error?: string;
}

/**
 * First-run gate. Shown when the backend reports `setup.visible` (no Gemini key
 * on disk). It cannot be dismissed: FRIDAY cannot think without a key, and a
 * silent overlay would look like a crash.
 *
 * The key is a password field by default, with an explicit reveal toggle so a
 * pasted key can be verified before it is saved; saving shows a spinner and a
 * backend rejection is surfaced inline instead of silently resetting the form.
 */
export function SetupCard({ open, onSubmitKey, onInteraction, error: backendError }: SetupCardProps) {
  const [key, setKey] = useState("");
  const [error, setError] = useState("");
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const region = useInteractiveRegion({
    pinned: open,
    onChange: (inside) => onInteraction?.(inside),
  });

  // A new key attempt (accepted or refused) always ends the spinner.
  useEffect(() => {
    setBusy(false);
    if (backendError) setError(backendError);
  }, [backendError, open]);

  if (!open) return null;

  const submit = () => {
    const trimmed = key.trim();
    if (busy) return;
    if (trimmed.length < 12) {
      setError("That key looks too short.");
      return;
    }
    setError("");
    setBusy(true);
    onSubmitKey(trimmed);
  };

  const message = error || backendError || "";

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
        <div className="relative min-w-0 flex-1">
          <input
            type={visible ? "text" : "password"}
            value={key}
            onChange={(event) => {
              setKey(event.target.value);
              if (error) setError("");
            }}
            placeholder="AIza…"
            aria-label="Gemini API key"
            autoFocus
            spellCheck={false}
            autoComplete="off"
            disabled={busy}
            className="h-9 w-full min-w-0 rounded-full bg-white/[0.06] pl-3.5 pr-9 text-[12.5px] font-medium tracking-wide text-siri-text outline-none hairline placeholder:text-white/30 focus:bg-white/[0.1] disabled:opacity-60"
          />
          <button
            type="button"
            aria-label={visible ? "Hide API key" : "Show API key"}
            aria-pressed={visible}
            tabIndex={0}
            className="focus-ring absolute right-1 top-1/2 grid size-7 -translate-y-1/2 place-items-center rounded-full text-white/45 hover:bg-white/10 hover:text-white"
            onClick={() => setVisible((value) => !value)}
          >
            {visible ? <EyeOff size={13} strokeWidth={2.2} /> : <Eye size={13} strokeWidth={2.2} />}
          </button>
        </div>
        <motion.button
          type="submit"
          disabled={busy}
          whileTap={{ scale: 0.95 }}
          transition={{ type: "spring", stiffness: 420, damping: 26 }}
          className="focus-ring flex h-9 shrink-0 items-center gap-1.5 rounded-full bg-siri-cyan px-4 text-[12px] font-semibold text-black hover:bg-white disabled:opacity-70"
        >
          {busy ? <Loader2 size={12} className="animate-spin" aria-hidden /> : null}
          {busy ? "Saving" : "Save"}
        </motion.button>
      </form>

      {message ? (
        <p role="alert" className="mt-2 text-[11px] font-medium text-siri-red">
          {message}
        </p>
      ) : (
        <p className="mt-2 text-[10.5px] text-white/35">
          aistudio.google.com/apikey → “Create API key”
        </p>
      )}
    </motion.section>
  );
}

export default SetupCard;
