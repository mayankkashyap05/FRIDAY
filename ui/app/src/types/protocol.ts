/**
 * Wire protocol shared with `ui/protocol.py`.
 *
 * Python → renderer
 * -----------------
 * Text frames are compact JSON objects discriminated by `t`. A `snapshot`
 * (full authoritative state) is pushed on every connect/reconnect; everything
 * else is a delta. Binary frames are `<stream byte><jpeg bytes>`.
 *
 * Renderer → Python
 * -----------------
 * Small intent messages (`submit_text`, `toggle_mute`, …). Typed here so a typo
 * cannot silently stop working.
 */

export const STREAM_VISION = 1;
export const STREAM_ARCADE = 2;
export type StreamId = typeof STREAM_VISION | typeof STREAM_ARCADE;

export type AssistantState =
  | "BOOTING"
  | "LISTENING"
  | "THINKING"
  | "PROCESSING"
  | "SPEAKING"
  | "MUTED";

export type Severity = "info" | "success" | "warn" | "error" | "security";

export type CardKind =
  | "text"
  | "weather"
  | "calendar"
  | "email"
  | "flight"
  | "telemetry"
  | "control"
  | "document"
  | "confirmation";

export type LogChannel =
  | "you"
  | "friday"
  | "system"
  | "error"
  | "vision"
  | "security"
  | "alert";

export interface StatePayload {
  state: AssistantState;
  statusLabel: string;
  energy: number;
  muted: boolean;
  silentMode: boolean;
  speaking: boolean;
  connected: boolean;
  connecting: boolean;
  connectionLabel: string;
  dnd: boolean;
  invoked: boolean;
  clock: string;
  micAmplitude: number;
}

export interface CardItem {
  uid: string;
  kind: CardKind;
  title: string;
  body: string;
  footer: string;
  payload: Record<string, unknown>;
  severity: Severity;
  ts: number;
  ttl: number;
  dismissible: boolean;
  age?: number;
}

export interface ActivityItem {
  aid: string;
  title: string;
  kind: string;
  detail: string;
  progress: number;
  active: boolean;
  ts: number;
}

export interface TranscriptItem {
  text: string;
  channel: LogChannel;
  severity: Severity;
  source: string;
  ts: number;
  seq: number;
  clock: string;
}

export interface PersonaState {
  mode: string;
  mood: string;
  voice: string;
  badge?: string;
}

export interface VisionState {
  signal: string;
  mode: string;
  previewOpen: boolean;
  detections: Array<{ label: string; confidence: number; box: number[] }>;
}

export interface ArcadeState {
  open: boolean;
  game: string;
  score: number;
  lives: number;
  message: string;
}

export interface InspectorState {
  open: boolean;
  tab: string;
  props: Record<string, unknown>;
}

export interface WakeState {
  active: boolean;
  status: string;
  phrase: string;
}

export interface ThemePalette {
  name: string;
  pri: string;
  mid: string;
  acc: string;
  acc2: string;
  text: string;
  green: string;
  red: string;
  reducedMotion?: boolean;
  transparency?: boolean;
  uiScale?: number;
  [key: string]: string | number | boolean | undefined;
}

/** Everything the renderer knows. Deliberately flat and serialisable. */
export interface UiSnapshot {
  state: StatePayload;
  cards: CardItem[];
  activities: ActivityItem[];
  activeCount: number;
  transcript: TranscriptItem[];
  persona: PersonaState;
  vision: VisionState;
  arcade: ArcadeState;
  setup: { visible: boolean };
  inspector: InspectorState;
  theme: ThemePalette;
  wake: WakeState;
}

export interface UiState extends UiSnapshot {
  /** Live microphone level, updated at ≤60 Hz. */
  amplitude: number;
  /** The single floating line above the orb (partial utterance). */
  liveTranscript: string;
  /** Operator toast (transient). */
  toast: { text: string; severity: Severity } | null;
  /** True once a snapshot has been received. */
  ready: boolean;
}

export type ServerMessage =
  | ({ t: "snapshot" } & UiSnapshot)
  | ({ t: "state" } & Partial<StatePayload>)
  | { t: "amplitude"; value: number }
  | { t: "live_transcript"; text: string }
  | ({ t: "transcript_append" } & TranscriptItem)
  | { t: "transcript_clear" }
  | { t: "cards"; items: CardItem[] }
  | { t: "activities"; items: ActivityItem[]; activeCount: number }
  | ({ t: "persona" } & PersonaState)
  | ({ t: "vision" } & VisionState)
  | ({ t: "arcade" } & ArcadeState)
  | { t: "setup"; visible: boolean }
  | ({ t: "inspector" } & InspectorState)
  | { t: "toast"; text: string; severity: Severity }
  | ({ t: "wake" } & WakeState)
  | ({ t: "theme" } & ThemePalette)
  | { t: "shutdown" }
  | { t: "pong"; ts: number };

/** Client → Python intents. */
export type ClientMessage =
  | { t: "submit_text"; text: string }
  | { t: "submit_api_key"; key: string }
  | { t: "dismiss_card"; uid: string }
  | { t: "clear_cards" }
  | { t: "clear_transcript" }
  | { t: "cancel_activity"; aid: string }
  | { t: "toggle_mute" }
  | { t: "toggle_silent" }
  | { t: "cancel" }
  | { t: "barge_in" }
  | { t: "open_inspector"; tab: string }
  | { t: "close_inspector" }
  | { t: "vision_action"; mode: string }
  | { t: "arcade_game"; game: string }
  | { t: "command"; text: string }
  | { t: "window_dismissed" }
  | { t: "interaction" }
  | { t: "typing_changed"; value: boolean }
  | { t: "pointer_inside"; value: boolean }
  | { t: "set_theme"; name: string }
  | { t: "ping" }
  | { t: "request_quit" };

export interface WsConnection {
  url: string;
  token: string;
}

/** The strict surface exposed by `electron/preload.ts`. */
export interface FridayAPI {
  windowKind: "stage" | "inspector" | "dev";
  getConnection(): Promise<WsConnection | null>;
  setInteractive(interactive: boolean): void;
  setInvoked(invoked: boolean): void;
  setFocusable(focusable: boolean): void;
  focusStage(): void;
  openInspector(tab: string): void;
  closeInspector(): void;
  setInspectorTab(tab: string): void;
  setEscapeArmed(armed: boolean): void;
  quitApp(): void;
  onEscape(cb: () => void): () => void;
  onInspectorTab(cb: (tab: string) => void): () => void;
  onInspectorClosed(cb: () => void): () => void;
}

declare global {
  interface Window {
    fridayAPI?: FridayAPI;
  }
}

export const VISION_MODES = [
  "GESTURE",
  "OBJECTS",
  "AIR MOUSE",
  "VOLUME",
  "POSTURE",
  "MOTION",
  "STOP",
] as const;

export const ARCADE_GAMES = [
  "face_filter",
  "gesture_runner",
  "block_breaker",
  "rhythm_catch",
  "pose_mirror",
  "target_tap",
  "balance_beam",
  "bubble_pop",
  "shadow_box",
  "neon_dodge",
] as const;

export const INSPECTOR_TABS = [
  "camera",
  "arcade",
  "wake",
  "history",
  "settings",
] as const;

export type InspectorTab = (typeof INSPECTOR_TABS)[number];
