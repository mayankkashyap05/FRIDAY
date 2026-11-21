import { FileText } from "lucide-react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadNumber, payloadString, type CardProps } from "./types";

/** A long answer, PDF excerpt or document Q&A turn. */
export function DocumentQACard({ card, index, onDismiss }: CardProps) {
  const excerpt = payloadString(card.payload.excerpt);
  const pages = payloadNumber(card.payload.pages);
  const body = excerpt || card.body;

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      icon={<FileText size={13} strokeWidth={2.2} />}
      accent="#1E5FFF"
      metric={
        pages !== null ? (
          <span className="text-[11px] font-medium tabular-nums text-dim">
            {Math.round(pages)}p
          </span>
        ) : null
      }
    >
      <details className="group" open={body.length <= 260}>
        <summary className="cursor-pointer list-none whitespace-pre-wrap break-words [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:5] overflow-hidden group-open:[display:block]">
          {body}
        </summary>
        {body.length > 260 ? (
          <span className="mt-1 inline-block text-[10.5px] font-medium uppercase tracking-[0.08em] text-siri-cyan/80 group-open:hidden">
            Show full answer
          </span>
        ) : null}
      </details>
    </BaseGlassCard>
  );
}

export default DocumentQACard;
