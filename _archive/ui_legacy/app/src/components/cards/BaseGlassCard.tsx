import { X } from "lucide-react";
import { motion } from "motion/react";
import type { ReactNode } from "react";

import { useInteractiveRegion } from "../../hooks/useInteractiveRegion";
import type { CardItem, Severity } from "../../types/protocol";

/** Severity rail + accent colours (one place so every card agrees). */
export const SEVERITY_COLOR: Record<Severity, string> = {
  info: "#06F7FF",
  success: "#30D158",
  warn: "#FFD60A",
  error: "#FF6961",
  security: "#FF2D9F",
};

export interface BaseGlassCardProps {
  card: CardItem;
  /** 0 = newest/top of the stack. */
  index: number;
  onDismiss: (uid: string) => void;
  icon?: ReactNode;
  /** Right-aligned metric (temperature, unread count, score…). */
  metric?: ReactNode;
  children: ReactNode;
  /** Tint for the icon chip; defaults to the severity colour. */
  accent?: string;
  /** Hover enter/leave, so Electron can drop click-through over the card. */
  onInteraction?: (inside: boolean) => void;
}

/**
 * The one glass surface every snippet uses.
 *
 * Spring entrance (stiffness 380 / damping 28), a 2 px hover lift, depth
 * fade+scale for cards deeper in the stack, right-click or × dismissal, and a
 * TTL hairline that drains for the lifetime of a transient card.
 *
 * Interaction note: the card owns a `useInteractiveRegion` handle. Windows
 * click-through is per-HWND, so without this every card would be a picture of
 * a button — hover and clicks would fall straight through to the desktop.
 *
 * Motion note: no Tailwind `transition-*` utilities here — all motion is
 * Motion springs/tweens so CSS transitions can never fight the physics.
 */
export function BaseGlassCard({
  card,
  index,
  onDismiss,
  icon,
  metric,
  children,
  accent,
  onInteraction,
}: BaseGlassCardProps) {
  const tint = accent ?? SEVERITY_COLOR[card.severity] ?? SEVERITY_COLOR.info;
  const depth = Math.min(index, 2);
  const region = useInteractiveRegion({ onChange: onInteraction });

  return (
    <motion.article
      {...region.regionProps}
      layout
      data-testid={`card-${card.kind}`}
      data-uid={card.uid}
      className={`${region.regionProps.className} glass group relative w-[min(430px,86vw)] px-4 pb-3.5 pt-3 text-left`}
      style={{
        borderColor: index === 0 ? "rgba(255,255,255,0.16)" : "rgba(255,255,255,0.08)",
      }}
      initial={{ opacity: 0, y: 16, scale: 0.94 }}
      animate={{
        opacity: 1 - depth * 0.18,
        y: 0,
        scale: 1 - depth * 0.025,
      }}
      exit={{ opacity: 0, y: 10, scale: 0.95 }}
      transition={{
        type: "spring",
        stiffness: 380,
        damping: 28,
        mass: 0.8,
        delay: index * 0.015,
      }}
      whileHover={{
        y: -2,
        borderColor: "rgba(255,255,255,0.26)",
      }}
      onContextMenu={(event) => {
        event.preventDefault();
        if (card.dismissible) onDismiss(card.uid);
      }}
      role="group"
      aria-label={`${card.kind} card: ${card.title || "FRIDAY"}`}
    >
      <span
        aria-hidden
        className="severity-rail"
        style={{ background: `linear-gradient(180deg, ${tint}, ${tint}33)` }}
      />

      <header className="mb-1.5 flex items-start gap-2.5 pl-1">
        {icon ? (
          <span
            className="mt-0.5 grid size-6 shrink-0 place-items-center rounded-full"
            style={{ backgroundColor: `${tint}22`, color: tint }}
          >
            {icon}
          </span>
        ) : null}
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[13px] font-semibold leading-5 tracking-[-0.01em] text-siri-text">
            {card.title || "FRIDAY"}
          </h3>
          {card.footer ? (
            <p className="truncate text-[10.5px] font-medium uppercase tracking-[0.08em] text-faint">
              {card.footer}
            </p>
          ) : null}
        </div>
        {metric ? (
          <div className="shrink-0 text-right text-[15px] font-semibold leading-5 text-siri-text">
            {metric}
          </div>
        ) : null}
        {card.dismissible ? (
          // Fades in on hover; always reachable by keyboard focus and by
          // right-click anywhere on the card.
          <button
            type="button"
            aria-label="Dismiss card"
            data-testid="card-dismiss"
            className="focus-ring -mr-1 ml-0.5 grid size-6 shrink-0 place-items-center rounded-full text-faint opacity-0 hover:bg-white/10 hover:text-siri-text focus-visible:opacity-100 group-hover:opacity-100"
            onClick={() => onDismiss(card.uid)}
          >
            <X size={12} strokeWidth={2.4} />
          </button>
        ) : null}
      </header>

      <div className="pl-1 text-[12.5px] leading-[1.45] text-white/85">{children}</div>

      {card.ttl > 0 ? (
        <motion.span
          aria-hidden
          data-testid="card-ttl"
          className="ttl-hairline"
          initial={{ scaleX: 1, opacity: 0.75 }}
          animate={{ scaleX: 0, opacity: 0.2 }}
          transition={{ duration: Math.max(1, card.ttl), ease: "linear" }}
        />
      ) : null}
    </motion.article>
  );
}

export default BaseGlassCard;
