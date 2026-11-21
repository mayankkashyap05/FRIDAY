import { AnimatePresence } from "motion/react";
import type { ComponentType } from "react";

import { CalendarCard } from "./cards/CalendarCard";
import { ConfirmationCard } from "./cards/ConfirmationCard";
import { ControlPillCard } from "./cards/ControlPillCard";
import { DocumentQACard } from "./cards/DocumentQACard";
import { EmailDigestCard } from "./cards/EmailDigestCard";
import { FlightCard } from "./cards/FlightCard";
import { TelemetryCard } from "./cards/TelemetryCard";
import { TextCard } from "./cards/TextCard";
import { WeatherBriefingCard } from "./cards/WeatherBriefingCard";
import type { CardProps } from "./cards/types";
import type { CardItem } from "../types/protocol";

/**
 * Kind → component registry.
 *
 * The backend may add a kind before the renderer knows it (rolling upgrade), so
 * an unknown kind falls back to the prose card instead of a blank slot. The
 * fallback is a *card*, not a second UI: there is still exactly one frontend.
 */
export const CARD_REGISTRY: Record<string, ComponentType<CardProps>> = {
  text: TextCard,
  weather: WeatherBriefingCard,
  calendar: CalendarCard,
  email: EmailDigestCard,
  flight: FlightCard,
  telemetry: TelemetryCard,
  control: ControlPillCard,
  document: DocumentQACard,
  confirmation: ConfirmationCard,
};

export interface SnippetStackProps {
  cards: CardItem[];
  onDismiss: (uid: string) => void;
  onReply?: (text: string) => void;
  /** Hide the whole stack (e.g. during dismissal). */
  hidden?: boolean;
  onInteraction?: (inside: boolean) => void;
}

/**
 * The glass snippet stack above the orb.
 *
 * `CardsModel` prepends, so `cards[0]` is the newest; `flex-col-reverse` puts
 * the newest card nearest the orb and lets older ones rise out of the top of
 * the screen. Capped at 3 visible so a burst of tool output never becomes a
 * wall of glass over the user's work.
 */
export function SnippetStack({
  cards,
  onDismiss,
  onReply,
  hidden = false,
  onInteraction,
}: SnippetStackProps) {
  const visible = cards.slice(0, 3);
  if (hidden || visible.length === 0) return null;

  return (
    <div
      className="interactive pointer-events-auto flex w-full flex-col-reverse items-center gap-2"
      onPointerEnter={() => onInteraction?.(true)}
      onPointerLeave={() => onInteraction?.(false)}
      aria-live="polite"
      aria-label="FRIDAY results"
    >
      <AnimatePresence initial={false} mode="popLayout">
        {visible.map((card) => {
          const Component = CARD_REGISTRY[card.kind] ?? TextCard;
          return (
            <Component
              key={card.uid || `${card.kind}:${card.title}`}
              card={card}
              index={visible.indexOf(card)}
              onDismiss={onDismiss}
              onReply={onReply}
            />
          );
        })}
      </AnimatePresence>
    </div>
  );
}

export default SnippetStack;
