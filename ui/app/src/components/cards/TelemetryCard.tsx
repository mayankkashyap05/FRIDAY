import { Activity } from "lucide-react";
import { motion } from "motion/react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadNumber, type CardProps } from "./types";

const METRICS: Array<{ key: string; label: string }> = [
  { key: "cpu", label: "CPU" },
  { key: "ram", label: "RAM" },
  { key: "memory", label: "RAM" },
  { key: "disk", label: "Disk" },
  { key: "battery", label: "Battery" },
  { key: "gpu", label: "GPU" },
];

/**
 * Semantic thresholds — a machine at 92 % disk has to *look* hot:
 * cyan < 70 %, amber 70–88 %, coral > 88 %.
 */
export function thresholdColor(value: number): string {
  if (value > 88) return "#FF6961";
  if (value >= 70) return "#FFD60A";
  return "#06F7FF";
}

/** `{"type":"telemetry","cpu":42,"ram":71,"disk":88,"battery":64}` */
export function TelemetryCard({ card, index, onDismiss, onInteraction }: CardProps) {
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
      onInteraction={onInteraction}
      icon={<Activity size={13} strokeWidth={2.2} />}
      accent="#30D158"
    >
      {rows.length ? (
        <div className="space-y-1.5">
          {rows.map((row) => {
            const tint = thresholdColor(row.value);
            return (
              <div key={row.label} className="flex items-center gap-2">
                <span className="w-12 shrink-0 text-[10.5px] font-semibold uppercase tracking-wide text-faint">
                  {row.label}
                </span>
                <span className="relative h-1.5 flex-1 overflow-hidden rounded-full bg-white/[0.09]">
                  <motion.span
                    className="absolute inset-y-0 left-0 rounded-full"
                    style={{
                      background: `linear-gradient(90deg, ${tint}aa, ${tint})`,
                      boxShadow: `0 0 8px ${tint}55`,
                    }}
                    initial={{ width: 0 }}
                    animate={{ width: `${row.value}%` }}
                    transition={{ type: "spring", stiffness: 220, damping: 26 }}
                  />
                </span>
                <span className="w-9 shrink-0 text-right text-[11px] font-medium tabular-nums text-white/80">
                  {Math.round(row.value)}%
                </span>
              </div>
            );
          })}
        </div>
      ) : (
        <p className="text-white/80">{card.body || "Telemetry unavailable."}</p>
      )}
    </BaseGlassCard>
  );
}

export default TelemetryCard;
