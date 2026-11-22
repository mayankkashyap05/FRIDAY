import { Plane } from "lucide-react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadString, type CardProps } from "./types";

/**
 * `{"type":"flight","code":"AI101","from":"DEL","to":"DXB","gate":"14A"}`
 *
 * Styled as a boarding-pass plate: the two airport codes sit at the ends of a
 * drawn hairline route with endpoint dots and a centred plane, so the route is
 * legible at a glance instead of being three words in a row.
 */
export function FlightCard({ card, index, onDismiss, onInteraction }: CardProps) {
  const payload = card.payload;
  const from = payloadString(payload.from) || payloadString(payload.origin);
  const to = payloadString(payload.to) || payloadString(payload.destination);
  const gate = payloadString(payload.gate);
  const status = payloadString(payload.status);
  const departure = payloadString(payload.departure);
  const code = payloadString(payload.code) || payloadString(payload.flight);

  const pills = [
    departure ? `departs ${departure}` : "",
    gate ? `gate ${gate}` : "",
    status,
  ].filter(Boolean);

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      onInteraction={onInteraction}
      icon={<Plane size={13} strokeWidth={2.2} />}
      accent="#06F7FF"
      metric={
        code ? (
          <span className="rounded-md bg-siri-cyan/15 px-1.5 py-0.5 text-[10.5px] font-semibold tracking-[0.06em] tabular-nums text-siri-cyan">
            {code}
          </span>
        ) : null
      }
    >
      {from || to ? (
        <div className="flex items-center gap-2 py-0.5">
          <span className="text-[17px] font-semibold leading-none tracking-[0.04em] tabular-nums text-white">
            {from || "—"}
          </span>
          <span className="relative flex min-w-0 flex-1 items-center" aria-hidden>
            <span className="size-1 shrink-0 rounded-full bg-siri-cyan/80" />
            <span className="h-px flex-1 bg-gradient-to-r from-siri-cyan/60 via-white/25 to-siri-violet/60" />
            <Plane size={11} className="mx-1 shrink-0 text-siri-cyan" strokeWidth={2} />
            <span className="h-px flex-1 bg-gradient-to-r from-siri-violet/60 via-white/25 to-siri-cyan/60" />
            <span className="size-1 shrink-0 rounded-full bg-siri-violet/80" />
          </span>
          <span className="text-[17px] font-semibold leading-none tracking-[0.04em] tabular-nums text-white">
            {to || "—"}
          </span>
        </div>
      ) : null}

      {card.body ? <p className="mt-1 truncate text-white/70">{card.body}</p> : null}

      {pills.length ? (
        <div className="mt-1.5 flex flex-wrap gap-1.5">
          {pills.map((pill, i) => (
            <span
              key={`${pill}-${i}`}
              className={`rounded-full px-2 py-0.5 text-[10.5px] ${
                i === 1
                  ? "bg-siri-cyan/15 font-medium text-siri-cyan"
                  : "bg-white/[0.06] text-white/70"
              }`}
            >
              {pill}
            </span>
          ))}
        </div>
      ) : null}
    </BaseGlassCard>
  );
}

export default FlightCard;
