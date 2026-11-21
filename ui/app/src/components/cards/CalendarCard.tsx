import { Calendar } from "lucide-react";
import { motion } from "motion/react";

import { BaseGlassCard } from "./BaseGlassCard";
import { formatClock, payloadList, payloadString, type CardProps } from "./types";

/**
 * `{"type":"calendar","events":[{"time":"09:00","title":"Standup"}]}`
 *
 * The first row is "next up": it gets a glowing indicator dot and a brighter
 * time column, with every time in a fixed-width tabular slot so the titles line
 * up like a calendar list rather than a paragraph.
 */
export function CalendarCard({ card, index, onDismiss, onInteraction }: CardProps) {
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
      onInteraction={onInteraction}
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
        {rows.map((row, i) => {
          const next = i === 0;
          return (
            <li
              key={`${row.title}-${i}`}
              className={`flex items-baseline gap-2 rounded-lg ${
                next ? "bg-white/[0.05] py-0.5 pl-1 pr-1.5" : ""
              }`}
            >
              {next ? (
                <motion.span
                  aria-hidden
                  data-testid="calendar-next-dot"
                  className="size-1.5 translate-y-[-1px] shrink-0 rounded-full"
                  style={{ backgroundColor: "#06F7FF" }}
                  animate={{ opacity: [0.55, 1, 0.55], scale: [0.9, 1.18, 0.9] }}
                  transition={{ duration: 2.1, repeat: Infinity, ease: "easeInOut" }}
                />
              ) : (
                <span aria-hidden className="size-1.5 shrink-0" />
              )}
              <span
                className={`w-[42px] shrink-0 text-[10.5px] font-semibold tabular-nums ${
                  next ? "text-siri-cyan" : "text-white/45"
                }`}
              >
                {row.time}
              </span>
              <span
                className={`truncate ${next ? "font-medium text-white/92" : "text-white/80"}`}
              >
                {row.title}
              </span>
            </li>
          );
        })}
        {rows.length === 0 ? <li className="text-dim">Nothing scheduled.</li> : null}
      </ul>
    </BaseGlassCard>
  );
}

export default CalendarCard;
