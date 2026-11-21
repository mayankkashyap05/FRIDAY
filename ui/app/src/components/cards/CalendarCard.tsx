import { Calendar } from "lucide-react";

import { BaseGlassCard } from "./BaseGlassCard";
import { formatClock, payloadList, payloadString, type CardProps } from "./types";

/** `{"type":"calendar","events":[{"time":"09:00","title":"Standup"}]}` */
export function CalendarCard({ card, index, onDismiss }: CardProps) {
  const events = payloadList(card.payload.events);
  const rows = events.length
    ? events.slice(0, 4).map((event) => ({
        time:
          payloadString(event.time) ||
          payloadString(event.start) ||
          formatClock(Number(event.ts ?? 0)),
        title:
          payloadString(event.title) ||
          payloadString(event.summary) ||
          payloadString(event.name) ||
          "Untitled",
      }))
    : card.body
        .split("\n")
        .filter(Boolean)
        .slice(0, 4)
        .map((line) => ({ time: "", title: line }));

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      icon={<Calendar size={13} strokeWidth={2.2} />}
      accent="#1E5FFF"
      metric={
        events.length ? (
          <span className="text-[12px] font-medium tabular-nums text-dim">
            {events.length} event{events.length === 1 ? "" : "s"}
          </span>
        ) : null
      }
    >
      <ul className="space-y-1">
        {rows.map((row, i) => (
          <li key={`${row.title}-${i}`} className="flex items-baseline gap-2">
            {row.time ? (
              <span className="shrink-0 rounded-md bg-white/[0.07] px-1.5 py-0.5 text-[10.5px] font-semibold tabular-nums text-siri-cyan/90">
                {row.time}
              </span>
            ) : null}
            <span className="truncate text-white/85">{row.title}</span>
          </li>
        ))}
        {rows.length === 0 ? <li className="text-dim">Nothing scheduled.</li> : null}
      </ul>
    </BaseGlassCard>
  );
}

export default CalendarCard;
