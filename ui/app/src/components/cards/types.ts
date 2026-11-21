import type { CardItem } from "../../types/protocol";

/** Every typed card takes the same props, so the stack can dispatch blindly. */
export interface CardProps {
  card: CardItem;
  /** 0 = newest/top of the stack (used for depth + stagger). */
  index: number;
  onDismiss: (uid: string) => void;
  /** Confirmation cards only: send an affirmative/negative reply. */
  onReply?: (text: string) => void;
}

export function payloadNumber(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) return value;
  if (typeof value === "string" && value.trim() !== "") {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : null;
  }
  return null;
}

export function payloadString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function payloadList(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  return value.filter(
    (entry): entry is Record<string, unknown> =>
      typeof entry === "object" && entry !== null,
  );
}

export function formatClock(ts: number): string {
  if (!ts) return "";
  try {
    return new Date(ts * 1000).toLocaleTimeString([], {
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return "";
  }
}
