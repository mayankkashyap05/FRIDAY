import { Mail } from "lucide-react";

import { BaseGlassCard } from "./BaseGlassCard";
import { payloadList, payloadNumber, payloadString, type CardProps } from "./types";

/**
 * `{"type":"email","unread":3,"emails":[{"from":"…","subject":"…"}]}`
 *
 * Two-tier rows: sender in small caps above the subject, an unread dot per
 * unread message, and a single violet pill for the total.
 */
export function EmailDigestCard({ card, index, onDismiss, onInteraction }: CardProps) {
  const emails = payloadList(card.payload.emails);
  const unread = payloadNumber(card.payload.unread);
  const rows = emails.length
    ? emails.slice(0, 4).map((email, i) => ({
        from: payloadString(email.from) || payloadString(email.sender) || "Unknown",
        subject: payloadString(email.subject) || payloadString(email.title) || "(no subject)",
        unread: email.unread === undefined ? i === 0 : Boolean(email.unread),
      }))
    : card.body
        .split("\n")
        .filter(Boolean)
        .slice(0, 4)
        .map((line, i) => ({ from: "", subject: line, unread: i === 0 }));

  return (
    <BaseGlassCard
      card={card}
      index={index}
      onDismiss={onDismiss}
      onInteraction={onInteraction}
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
      <ul className="space-y-1.5">
        {rows.map((row, i) => (
          <li key={`${row.subject}-${i}`} className="flex items-start gap-2">
            <span
              aria-hidden
              data-testid={row.unread ? "email-unread-dot" : undefined}
              className={`mt-[6px] size-1.5 shrink-0 rounded-full ${
                row.unread ? "bg-siri-violet" : "bg-white/15"
              }`}
            />
            <div className="min-w-0 flex-1">
              {row.from ? (
                <p className="truncate text-[9.5px] font-semibold uppercase tracking-[0.08em] text-white/45">
                  {row.from}
                </p>
              ) : null}
              <p
                className={`truncate ${
                  row.unread ? "font-medium text-white/92" : "text-white/75"
                }`}
              >
                {row.subject}
              </p>
            </div>
          </li>
        ))}
        {rows.length === 0 ? <li className="text-dim">Inbox is clear.</li> : null}
      </ul>
    </BaseGlassCard>
  );
}

export default EmailDigestCard;
