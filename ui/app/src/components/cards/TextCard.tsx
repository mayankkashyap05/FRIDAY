import { MessageSquare } from "lucide-react";

import { BaseGlassCard } from "./BaseGlassCard";
import type { CardProps } from "./types";

/** Plain prose: the fallback for anything that is not a typed payload. */
export function TextCard({ card, index, onDismiss }: CardProps) {
  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      icon={<MessageSquare size={13} strokeWidth={2.2} />}
    >
      <p className="whitespace-pre-wrap break-words [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:7] overflow-hidden">
        {card.body}
      </p>
    </BaseGlassCard>
  );
}

export default TextCard;
