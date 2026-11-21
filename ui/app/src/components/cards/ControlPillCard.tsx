import { Minus, Plus, SlidersHorizontal } from "lucide-react";
import { motion } from "motion/react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadList, payloadNumber, payloadString, type CardProps } from "./types";

/** Seconds → the phrase the backend understands ("set volume to 65%"). */
function controlCommand(label: string, next: number, unit: string): string {
  const target = String(Math.round(next));
  return `set ${label.toLowerCase()} to ${target}${unit === "%" ? "%" : ""}`;
}

/**
 * `{"type":"control","label":"Volume","value":60,"unit":"%"}`
 *
 * The steppers are real controls, not decoration: −/+ emit a command through
 * `onReply`, which the stage forwards as `submit_text`, so clicking works
 * exactly like saying "set volume to 55%". A card may also supply explicit
 * `payload.actions` (`[{label, command}]`) when a device has discrete modes.
 */
export function ControlPillCard({ card, index, onDismiss, onReply, onInteraction }: CardProps) {
  const label = payloadString(card.payload.label) || card.title || "Control";
  const value = payloadNumber(card.payload.value);
  const unit = payloadString(card.payload.unit) || (value !== null ? "%" : "");
  const step = payloadNumber(card.payload.step) ?? 5;
  const percent = value !== null ? Math.max(0, Math.min(100, value)) : null;
  const actions = payloadList(card.payload.actions).slice(0, 3);

  const nudge = (delta: number) => {
    if (value === null) return;
    const next = Math.max(0, Math.min(100, value + delta));
    onReply?.(controlCommand(label, next, unit));
  };

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      onInteraction={onInteraction}
      icon={<SlidersHorizontal size={13} strokeWidth={2.2} />}
      accent="#FF2D9F"
      metric={
        value !== null ? (
          <span className="rounded-full bg-siri-magenta/15 px-2.5 py-0.5 text-[12px] font-semibold tabular-nums text-siri-magenta">
            {value}
            {unit}
          </span>
        ) : null
      }
    >
      <div className="flex items-center gap-2">
        <span className="text-[13px] font-medium text-white/90">{label}</span>
        {card.body ? <span className="truncate text-white/60">{card.body}</span> : null}

        {percent !== null ? (
          <div className="ml-auto flex items-center gap-1">
            <motion.button
              type="button"
              aria-label={`Decrease ${label}`}
              disabled={!onReply}
              whileTap={{ scale: 0.9 }}
              whileHover={{ scale: 1.06 }}
              transition={{ type: "spring", stiffness: 420, damping: 24 }}
              className="focus-ring grid size-6 place-items-center rounded-full bg-white/[0.08] text-white/75 hairline hover:bg-white/[0.16] hover:text-white disabled:opacity-40"
              onClick={() => nudge(-step)}
            >
              <Minus size={11} strokeWidth={2.8} />
            </motion.button>
            <motion.button
              type="button"
              aria-label={`Increase ${label}`}
              disabled={!onReply}
              whileTap={{ scale: 0.9 }}
              whileHover={{ scale: 1.06 }}
              transition={{ type: "spring", stiffness: 420, damping: 24 }}
              className="focus-ring grid size-6 place-items-center rounded-full bg-white/[0.08] text-white/75 hairline hover:bg-white/[0.16] hover:text-white disabled:opacity-40"
              onClick={() => nudge(step)}
            >
              <Plus size={11} strokeWidth={2.8} />
            </motion.button>
          </div>
        ) : null}
      </div>

      {percent !== null ? (
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/[0.08] hairline">
          <motion.div
            className="h-full rounded-full"
            style={{
              background:
                "linear-gradient(90deg, #06F7FF 0%, #1E5FFF 42%, #B030FF 74%, #FF2D9F 100%)",
            }}
            initial={{ width: 0 }}
            animate={{ width: `${percent}%` }}
            transition={{ type: "spring", stiffness: 220, damping: 28 }}
          />
        </div>
      ) : null}

      {actions.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {actions.map((action, i) => {
            const command = payloadString(action.command);
            const actionLabel = payloadString(action.label) || command;
            if (!command) return null;
            return (
              <button
                key={`${actionLabel}-${i}`}
                type="button"
                className="focus-ring rounded-full bg-white/[0.07] px-2.5 py-0.5 text-[10.5px] font-medium text-white/75 hairline hover:bg-white/[0.14] hover:text-white"
                onClick={() => onReply?.(command)}
              >
                {actionLabel}
              </button>
            );
          })}
        </div>
      ) : null}
    </BaseGlassCard>
  );
}

export default ControlPillCard;
