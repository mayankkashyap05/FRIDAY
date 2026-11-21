import { AnimatePresence, motion } from "motion/react";
import { X } from "lucide-react";
import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";

import { useFridaySocket, initialUiState, applyMessage } from "../hooks/useFridaySocket";
import { useInteractiveRegion } from "../hooks/useInteractiveRegion";
import type {
  AssistantState,
  CardItem,
  CardKind,
  ThemePalette,
} from "../types/protocol";
import { DevPreviewPanel } from "./DevPreviewPanel";
import { LiveActivityBar } from "./LiveActivityBar";
import { LiveTranscript } from "./LiveTranscript";
import { SetupCard } from "./SetupCard";
import { SiriOrb } from "./SiriOrb";
import { SnippetStack } from "./SnippetStack";
import { StreamCanvas } from "./StreamCanvas";
import { TypeToSiriPill } from "./TypeToSiriPill";

/* ── theme tokens (`ui/themes.py` palette names → CSS custom properties) ──── */
const THEME_VARS: Record<string, string> = {
  pri: "--color-siri-cyan",
  mid: "--color-siri-sapphire",
  acc: "--color-siri-magenta",
  acc2: "--color-siri-violet",
  text: "--color-siri-text",
  green: "--color-siri-green",
  red: "--color-siri-red",
};

function useThemeVars(theme: ThemePalette): void {
  useEffect(() => {
    const root = document.documentElement;
    for (const [key, cssVar] of Object.entries(THEME_VARS)) {
      const value = theme[key];
      if (typeof value === "string" && value) root.style.setProperty(cssVar, value);
    }
    root.style.setProperty("--siri-ui-scale", String(theme.uiScale ?? 1));
    root.dataset.reducedMotion = theme.reducedMotion ? "true" : "false";
    root.dataset.transparency = theme.transparency === false ? "opaque" : "translucent";
  }, [theme]);
}

const DEV_CARDS: Record<CardKind, () => Omit<CardItem, "uid" | "ts" | "age">> = {
  weather: () => ({
    kind: "weather", title: "Weather · Dibrugarh", body: "Light rain easing by evening.",
    footer: "OpenWeather", payload: { city: "Dibrugarh", temp_c: 27, humidity: 81, wind: "9 km/h" },
    severity: "info", ttl: 30, dismissible: true,
  }),
  calendar: () => ({
    kind: "calendar", title: "Next up", body: "",
    footer: "Google Calendar", payload: { events: [
      { time: "09:00", title: "Standup" }, { time: "11:30", title: "Design review" },
      { time: "16:00", title: "Call with Aarav" }] },
    severity: "info", ttl: 30, dismissible: true,
  }),
  email: () => ({
    kind: "email", title: "Inbox digest", body: "", footer: "Gmail",
    payload: { unread: 3, emails: [
      { from: "Priya", subject: "Contract draft v4" },
      { from: "GitHub", subject: "FRIDAY: 2 new issues" }] },
    severity: "info", ttl: 30, dismissible: true,
  }),
  flight: () => ({
    kind: "flight", title: "AI 101 · Delhi → Dubai", body: "On time", footer: "IndiGo",
    payload: { code: "AI101", from: "DEL", to: "DXB", gate: "14A", departure: "18:40" },
    severity: "success", ttl: 30, dismissible: true,
  }),
  telemetry: () => ({
    kind: "telemetry", title: "System", body: "", footer: "this machine",
    payload: { cpu: 37, ram: 68, disk: 82, battery: 61 }, severity: "info", ttl: 15, dismissible: true,
  }),
  control: () => ({
    kind: "control", title: "Volume", body: "", footer: "system",
    payload: { label: "Volume", value: 60, unit: "%" }, severity: "info", ttl: 8, dismissible: true,
  }),
  document: () => ({
    kind: "document", title: "Contract draft v4", body: "", footer: "PDF · 12 pages",
    payload: { pages: 12, excerpt: "Clause 7 caps aggregate liability at the fees paid in the preceding twelve months, with a carve-out for gross negligence." },
    severity: "info", ttl: 0, dismissible: true,
  }),
  confirmation: () => ({
    kind: "confirmation", title: "Delete 42 files?", body: "This permanently removes them from Downloads.",
    footer: "needs your consent", payload: { confirm_label: "Approve", deny_label: "Deny" },
    severity: "security", ttl: 0, dismissible: false,
  }),
  text: () => ({
    kind: "text", title: "FRIDAY", body: "I can summarise your screen, brief you, or run a task — just say the word.",
    footer: "", payload: {}, severity: "info", ttl: 12, dismissible: true,
  }),
};

let fakeCounter = 0;

export function StageApp() {
  const [frames, setFrames] = useState<{ vision: Uint8Array | null; arcade: Uint8Array | null }>({
    vision: null,
    arcade: null,
  });

  const handleFrame = useCallback((stream: number, jpeg: Uint8Array) => {
    setFrames((current) =>
      stream === 1 ? { ...current, vision: jpeg } : { ...current, arcade: jpeg },
    );
  }, []);

  const socket = useFridaySocket({ onFrame: handleFrame });
  const [devUi, devDispatch] = useReducer(applyMessage, initialUiState);
  const [typing, setTyping] = useState(false);
  const [devOpen, setDevOpen] = useState(false);
  const [errorSignal, setErrorSignal] = useState(0);

  const connected = socket.connected;
  const live = socket.ui;
  const ui = live.ready || connected ? live : devUi;
  /** Summoned = the orb is (or is about to be) on screen. */
  const invoked = ui.state.invoked;

  // The renderer boots before the socket exists, so "not connected" is normal
  // for the first second. Only call it a dev session once nothing has arrived.
  const [devFallback, setDevFallback] = useState(false);
  useEffect(() => {
    if (connected || live.ready) {
      setDevFallback(false);
      return;
    }
    const timer = window.setTimeout(() => setDevFallback(true), 2500);
    return () => window.clearTimeout(timer);
  }, [connected, live.ready]);
  const useDev = devFallback && !connected && !live.ready;

  // The bench opens on a summoned orb — otherwise a fresh browser preview is
  // just an empty transparent page.
  useEffect(() => {
    if (!useDev) return;
    devDispatch({
      t: "state",
      state: "LISTENING",
      statusLabel: "LISTENING",
      invoked: true,
      energy: 0.42,
    });
    devDispatch({
      t: "activities",
      items: [
        { aid: "dev-1", title: "Warming the renderer", kind: "task", detail: "",
          progress: 0.35, active: true, ts: Date.now() / 1000 },
      ],
      activeCount: 1,
    });
    devDispatch({
      t: "live_transcript",
      text: "Click the orb to type, or use the dev bench.",
    });
  }, [useDev]);

  const send = socket.send;
  useThemeVars(ui.theme);

  // ── focus / window discipline ─────────────────────────────────────────────
  // Summoning by voice must NEVER steal focus from Notepad/VS Code/games.
  // Only a surface that genuinely needs the keyboard — the type-to-Siri pill or
  // the first-run setup card — makes the stage focusable.
  const setupVisible = ui.setup.visible;
  useEffect(() => {
    window.fridayAPI?.setEscapeArmed(invoked);
    window.fridayAPI?.setInvoked(invoked);
  }, [invoked]);

  useEffect(() => {
    window.fridayAPI?.setFocusable(typing || setupVisible);
  }, [typing, setupVisible]);

  const dismiss = useCallback(() => {
    setTyping(false);
    send({ t: "cancel" });
  }, [send]);

  const handleEscape = useCallback(() => {
    if (typing) {
      setTyping(false);
      return;
    }
    if (ui.state.invoked) dismiss();
  }, [typing, ui.state.invoked, dismiss]);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        handleEscape();
      }
    };
    window.addEventListener("keydown", onKey);
    const off = window.fridayAPI?.onEscape(handleEscape);
    return () => {
      window.removeEventListener("keydown", onKey);
      off?.();
    };
  }, [handleEscape]);

  // Python owns the inspector's existence; the renderer mirrors it into the
  // second BrowserWindow (and mirrors closure back).
  useEffect(() => {
    if (ui.inspector.open) {
      window.fridayAPI?.openInspector(ui.inspector.tab || "camera");
    }
  }, [ui.inspector.open, ui.inspector.tab]);

  // Python closing the inspector (or the user closing it with its ×) must
  // tear the second BrowserWindow down too — a true→false edge, not a level.
  const inspectorOpen = ui.inspector.open;
  const wasInspectorOpen = useRef(false);
  useEffect(() => {
    if (wasInspectorOpen.current && !inspectorOpen) {
      window.fridayAPI?.closeInspector();
    }
    wasInspectorOpen.current = inspectorOpen;
  }, [inspectorOpen]);

  useEffect(() => {
    const off = window.fridayAPI?.onInspectorClosed(() => {
      send({ t: "close_inspector" });
    });
    return () => off?.();
  }, [send]);

  const notifyInteraction = useCallback((inside: boolean) => {
    send({ t: "pointer_inside", value: inside });
    if (inside) send({ t: "interaction" });
  }, [send]);

  const handleReply = useCallback(
    (text: string) => {
      if (useDev) {
        devDispatch({ t: "cards", items: [] });
        return;
      }
      send({ t: "submit_text", text });
    },
    [useDev, send],
  );

  const handleDismissCard = useCallback(
    (uid: string) => {
      if (useDev) {
        devDispatch({ t: "cards", items: devUi.cards.filter((card) => card.uid !== uid) });
        return;
      }
      send({ t: "dismiss_card", uid });
    },
    [useDev, devUi.cards, send],
  );

  const handleOrbDismissed = useCallback(() => {
    // The contraction finished. Only a stage that is still *not* summoned may
    // drop its HWND — hiding a live setup card would strand the user.
    if (!invoked) {
      window.fridayAPI?.hideStage();
      send({ t: "window_dismissed" });
    }
  }, [invoked, send]);

  const injectedCards = useMemo(() => ui.cards, [ui.cards]);
  const confirmationWaiting = injectedCards.some((card) => card.kind === "confirmation");
  const reducedMotion = Boolean(ui.theme.reducedMotion);

  const toast = ui.toast;
  const [toastVisible, setToastVisible] = useState(true);
  useEffect(() => {
    if (!toast) return;
    setToastVisible(true);
    const timer = window.setTimeout(() => setToastVisible(false), 4200);
    return () => window.clearTimeout(timer);
  }, [toast]);

  // FRIDAY's own streaming words are the only honest amplitude source while
  // she speaks (the mic is muted during TTS), so each growth of the live line
  // becomes a syllabic impulse the orb can feel.
  const [speechPulse, setSpeechPulse] = useState(0);
  const lastTranscriptLength = useRef(0);
  useEffect(() => {
    const length = ui.liveTranscript.length;
    if (ui.state.speaking && length > lastTranscriptLength.current) {
      setSpeechPulse((value) => value + 1);
    }
    lastTranscriptLength.current = length;
  }, [ui.liveTranscript, ui.state.speaking]);

  const arcadeOpen = ui.arcade.open;
  // A pinned region: the arcade feed must be hittable (close button, score
  // chips) for as long as it covers the stage, not only while hovered.
  const arcadeRegion = useInteractiveRegion({
    pinned: arcadeOpen,
    onChange: notifyInteraction,
  });

  const injectCard = (kind: CardKind) => {
    fakeCounter += 1;
    const base = DEV_CARDS[kind]();
    devDispatch({
      t: "cards",
      items: [
        { ...base, uid: `dev-${fakeCounter}`, ts: Date.now() / 1000, age: 0 },
        ...devUi.cards,
      ].slice(0, 8) as CardItem[],
    });
  };

  return (
    <div
      className="stage-root relative flex h-full w-full flex-col items-center justify-end gap-2 pb-6"
      data-testid="friday-stage"
      data-connected={connected ? "true" : "false"}
    >
      {/* Snippet stack — newest nearest the orb */}
      <SnippetStack
        cards={ui.cards}
        onDismiss={handleDismissCard}
        onReply={handleReply}
        onInteraction={notifyInteraction}
      />

      <AnimatePresence>
        {ui.activeCount > 0 ? (
          <LiveActivityBar
            activities={ui.activities}
            activeCount={ui.activeCount}
            onCancel={(aid) => send({ t: "cancel_activity", aid })}
            onInteraction={notifyInteraction}
          />
        ) : null}
      </AnimatePresence>

      <LiveTranscript text={ui.liveTranscript} speaking={ui.state.speaking} />

      <TypeToSiriPill
        open={typing}
        onSubmit={(text) => send({ t: "submit_text", text })}
        onClose={() => setTyping(false)}
        onInteraction={notifyInteraction}
        onTyping={(value) => send({ t: "typing_changed", value })}
      />

      <motion.div
        className="relative flex h-[204px] w-[204px] items-center justify-center"
        animate={{ opacity: setupVisible ? 0 : 1, scale: setupVisible ? 0.92 : 1 }}
        transition={{ type: "spring", stiffness: 380, damping: 30 }}
        style={{ pointerEvents: setupVisible ? "none" : "auto" }}
        aria-hidden={setupVisible}
      >
        <SiriOrb
          invoked={ui.state.invoked}
          state={ui.state.state as AssistantState}
          energy={ui.state.energy}
          amplitude={ui.amplitude}
          muted={ui.state.muted}
          attention={confirmationWaiting}
          errorSignal={errorSignal}
          reducedMotion={reducedMotion}
          acting={ui.activeCount > 0 || ui.state.state === "PROCESSING"}
          speechPulse={speechPulse}
          onActivate={() => setTyping(true)}
          onDismissed={handleOrbDismissed}
          onInteraction={notifyInteraction}
        />
      </motion.div>

      {/* Vision pip: only while the camera preview is open. */}
      {ui.vision.previewOpen && frames.vision ? (
        <motion.div
          className="glass pointer-events-none absolute bottom-6 right-5 w-[148px] overflow-hidden rounded-2xl p-1"
          initial={{ opacity: 0, scale: 0.94 }}
          animate={{ opacity: 1, scale: 1 }}
        >
          <StreamCanvas bytes={frames.vision} className="h-[92px] w-full rounded-xl object-cover" />
          <p className="truncate px-1 pt-1 text-[9.5px] font-medium uppercase tracking-[0.06em] text-white/50">
            {ui.vision.mode} · {ui.vision.signal}
          </p>
        </motion.div>
      ) : null}

      {/* Arcade feed fills the overlay while a game is running. */}
      <AnimatePresence>
        {arcadeOpen ? (
          <motion.div
            {...arcadeRegion.regionProps}
            className={`${arcadeRegion.regionProps.className} absolute inset-0 z-30 flex flex-col items-center justify-center bg-black/70 backdrop-blur-sm`}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          >
            <div className="relative w-[min(360px,80vw)] overflow-hidden rounded-3xl border border-white/10">
              <StreamCanvas bytes={frames.arcade} className="w-full" label="FRIDAY arcade" />
              <span className="absolute left-3 top-3 rounded-full bg-black/60 px-2 py-0.5 text-[10px] font-semibold tabular-nums text-siri-cyan">
                {ui.arcade.score} pts
              </span>
              <span className="absolute right-3 top-3 rounded-full bg-black/60 px-2 py-0.5 text-[10px] font-semibold text-white/80">
                {ui.arcade.lives} lives
              </span>
            </div>
            <div className="mt-2 flex items-center gap-3">
              <span className="text-[11px] font-medium uppercase tracking-[0.1em] text-white/50">
                {ui.arcade.game.replace(/_/g, " ")} · {ui.arcade.message}
              </span>
              <button
                type="button"
                className="glass glass-pill grid size-7 place-items-center text-white/70"
                onClick={() => send({ t: "command", text: "stop arcade" })}
                aria-label="Close arcade"
              >
                <X size={13} strokeWidth={2.4} />
              </button>
            </div>
          </motion.div>
        ) : null}
      </AnimatePresence>

      {/* Operator toast */}
      <AnimatePresence>
        {toast && toastVisible ? (
          <motion.div
            key={toast.text}
            className="glass pointer-events-none absolute left-1/2 top-4 -translate-x-1/2 px-3 py-1.5 text-[11.5px] font-medium text-white/90"
            initial={{ opacity: 0, y: -8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -6 }}
            transition={{ duration: 0.2 }}
            role="status"
          >
            <span
              className="mr-1.5 inline-block size-1.5 rounded-full align-middle"
              style={{
                backgroundColor:
                  toast.severity === "error" ? "#FF6961"
                  : toast.severity === "warn" ? "#FFD60A"
                  : toast.severity === "success" ? "#30D158" : "#06F7FF",
              }}
            />
            {toast.text}
          </motion.div>
        ) : null}
      </AnimatePresence>

      {/* First-run key gate must win over everything. */}
      <div className="absolute inset-0 z-40 flex items-center justify-center pointer-events-none">
        <div className="pointer-events-auto">
          <SetupCard
            open={ui.setup.visible}
            error={
              ui.setup.visible && ui.toast?.severity === "error" ? ui.toast.text : ""
            }
            onSubmitKey={(key) => send({ t: "submit_api_key", key })}
            onInteraction={notifyInteraction}
          />
        </div>
      </div>

      {useDev ? (
        <DevPreviewPanel
          open={devOpen}
          onOpenChange={setDevOpen}
          onState={(state) =>
            devDispatch({ t: "state", state, invoked: true, statusLabel: state, energy: state === "SPEAKING" ? 0.85 : 0.4 })
          }
          onCard={injectCard}
          onAmplitude={(value) => devDispatch({ t: "amplitude", value })}
          onToggleInspector={() => devDispatch({ t: "inspector", open: true, tab: "camera", props: {} })}
          onError={() => setErrorSignal((value) => value + 1)}
          onShutdown={() => devDispatch({ t: "shutdown" })}
          onClear={() => {
            devDispatch({ t: "cards", items: [] });
            devDispatch({ t: "activities", items: [], activeCount: 0 });
          }}
        />
      ) : null}
    </div>
  );
}

export default StageApp;
