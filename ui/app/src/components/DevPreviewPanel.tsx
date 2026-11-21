import { AnimatePresence, motion } from "motion/react";
import { Bug, ChevronDown, Power } from "lucide-react";
import { useState } from "react";

import type { AssistantState, CardKind } from "../types/protocol";

export interface DevPreviewPanelProps {
  /** Only visible when no backend is attached (see StageApp). */
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onState: (state: AssistantState) => void;
  onCard: (kind: CardKind) => void;
  onAmplitude: (value: number, bands?: { low: number; mid: number; high: number; source: "mic" | "tts" }) => void;
  onToggleInspector: () => void;
  onError: () => void;
  onShutdown: () => void;
  onClear: () => void;
}

const STATES: AssistantState[] = ["LISTENING", "THINKING", "PROCESSING", "SPEAKING", "MUTED"];
const KINDS: CardKind[] = [
  "weather",
  "calendar",
  "email",
  "flight",
  "telemetry",
  "control",
  "document",
  "confirmation",
  "text",
];

/**
 * Developer bench for the overlay.
 *
 * Only rendered when `npx vite` is opened in a browser with no WebSocket
 * attached — never in the packaged app, and never as a "fallback UI": the
 * Python controller is the only production transport, this panel just exercises
 * the same reducer the socket feeds.
 */
export function DevPreviewPanel({
  open,
  onOpenChange,
  onState,
  onCard,
  onAmplitude,
  onToggleInspector,
  onError,
  onShutdown,
  onClear,
}: DevPreviewPanelProps) {
  const [amplitude, setAmplitude] = useState(0.25);
  const [bands, setBands] = useState<{ low: number; mid: number; high: number; source: "mic" | "tts" } | undefined>(undefined);

  return (
    <div className="fixed bottom-4 left-4 z-50 w-[268px]">
      <button
        type="button"
        onClick={() => onOpenChange(!open)}
        className="glass glass-pill flex h-8 w-full items-center gap-2 px-3 text-[11px] font-semibold uppercase tracking-[0.08em] text-siri-cyan/90"
        aria-expanded={open}
      >
        <Bug size={12} strokeWidth={2.4} />
        dev preview
        <span className="ml-auto text-[9.5px] font-medium normal-case tracking-normal text-white/35">
          no backend
        </span>
        <ChevronDown
          size={12}
          className="text-white/40"
          style={{ transform: open ? "rotate(180deg)" : "none" }}
        />
      </button>

      <AnimatePresence>
        {open ? (
          <motion.div
            className="glass mt-2 space-y-3 p-3 text-[11px]"
            initial={{ opacity: 0, y: 8, scale: 0.98 }}
            animate={{ opacity: 1, y: 0, scale: 1 }}
            exit={{ opacity: 0, y: 6, scale: 0.98 }}
            transition={{ type: "spring", stiffness: 420, damping: 32 }}
          >
            <section>
              <p className="mb-1.5 text-[9.5px] font-semibold uppercase tracking-[0.1em] text-white/35">
                state
              </p>
              <div className="flex flex-wrap gap-1">
                {STATES.map((state) => (
                  <button
                    key={state}
                    type="button"
                    onClick={() => onState(state)}
                    className="rounded-full bg-white/[0.07] px-2 py-0.5 text-[10px] font-medium text-white/70 hairline hover:bg-white/[0.14] hover:text-white"
                  >
                    {state}
                  </button>
                ))}
                <button
                  type="button"
                  onClick={onError}
                  className="rounded-full bg-siri-red/15 px-2 py-0.5 text-[10px] font-medium text-siri-red hover:bg-siri-red/25"
                >
                  error
                </button>
              </div>
            </section>

            <section>
              <p className="mb-1.5 text-[9.5px] font-semibold uppercase tracking-[0.1em] text-white/35">
                inject card
              </p>
              <div className="flex flex-wrap gap-1">
                {KINDS.map((kind) => (
                  <button
                    key={kind}
                    type="button"
                    onClick={() => onCard(kind)}
                    className="rounded-full bg-white/[0.07] px-2 py-0.5 text-[10px] font-medium text-white/70 hairline hover:bg-white/[0.14] hover:text-white"
                  >
                    {kind}
                  </button>
                ))}
              </div>
            </section>

            <section>
              <label className="mb-1 flex items-center justify-between text-[9.5px] font-semibold uppercase tracking-[0.1em] text-white/35">
                mic amplitude
                <span className="tabular-nums text-siri-cyan/80">
                  {amplitude.toFixed(2)}
                </span>
              </label>
              <input
                type="range"
                min={0}
                max={1}
                step={0.01}
                value={amplitude}
                aria-label="Simulated microphone amplitude"
                onChange={(event) => {
                  const value = Number(event.target.value);
                  setAmplitude(value);
                  onAmplitude(value);
                }}
                className="w-full accent-siri-cyan"
              />
              <div className="flex flex-wrap gap-1 mt-1">
                <button
                  type="button"
                  onClick={() => {
                    const newBands = { low: 0.8, mid: 0.5, high: 0.2, source: "mic" as const };
                    setBands(newBands);
                    onAmplitude(amplitude, newBands);
                  }}
                  className="rounded-full bg-white/[0.07] px-2 py-0.5 text-[10px] font-medium text-white/70 hairline hover:bg-white/[0.14] hover:text-white"
                >
                  mic bands
                </button>
                <button
                  type="button"
                  onClick={() => {
                    const newBands = { low: 0.2, mid: 0.6, high: 0.9, source: "tts" as const };
                    setBands(newBands);
                    onAmplitude(amplitude, newBands);
                  }}
                  className="rounded-full bg-white/[0.07] px-2 py-0.5 text-[10px] font-medium text-white/70 hairline hover:bg-white/[0.14] hover:text-white"
                >
                  tts bands
                </button>
              </div>
            </section>

            <section className="flex items-center gap-1">
              <button
                type="button"
                onClick={onToggleInspector}
                className="flex-1 rounded-full bg-white/[0.07] px-2 py-1 text-[10px] font-medium text-white/75 hairline hover:bg-white/[0.14]"
              >
                inspector
              </button>
              <button
                type="button"
                onClick={onClear}
                className="flex-1 rounded-full bg-white/[0.07] px-2 py-1 text-[10px] font-medium text-white/75 hairline hover:bg-white/[0.14]"
              >
                clear
              </button>
              <button
                type="button"
                onClick={onShutdown}
                className="grid size-6 place-items-center rounded-full bg-siri-red/15 text-siri-red hover:bg-siri-red/25"
                aria-label="Simulate backend shutdown"
              >
                <Power size={11} strokeWidth={2.4} />
              </button>
            </section>
          </motion.div>
        ) : null}
      </AnimatePresence>
    </div>
  );
}

export default DevPreviewPanel;
