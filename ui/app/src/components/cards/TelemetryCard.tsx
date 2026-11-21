import { Activity } from "lucide-react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadNumber, type CardProps } from "./types";

const METRICS: Array<{ key: string; label: string; tint: string }> = [
  { key: "cpu", label: "CPU", tint: "#06F7FF" },
  { key: "ram", label: "RAM", tint: "#1E5FFF" },
  { key: "memory", label: "RAM", tint: "#1E5FFF" },
  { key: "disk", label: "Disk", tint: "#B030FF" },
  { key: "battery", label: "Battery", tint: "#30D158" },
  { key: "gpu", label: "GPU", tint: "#FF2D9F" },
];

/** `{"type":"telemetry","cpu":42,"ram":71,"disk":88,"battery":64}` */
export function TelemetryCard({ card, index, onDismiss }: CardProps) {
  const seen = new Set<string>();
  const rows = METRICS.flatMap((metric) => {
    if (seen.has(metric.label)) return [];
    const value = payloadNumber(card.payload[metric.key]);
    if (value === null) return [];
    seen.add(metric.label);
    return [{ ...metric, value: Math.max(0, Math.min(100, value)) }];
  });

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      icon={<Activity size={13} strokeWidth={2.2} />}
      accent="#30D158"
    >
      {rows.length ? (
        <div className="space-y-1.5">
          {rows.map((row) => (
            <div key={row.label} className="flex items-center gap-2">
              <span className="w-12 shrink-0 text-[10.5px] font-semibold uppercase tracking-wide text-faint">
                {row.label}
              </span>
              <span className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.09]">
                <span
                  className="absolute inset-y-0 left-0 rounded-full"
                  style={{
                    width: `${row.value}%`,
                    background: `linear-gradient(90deg, ${row.tint}aa, ${row.tint})`,
                  }}
                />
              </span>
              <span className="w-9 shrink-0 text-right text-[11px] font-medium tabular-nums text-white/80">
                {Math.round(row.value)}%
              </span>
            </div>
          ))}
        </div>
      ) : (
        <p className="text-white/80">{card.body || "Telemetry unavailable."}</p>
      )}
    </BaseGlassCard>
  );
}

export default TelemetryCard;
