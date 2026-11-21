import { Mail } from "lucide-react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadList, payloadNumber, payloadString, type CardProps } from "./types";

/** `{"type":"email","unread":3,"emails":[{"from":"…","subject":"…"}]}` */
export function EmailDigestCard({ card, index, onDismiss }: CardProps) {
  const emails = payloadList(card.payload.emails);
  const unread = payloadNumber(card.payload.unread);
  const rows = emails.length
    ? emails.slice(0, 4).map((email) => ({
        from: payloadString(email.from) || payloadString(email.sender) || "Unknown",
        subject: payloadString(email.subject) || payloadString(email.title) || "(no subject)",
      }))
    : card.body
        .split("\n")
        .filter(Boolean)
        .slice(0, 4)
        .map((line) => ({ from: "", subject: line }));

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      icon={<Mail size={13} strokeWidth={2.2} />}
      accent="#B030FF"
      metric={
        unread !== null ? (
          <span className="rounded-full bg-siri-violet/20 px-2 py-0.5 text-[11px] font-semibold tabular-nums text-siri-violet">
            {Math.round(unread)} unread
          </span>
        ) : null
      }
    >
      <ul className="space-y-1">
        {rows.map((row, i) => (
          <li key={`${row.subject}-${i}`} className="truncate">
            {row.from ? (
              <span className="mr-1.5 font-medium text-white/70">{row.from}</span>
            ) : null}
            <span className="text-white/85">{row.subject}</span>
          </li>
        ))}
        {rows.length === 0 ? <li className="text-dim">Inbox is clear.</li> : null}
      </ul>
    </BaseGlassCard>
  );
}

export default EmailDigestCard;
