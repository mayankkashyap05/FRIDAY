import { FileText } from "lucide-react";
import { motion } from "motion/react";
import { useState } from "react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadNumber, payloadString, type CardProps } from "./types";

/**
 * A long answer, PDF excerpt or document Q&A turn.
 *
 * Collapsed above ~260 characters, with a bottom fade mask instead of a hard
 * clip, and a Motion height animation for expand/collapse (the raw `<details>`
 * snap of v1 was the one place the UI moved without physics).
 */
export function DocumentQACard({ card, index, onDismiss, onInteraction }: CardProps) {
  const excerpt = payloadString(card.payload.excerpt);
  const pages = payloadNumber(card.payload.pages);
  const body = excerpt || card.body;
  const collapsible = body.length > 260;
  const [expanded, setExpanded] = useState(!collapsible);

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      onInteraction={onInteraction}
      icon={<FileText size={13} strokeWidth={2.2} />}
      accent="#1E5FFF"
      metric={
        pages !== null ? (
          <span className="rounded-full bg-white/[0.07] px-1.5 py-0.5 text-[10.5px] font-medium tabular-nums text-dim">
            {Math.round(pages)}p
          </span>
        ) : null
      }
    >
      <motion.div
        initial={false}
        animate={{ height: "auto" }}
        transition={{ type: "spring", stiffness: 380, damping: 32 }}
        className="relative overflow-hidden"
      >
        <blockquote
          className={`border-l-2 border-siri-sapphire/40 pl-2.5 italic text-white/85 ${
            !expanded ? "fade-mask [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:5]" : ""
          }`}
        >
          <p className="whitespace-pre-wrap break-words">{body}</p>
        </blockquote>
      </motion.div>
      {collapsible ? (
        <button
          type="button"
          aria-expanded={expanded}
          className="focus-ring mt-1 inline-block text-[10.5px] font-medium uppercase tracking-[0.08em] text-siri-cyan/80 hover:text-siri-cyan"
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "Show less" : "Show full answer"}
        </button>
      ) : null}
    </BaseGlassCard>
  );
}

export default DocumentQACard;
