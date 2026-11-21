import { Plane } from "lucide-react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadString, type CardProps } from "./types";

/** `{"type":"flight","code":"AI101","from":"DEL","to":"DXB","gate":"14A"}` */
export function FlightCard({ card, index, onDismiss }: CardProps) {
  const payload = card.payload;
  const from = payloadString(payload.from) || payloadString(payload.origin);
  const to = payloadString(payload.to) || payloadString(payload.destination);
  const gate = payloadString(payload.gate);
  const status = payloadString(payload.status);
  const departure = payloadString(payload.departure);
  const code = payloadString(payload.code) || payloadString(payload.flight);

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      icon={<Plane size={13} strokeWidth={2.2} />}
      accent="#06F7FF"
      metric={code ? <span className="tabular-nums">{code}</span> : null}
    >
      {from || to ? (
        <div className="mb-1.5 flex items-center gap-2 text-[14px] font-semibold tracking-wide">
          <span>{from || "—"}</span>
          <Plane size={12} className="text-siri-cyan" aria-hidden />
          <span>{to || "—"}</span>
        </div>
      ) : null}
      {card.body ? <p className="truncate text-white/70">{card.body}</p> : null}
      <div className="mt-1.5 flex flex-wrap gap-1.5">
        {departure ? (
          <span className="rounded-full bg-white/[0.06] px-2 py-0.5 text-[10.5px] text-white/70">
            departs {departure}
          </span>
        ) : null}
        {gate ? (
          <span className="rounded-full bg-siri-cyan/15 px-2 py-0.5 text-[10.5px] font-medium text-siri-cyan">
            gate {gate}
          </span>
        ) : null}
        {status ? (
          <span className="rounded-full bg-white/[0.06] px-2 py-0.5 text-[10.5px] text-white/70">
            {status}
          </span>
        ) : null}
      </div>
    </BaseGlassCard>
  );
}

export default FlightCard;
