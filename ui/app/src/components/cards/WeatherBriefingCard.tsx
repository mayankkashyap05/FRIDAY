import { CloudSun } from "lucide-react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadNumber, payloadString, type CardProps } from "./types";

/** `{"type":"weather","city":"…","temp_c":29,"condition":"rain","humidity":81}` */
export function WeatherBriefingCard({ card, index, onDismiss }: CardProps) {
  const payload = card.payload;
  const temp = payloadNumber(payload.temp_c) ?? payloadNumber(payload.temperature);
  const condition = payloadString(payload.condition) || payloadString(payload.summary);
  const humidity = payloadNumber(payload.humidity);
  const wind = payloadString(payload.wind) || (payloadNumber(payload.wind)?.toString() ?? "");
  const city = payloadString(payload.city);

  const chips = [
    humidity !== null ? `${Math.round(humidity)}% humidity` : "",
    wind ? `wind ${wind}` : "",
    condition ? condition : "",
  ].filter(Boolean);

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      icon={<CloudSun size={13} strokeWidth={2.2} />}
      accent="#06F7FF"
      metric={
        temp !== null ? (
          <span className="tabular-nums">
            {Math.round(temp)}
            <span className="text-[11px] align-top text-dim">°C</span>
          </span>
        ) : null
      }
    >
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        {city ? <span className="text-[13px] font-medium text-white">{city}</span> : null}
        {card.body ? <span className="text-white/70">{card.body}</span> : null}
      </div>
      {chips.length ? (
        <div className="mt-2 flex flex-wrap gap-1.5">
          {chips.slice(0, 3).map((chip) => (
            <span
              key={chip}
              className="rounded-full bg-white/[0.06] px-2 py-0.5 text-[10.5px] font-medium text-white/70 hairline"
            >
              {chip}
            </span>
          ))}
        </div>
      ) : null}
    </BaseGlassCard>
  );
}

export default WeatherBriefingCard;
