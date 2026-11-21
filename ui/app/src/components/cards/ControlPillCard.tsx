import { SlidersHorizontal } from "lucide-react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadNumber, payloadString, type CardProps } from "./types";

/** `{"type":"control","label":"Volume","value":60,"unit":"%"}` */
export function ControlPillCard({ card, index, onDismiss }: CardProps) {
  const label = payloadString(card.payload.label) || card.title || "Control";
  const value = payloadNumber(card.payload.value);
  const unit = payloadString(card.payload.unit) || (value !== null ? "%" : "");
  const percent = value !== null ? Math.max(0, Math.min(100, value)) : null;

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
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
      </div>
      {percent !== null ? (
        <div className="mt-2 h-2 overflow-hidden rounded-full bg-white/[0.08] hairline">
          <div
            className="h-full rounded-full"
            style={{
              width: `${percent}%`,
              background:
                "linear-gradient(90deg, #06F7FF 0%, #1E5FFF 42%, #B030FF 74%, #FF2D9F 100%)",
            }}
          />
        </div>
      ) : null}
    </BaseGlassCard>
  );
}

export default ControlPillCard;
