import {
  Activity,
  Camera,
  Gamepad2,
  History,
  Maximize2,
  Mic,
  Minimize2,
  Minus,
  MonitorPlay,
  Settings,
  Volume2,
  VolumeX,
  X,
} from "lucide-react";
import { motion } from "motion/react";
import { useCallback, useEffect, useMemo, useState } from "react";
import type { CSSProperties } from "react";

import { useFridaySocket, initialUiState } from "../hooks/useFridaySocket";
import { ARCADE_GAMES, INSPECTOR_TABS, VISION_MODES } from "../types/protocol";
import type { InspectorTab, LogChannel } from "../types/protocol";
import { StreamCanvas } from "./StreamCanvas";

const TAB_META: Record<InspectorTab, { label: string; icon: typeof Camera }> = {
  camera: { label: "Camera", icon: Camera },
  arcade: { label: "Arcade", icon: Gamepad2 },
  wake: { label: "Wake", icon: Mic },
  history: { label: "History", icon: History },
  settings: { label: "Settings", icon: Settings },
};

const CHANNEL_TINT: Record<string, string> = {
  you: "#06F7FF",
  friday: "#B030FF",
  system: "#30D158",
  error: "#FF6961",
  vision: "#1E5FFF",
  security: "#FF2D9F",
  alert: "#FFD60A",
};

function pretty(value: string): string {
  return value.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

/**
 * The inspector window: the *only* place FRIDAY shows detail.
 *
 * It is a separate BrowserWindow (own socket, own snapshot) so the overlay
 * itself can stay a single quiet orb, and it is excluded from screen capture.
 */
export function InspectorApp({ initialTab }: { initialTab?: string }) {
  // Two independent live streams: 1 = vision camera, 2 = arcade. The arcade
  // tab used to render an empty box because only stream 1 was ever stored.
  const [frames, setFrames] = useState<{ camera: Uint8Array | null; arcade: Uint8Array | null }>({
    camera: null,
    arcade: null,
  });
  const onFrame = useCallback((stream: number, jpeg: Uint8Array) => {
    if (stream === 1) setFrames((current) => ({ ...current, camera: jpeg }));
    else if (stream === 2) setFrames((current) => ({ ...current, arcade: jpeg }));
  }, []);
  const [maximized, setMaximized] = useState(false);
  const socket = useFridaySocket({ onFrame });
  const ui = socket.ui.ready ? socket.ui : initialUiState;
  const send = socket.send;

  const [tab, setTab] = useState<InspectorTab>(() => {
    const candidate = (initialTab || ui.inspector.tab || "camera").toLowerCase();
    return (INSPECTOR_TABS as readonly string[]).includes(candidate)
      ? (candidate as InspectorTab)
      : "camera";
  });

  useEffect(() => {
    const off = window.fridayAPI?.onInspectorTab((next) => {
      if ((INSPECTOR_TABS as readonly string[]).includes(next)) setTab(next as InspectorTab);
    });
    return () => off?.();
  }, []);

  const selectTab = (next: InspectorTab) => {
    setTab(next);
    window.fridayAPI?.setInspectorTab(next);
  };

  const close = () => {
    send({ t: "close_inspector" });
    window.fridayAPI?.closeInspector();
  };

  const transcript = useMemo(() => ui.transcript.slice(-120).reverse(), [ui.transcript]);

  // The backend publishes its whole diagnostic tree as inspector props; show
  // it verbatim (pretty-printed) so a support session needs no second tool.
  const diagnostics = useMemo(() => {
    const raw = ui.inspector.props?.diagnostics;
    if (raw === undefined || raw === null) return "";
    try {
      return JSON.stringify(raw, null, 2);
    } catch {
      return "";
    }
  }, [ui.inspector.props]);

  return (
    <div className="inspector-shell flex h-full w-full flex-col text-siri-text">
      <header
        className="flex h-11 shrink-0 items-center gap-2 border-b border-white/[0.07] bg-[#111114] px-3"
        style={{ WebkitAppRegion: "drag" } as CSSProperties}
      >
        <span className="grid size-5 place-items-center rounded-full bg-gradient-to-br from-siri-cyan to-siri-violet text-[9px] font-black text-black">
          F
        </span>
        <h1 className="text-[12px] font-semibold tracking-[-0.01em]">FRIDAY Inspector</h1>
        <span className="ml-1 rounded-full bg-white/[0.06] px-2 py-0.5 text-[9.5px] font-medium uppercase tracking-[0.08em] text-white/40">
          {ui.state.connectionLabel || "offline"}
        </span>
        <div className="ml-auto flex items-center gap-1" style={{ WebkitAppRegion: "no-drag" } as CSSProperties}>
          <button
            type="button"
            className="focus-ring grid size-6 place-items-center rounded-full text-white/45 hover:bg-white/10 hover:text-white"
            onClick={() => {
              send({ t: "toggle_mute" });
            }}
            aria-label={ui.state.muted ? "Unmute" : "Mute"}
          >
            {ui.state.muted ? <VolumeX size={13} /> : <Volume2 size={13} />}
          </button>
          <span aria-hidden className="mx-0.5 h-4 w-px bg-white/10" />
          <button
            type="button"
            className="focus-ring grid size-6 place-items-center rounded-full text-white/45 hover:bg-white/10 hover:text-white"
            onClick={() => window.fridayAPI?.minimizeInspector()}
            aria-label="Minimize window"
          >
            <Minus size={13} strokeWidth={2.4} />
          </button>
          <button
            type="button"
            className="focus-ring grid size-6 place-items-center rounded-full text-white/45 hover:bg-white/10 hover:text-white"
            onClick={async () => {
              const next = await window.fridayAPI?.toggleMaximizeInspector();
              setMaximized(Boolean(next));
            }}
            aria-label={maximized ? "Restore window" : "Maximize window"}
          >
            {maximized ? (
              <Minimize2 size={12} strokeWidth={2.4} />
            ) : (
              <Maximize2 size={12} strokeWidth={2.4} />
            )}
          </button>
          <button
            type="button"
            className="focus-ring grid size-6 place-items-center rounded-full text-white/45 hover:bg-siri-red/20 hover:text-siri-red"
            onClick={close}
            aria-label="Close inspector"
          >
            <X size={14} strokeWidth={2.4} />
          </button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <nav className="flex w-[132px] shrink-0 flex-col gap-0.5 border-r border-white/[0.06] bg-[#0e0e12] p-2">
          {INSPECTOR_TABS.map((name) => {
            const meta = TAB_META[name];
            const Icon = meta.icon;
            const active = tab === name;
            return (
              <button
                key={name}
                type="button"
                onClick={() => selectTab(name)}
                aria-current={active ? "page" : undefined}
                className={`focus-ring flex h-8 items-center gap-2 rounded-lg px-2.5 text-[11.5px] font-medium ${
                  active
                    ? "bg-white/[0.09] text-white hairline"
                    : "text-white/50 hover:bg-white/[0.05] hover:text-white/80"
                }`}
              >
                <Icon size={13} strokeWidth={2.2} />
                {meta.label}
              </button>
            );
          })}
          <div className="mt-auto rounded-lg bg-white/[0.03] p-2 text-[10px] leading-snug text-white/35">
            <p className="font-semibold uppercase tracking-[0.08em] text-white/45">FRIDAY</p>
            <p className="mt-0.5">{ui.persona.voice} · {ui.persona.mood}</p>
            <p>{ui.theme.name} theme</p>
          </div>
        </nav>

        <motion.main
          key={tab}
          className="scrollbar-none min-w-0 flex-1 overflow-y-auto p-4"
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.18 }}
        >
          {tab === "camera" ? (
            <section className="space-y-3">
              <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-black">
                {frames.camera ? (
                  <StreamCanvas bytes={frames.camera} className="aspect-video w-full object-cover" />
                ) : (
                  <div className="grid aspect-video w-full place-items-center text-[11px] text-white/35">
                    <span className="flex items-center gap-1.5">
                      <MonitorPlay size={13} /> awaiting camera frames
                    </span>
                  </div>
                )}
                <span className="absolute left-2.5 top-2.5 rounded-full bg-black/60 px-2 py-0.5 text-[9.5px] font-semibold uppercase tracking-[0.08em] text-siri-cyan">
                  {ui.vision.mode}
                </span>
              </div>

              <div className="flex flex-wrap gap-1.5">
                {VISION_MODES.map((mode) => (
                  <button
                    key={mode}
                    type="button"
                    onClick={() => send({ t: "vision_action", mode })}
                    className={`rounded-full px-2.5 py-1 text-[10.5px] font-semibold uppercase tracking-[0.06em] hairline ${
                      ui.vision.mode === mode
                        ? "bg-siri-cyan/20 text-siri-cyan"
                        : "bg-white/[0.05] text-white/60 hover:bg-white/[0.1] hover:text-white"
                    }`}
                  >
                    {mode}
                  </button>
                ))}
              </div>

              <div className="rounded-xl bg-white/[0.04] p-3">
                <p className="text-[10px] font-semibold uppercase tracking-[0.09em] text-white/40">
                  signal
                </p>
                <p className="mt-0.5 text-[12px] text-white/80">{ui.vision.signal}</p>
                <p className="mt-2 text-[10px] font-semibold uppercase tracking-[0.09em] text-white/40">
                  detections
                </p>
                {ui.vision.detections.length ? (
                  <ul className="mt-1 space-y-0.5 text-[11px] text-white/70">
                    {ui.vision.detections.slice(0, 8).map((detection, i) => (
                      <li key={`${detection.label}-${i}`} className="flex justify-between">
                        <span>{detection.label}</span>
                        <span className="tabular-nums text-white/40">
                          {(detection.confidence * 100).toFixed(0)}%
                        </span>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="mt-1 text-[11px] text-white/35">nothing detected yet</p>
                )}
              </div>
            </section>
          ) : null}

          {tab === "arcade" ? (
            <section className="space-y-3">
              <div className="relative overflow-hidden rounded-2xl border border-white/10 bg-black">
                {frames.arcade ? (
                  <StreamCanvas
                    bytes={frames.arcade}
                    className="aspect-video w-full object-cover"
                    label="FRIDAY arcade"
                  />
                ) : (
                  <div className="grid aspect-video w-full place-items-center text-[11px] text-white/35">
                    <span className="flex items-center gap-1.5">
                      <Gamepad2 size={13} /> awaiting arcade frames
                    </span>
                  </div>
                )}
                <span className="absolute left-2.5 top-2.5 rounded-full bg-black/65 px-2 py-0.5 text-[9.5px] font-semibold uppercase tracking-[0.08em] text-siri-cyan">
                  {ui.arcade.game.replace(/_/g, " ")}
                </span>
                <span className="absolute right-2.5 top-2.5 rounded-full bg-black/65 px-2 py-0.5 text-[9.5px] font-semibold tabular-nums text-white/85">
                  {ui.arcade.score} pts · {ui.arcade.lives} lives
                </span>
                <span className="absolute bottom-2.5 left-2.5 rounded-full bg-black/65 px-2 py-0.5 text-[9.5px] font-medium text-white/70">
                  {ui.arcade.open ? "live" : "idle"} · {ui.arcade.message}
                </span>
              </div>
              <div className="grid grid-cols-3 gap-2">
                <div className="rounded-xl bg-white/[0.04] p-2.5">
                  <p className="text-[9.5px] uppercase tracking-[0.09em] text-white/40">Score</p>
                  <p className="text-[18px] font-semibold tabular-nums text-siri-cyan">{ui.arcade.score}</p>
                </div>
                <div className="rounded-xl bg-white/[0.04] p-2.5">
                  <p className="text-[9.5px] uppercase tracking-[0.09em] text-white/40">Lives</p>
                  <p className="text-[18px] font-semibold tabular-nums">{ui.arcade.lives}</p>
                </div>
                <div className="rounded-xl bg-white/[0.04] p-2.5">
                  <p className="text-[9.5px] uppercase tracking-[0.09em] text-white/40">State</p>
                  <p className="truncate text-[12px] font-medium">{ui.arcade.open ? "live" : "idle"}</p>
                </div>
              </div>
              <p className="text-[11.5px] text-white/55">{ui.arcade.message}</p>
              <div className="grid grid-cols-2 gap-1.5">
                {ARCADE_GAMES.map((game) => (
                  <button
                    key={game}
                    type="button"
                    onClick={() => send({ t: "arcade_game", game })}
                    className={`focus-ring rounded-xl px-3 py-2 text-left text-[11px] font-medium hairline ${
                      ui.arcade.game === game
                        ? "bg-siri-violet/20 text-white"
                        : "bg-white/[0.04] text-white/65 hover:bg-white/[0.09] hover:text-white"
                    }`}
                  >
                    {pretty(game)}
                  </button>
                ))}
              </div>
            </section>
          ) : null}

          {tab === "wake" ? (
            <section className="space-y-3">
              <div className="rounded-2xl bg-gradient-to-br from-white/[0.07] to-white/[0.02] p-4 hairline">
                <div className="flex items-center gap-2">
                  <span
                    className="size-2 rounded-full"
                    style={{ backgroundColor: ui.wake.active ? "#30D158" : "#FF6961" }}
                  />
                  <p className="text-[13px] font-semibold">{ui.wake.active ? "Listening for wake word" : "Wake word disabled"}</p>
                </div>
                <p className="mt-1 text-[11.5px] text-white/60">{ui.wake.status}</p>
                <p className="mt-2 rounded-lg bg-black/30 px-2.5 py-1.5 text-[12px] font-medium tracking-wide text-siri-cyan">
                  “{ui.wake.phrase}”
                </p>
              </div>
              <div className="flex flex-wrap gap-1.5">
                {[
                  { label: "Restart listener", command: "restart wake word listener" },
                  { label: "Enable", command: "enable wake word" },
                  { label: "Disable", command: "disable wake word" },
                ].map((action) => (
                  <button
                    key={action.label}
                    type="button"
                    onClick={() => send({ t: "command", text: action.command })}
                    className="rounded-full bg-white/[0.06] px-3 py-1 text-[11px] font-medium text-white/75 hairline hover:bg-white/[0.12] hover:text-white"
                  >
                    {action.label}
                  </button>
                ))}
              </div>
            </section>
          ) : null}

          {tab === "history" ? (
            <section className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-[10px] font-semibold uppercase tracking-[0.09em] text-white/40">
                  {ui.transcript.length} events
                </p>
                <button
                  type="button"
                  onClick={() => send({ t: "clear_transcript" })}
                  className="rounded-full bg-white/[0.06] px-2.5 py-0.5 text-[10px] font-medium text-white/60 hover:bg-white/[0.12] hover:text-white"
                >
                  Clear
                </button>
              </div>
              <ul className="space-y-1">
                {transcript.map((entry) => (
                  <li
                    key={`${entry.seq}-${entry.ts}`}
                    className="flex gap-2 rounded-lg bg-white/[0.03] px-2.5 py-1.5 text-[11.5px]"
                  >
                    <span
                      className="mt-1 size-1.5 shrink-0 rounded-full"
                      style={{ backgroundColor: CHANNEL_TINT[entry.channel as LogChannel] ?? "#06F7FF" }}
                    />
                    <div className="min-w-0 flex-1">
                      <p className="break-words text-white/80">{entry.text}</p>
                      <p className="mt-0.5 text-[9.5px] uppercase tracking-[0.06em] text-white/30">
                        {entry.channel} · {entry.clock || new Date((entry.ts || 0) * 1000).toLocaleTimeString()}
                      </p>
                    </div>
                  </li>
                ))}
                {transcript.length === 0 ? (
                  <li className="rounded-lg bg-white/[0.03] px-2.5 py-6 text-center text-[11px] text-white/35">
                    No history yet.
                  </li>
                ) : null}
              </ul>
            </section>
          ) : null}

          {tab === "settings" ? (
            <section className="space-y-3">
              <div className="rounded-xl bg-white/[0.04] p-3">
                <SettingRow label="Theme" value={ui.theme.name} />
                <SettingRow label="Reduced motion" value={ui.theme.reducedMotion ? "on" : "off"} />
                <SettingRow label="Transparency" value={ui.theme.transparency === false ? "opaque" : "on"} />
                <SettingRow label="Persona" value={`${ui.persona.mode} · ${ui.persona.voice}`} />
                <SettingRow label="Status" value={ui.state.statusLabel} />
                <SettingRow label="Connection" value={ui.state.connectionLabel || "local"} />
              </div>

              <div className="rounded-xl bg-white/[0.04] p-3">
                <details className="group">
                  <summary className="cursor-pointer list-none text-[10px] font-semibold uppercase tracking-[0.09em] text-white/40 group-open:text-white/60">
                    Live diagnostics
                  </summary>
                  {diagnostics ? (
                    <pre className="scrollbar-none mt-2 max-h-[220px] overflow-auto rounded-lg bg-black/40 p-2 text-[10px] leading-relaxed text-white/70">
                      {diagnostics}
                    </pre>
                  ) : (
                    <p className="mt-1 text-[11px] text-white/35">
                      no diagnostics reported yet
                    </p>
                  )}
                </details>
              </div>

              <div className="flex flex-wrap gap-1.5">
                <button
                  type="button"
                  onClick={() => send({ t: "toggle_mute" })}
                  className="rounded-full bg-white/[0.06] px-3 py-1 text-[11px] font-medium text-white/75 hairline hover:bg-white/[0.12] hover:text-white"
                >
                  {ui.state.muted ? "Unmute" : "Mute"}
                </button>
                <button
                  type="button"
                  onClick={() => send({ t: "toggle_silent" })}
                  className="rounded-full bg-white/[0.06] px-3 py-1 text-[11px] font-medium text-white/75 hairline hover:bg-white/[0.12] hover:text-white"
                >
                  {ui.state.silentMode ? "Silent mode off" : "Silent mode on"}
                </button>
                <button
                  type="button"
                  onClick={() => send({ t: "set_theme", name: "siri" })}
                  className="rounded-full bg-white/[0.06] px-3 py-1 text-[11px] font-medium text-white/75 hairline hover:bg-white/[0.12] hover:text-white"
                >
                  Reset theme
                </button>
              </div>

              <button
                type="button"
                onClick={() => {
                  send({ t: "request_quit" });
                  window.fridayAPI?.quitApp();
                }}
                className="mt-2 flex w-full items-center justify-center gap-1.5 rounded-xl bg-siri-red/15 py-2 text-[11.5px] font-semibold text-siri-red hover:bg-siri-red/25"
              >
                <Activity size={12} /> Quit FRIDAY
              </button>
            </section>
          ) : null}
        </motion.main>
      </div>
    </div>
  );
}

function SettingRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex items-baseline justify-between gap-3 border-b border-white/[0.05] py-1 text-[11.5px] last:border-0">
      <span className="text-white/45">{label}</span>
      <span className="truncate font-medium text-white/80">{value}</span>
    </div>
  );
}

export default InspectorApp;
