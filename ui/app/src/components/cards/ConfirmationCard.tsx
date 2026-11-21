import { ShieldQuestion } from "lucide-react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadString, type CardProps } from "./types";

/**
 * A high-risk action is waiting for consent.
 *
 * Approve/Deny send the *exact* phrases the backend's safety gate expects, so a
 * click is indistinguishable from the user saying it out loud. The orb pulses
 * magenta for as long as one of these is on screen (`attention`).
 */
export function ConfirmationCard({ card, index, onDismiss, onReply }: CardProps) {
  const approveLabel = payloadString(card.payload.confirm_label) || "Approve";
  const denyLabel = payloadString(card.payload.deny_label) || "Deny";

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      icon={<ShieldQuestion size={13} strokeWidth={2.2} />}
      accent="#FF2D9F"
    >
      <p className="whitespace-pre-wrap break-words text-white/85">{card.body}</p>
      <div className="mt-2.5 flex items-center gap-2">
        <button
          type="button"
          className="focus-ring rounded-full bg-siri-cyan px-3.5 py-1 text-[11.5px] font-semibold text-black hover:bg-white"
          onClick={() => onReply?.("yes, I approve")}
        >
          {approveLabel}
        </button>
        <button
          type="button"
          className="focus-ring rounded-full bg-white/[0.08] px-3.5 py-1 text-[11.5px] font-semibold text-white/85 hairline hover:bg-white/[0.14]"
          onClick={() => onReply?.("no, cancel that")}
        >
          {denyLabel}
        </button>
      </div>
    </BaseGlassCard>
  );
}

export default ConfirmationCard;
