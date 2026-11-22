import { ShieldQuestion } from "lucide-react";
import { motion } from "motion/react";
import { useEffect, useState } from "react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadString, type CardProps } from "./types";

/**
 * A high-risk action is waiting for consent.
 *
 * Approve/Deny send the *exact* phrases the backend's safety gate expects, so a
 * click is indistinguishable from the user saying it out loud. The orb pulses
 * magenta for as long as one of these is on screen (`attention`), and the card
 * itself wears a slow magenta halo so it can never be mistaken for a normal
 * snippet.
 *
 * Once answered, both buttons lock: a double-click must never send consent
 * twice.
 */
export function ConfirmationCard({ card, index, onDismiss, onReply, onInteraction }: CardProps) {
  const approveLabel = payloadString(card.payload.confirm_label) || "Approve";
  const denyLabel = payloadString(card.payload.deny_label) || "Deny";
  const [answered, setAnswered] = useState<"approve" | "deny" | null>(null);

  // A replacement card (same payload, new uid) re-arms the buttons.
  useEffect(() => setAnswered(null), [card.uid]);

  const answer = (choice: "approve" | "deny") => {
    if (answered) return;
    setAnswered(choice);
    onReply?.(choice === "approve" ? "yes, I approve" : "no, cancel that");
  };

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      onInteraction={onInteraction}
      icon={<ShieldQuestion size={13} strokeWidth={2.2} />}
      accent="#FF2D9F"
    >
      <motion.span
        aria-hidden
        data-testid="confirmation-halo"
        className="pointer-events-none absolute inset-0 rounded-[22px] border border-siri-magenta/45"
        animate={{ opacity: [0.18, 0.5, 0.18], scale: [1, 1.008, 1] }}
        transition={{ duration: 2.6, repeat: Infinity, ease: "easeInOut" }}
      />
      <p className="whitespace-pre-wrap break-words text-white/85">{card.body}</p>
      <div className="mt-2.5 flex items-center gap-2">
        <motion.button
          type="button"
          className="focus-ring rounded-full bg-siri-cyan px-3.5 py-1 text-[11.5px] font-semibold text-black hover:bg-white disabled:opacity-50"
          whileTap={{ scale: 0.95 }}
          transition={{ type: "spring", stiffness: 420, damping: 26 }}
          disabled={answered !== null}
          onClick={() => answer("approve")}
        >
          {approveLabel}
        </motion.button>
        <motion.button
          type="button"
          className="focus-ring rounded-full bg-white/[0.08] px-3.5 py-1 text-[11.5px] font-semibold text-white/85 hairline hover:bg-white/[0.14] disabled:opacity-50"
          whileTap={{ scale: 0.95 }}
          transition={{ type: "spring", stiffness: 420, damping: 26 }}
          disabled={answered !== null}
          onClick={() => answer("deny")}
        >
          {denyLabel}
        </motion.button>
        {answered ? (
          <span className="ml-1 text-[10.5px] font-medium uppercase tracking-[0.08em] text-white/40">
            {answered === "approve" ? "approved" : "denied"}
          </span>
        ) : null}
      </div>
    </BaseGlassCard>
  );
}

export default ConfirmationCard;
