import {
  CloudFog,
  CloudLightning,
  CloudRain,
  CloudSnow,
  CloudSun,
  Sun,
  Wind,
} from "lucide-react";
import type { ComponentType } from "react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadList, payloadNumber, payloadString, type CardProps } from "./types";

type LucideIcon = ComponentType<{ size?: number | string; strokeWidth?: number }>;

/** Condition string → the icon that actually describes the sky. */
export function conditionIcon(condition: string): LucideIcon {
  const text = condition.toLowerCase();
  if (/thunder|storm|lightning/.test(text)) return CloudLightning;
  if (/snow|sleet|blizzard|hail/.test(text)) return CloudSnow;
  if (/rain|drizzle|shower|monsoon/.test(text)) return CloudRain;
  if (/fog|mist|haze|smog/.test(text)) return CloudFog;
  if (/wind|gust|breeze/.test(text)) return Wind;
  if (/clear|sunny|sun\b/.test(text)) return Sun;
  return CloudSun;
}

/** Humidity / wind / condition chips, in that order. */
function chipList(humidity: number | null, wind: string, condition: string): string[] {
  return [
    humidity !== null ? `${Math.round(humidity)}% humidity` : "",
    wind ? `wind ${wind}` : "",
    condition,
  ].filter(Boolean);
}

/** `{"type":"weather","city":"…","temp_c":29,"condition":"rain","humidity":81}` */
export function WeatherBriefingCard({ card, index, onDismiss, onInteraction }: CardProps) {
  const payload = card.payload;
  const temp = payloadNumber(payload.temp_c) ?? payloadNumber(payload.temperature);
  const condition = payloadString(payload.condition) || payloadString(payload.summary);
  const humidity = payloadNumber(payload.humidity);
  const wind = payloadString(payload.wind) || (payloadNumber(payload.wind)?.toString() ?? "");
  const city = payloadString(payload.city);
  const hours = payloadList(payload.hours).slice(0, 6);
  const chips = chipList(humidity, wind, condition);

  const Icon = conditionIcon(condition);
  // Drawn degree ring: one full turn spans a plausible −10 … 45 °C range.
  const fill = temp === null ? 0 : Math.max(0, Math.min(1, (temp + 10) / 55));
  const circumference = 2 * Math.PI * 25;

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      onInteraction={onInteraction}
      icon={<Icon size={13} strokeWidth={2.2} />}
      accent="#06F7FF"
    >
      <div className="flex items-center gap-3">
        {temp !== null ? (
          <span className="relative grid size-[58px] shrink-0 place-items-center">
            <svg
              viewBox="0 0 56 56"
              className="absolute inset-0 size-full -rotate-90"
              aria-hidden
            >
              <circle
                cx="28"
                cy="28"
                r="25"
                fill="none"
                stroke="rgba(255,255,255,0.10)"
                strokeWidth="2"
              />
              <circle
                cx="28"
                cy="28"
                r="25"
                fill="none"
                stroke="url(#siri-temp-ring)"
                strokeWidth="2"
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={circumference * (1 - fill)}
              />
              <defs>
                <linearGradient id="siri-temp-ring" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stopColor="#06F7FF" />
                  <stop offset="60%" stopColor="#1E5FFF" />
                  <stop offset="100%" stopColor="#B030FF" />
                </linearGradient>
              </defs>
            </svg>
            <span className="relative text-[30px] font-light leading-none tabular-nums text-siri-text">
              {Math.round(temp)}
              <span className="align-top text-[11px] font-normal text-dim">°</span>
            </span>
          </span>
        ) : null}

        <div className="min-w-0 flex-1">
          <div className="flex items-baseline gap-2">
            {city ? (
              <span className="truncate text-[13px] font-medium text-white">{city}</span>
            ) : null}
            {condition ? (
              <span className="shrink-0 text-[10.5px] font-medium uppercase tracking-[0.07em] text-siri-cyan/80">
                {condition}
              </span>
            ) : null}
          </div>
          {card.body ? (
            <p className="mt-0.5 line-clamp-2 text-white/70">{card.body}</p>
          ) : null}
        </div>
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

      {hours.length ? (
        <div className="mt-2 flex items-stretch gap-1.5 border-t border-white/[0.07] pt-2">
          {hours.map((hour, i) => {
            const value = payloadNumber(hour.temp_c) ?? payloadNumber(hour.temp);
            const label = payloadString(hour.time) || payloadString(hour.hour);
            return (
              <div
                key={`${label}-${i}`}
                className="flex min-w-0 flex-1 flex-col items-center gap-0.5 rounded-lg bg-white/[0.04] px-1 py-1"
              >
                <span className="truncate text-[9.5px] font-medium uppercase tracking-[0.05em] text-white/40">
                  {label || `${i * 3}h`}
                </span>
                <span className="text-[11px] font-medium tabular-nums text-white/80">
                  {value !== null ? `${Math.round(value)}°` : "—"}
                </span>
              </div>
            );
          })}
        </div>
      ) : null}
    </BaseGlassCard>
  );
}

export default WeatherBriefingCard;
