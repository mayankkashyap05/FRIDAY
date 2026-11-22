import { MessageSquare } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useState } from "react";

import { BaseGlassCard } from "./BaseGlassCard";
import type { CardProps } from "./types";

/** Prose longer than this is offered as expandable. */
const COLLAPSE_AT = 260;

/** Plain prose: the fallback for anything that is not a typed payload. */
export function TextCard({ card, index, onDismiss, onInteraction }: CardProps) {
  const body = card.body || "";
  const collapsible = body.length > COLLAPSE_AT || body.split("\n").length > 4;
  const [expanded, setExpanded] = useState(!collapsible);

  // A recycled card slot (dev bench, backend refresh) starts collapsed again.
  useEffect(() => setExpanded(!collapsible), [card.uid, collapsible]);

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      onInteraction={onInteraction}
      icon={<MessageSquare size={13} strokeWidth={2.2} />}
    >
      <motion.div
        initial={false}
        animate={{ height: "auto" }}
        transition={{ type: "spring", stiffness: 380, damping: 32 }}
        className="overflow-hidden"
      >
        <p
          className={`whitespace-pre-wrap break-words ${
            collapsible && !expanded
              ? "fade-mask [display:-webkit-box] [-webkit-box-orient:vertical] [-webkit-line-clamp:7]"
              : ""
          }`}
        >
          {body}
        </p>
      </motion.div>
      {collapsible ? (
        <button
          type="button"
          aria-expanded={expanded}
          className="focus-ring mt-1 text-[10.5px] font-medium uppercase tracking-[0.08em] text-siri-cyan/80 hover:text-siri-cyan"
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "Show less" : "Show more"}
        </button>
      ) : null}
    </BaseGlassCard>
  );
}

export default TextCard;
