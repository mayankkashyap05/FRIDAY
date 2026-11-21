import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  STREAM_ARCADE,
  STREAM_VISION,
  type ClientMessage,
  type ServerMessage,
  type StreamId,
  type UiState,
  type WsConnection,
} from "../types/protocol";

/** The state a renderer shows before the first snapshot arrives. */
export const initialUiState: UiState = {
  state: {
    state: "BOOTING",
    statusLabel: "INITIALISING",
    energy: 0.24,
    muted: false,
    silentMode: false,
    speaking: false,
    connected: false,
    connecting: false,
    connectionLabel: "OFFLINE",
    dnd: false,
    invoked: false,
    clock: "",
    micAmplitude: 0,
  },
  cards: [],
  activities: [],
  activeCount: 0,
  transcript: [],
  persona: { mode: "normal", mood: "calm", voice: "Aoede", badge: "" },
  vision: { signal: "Awaiting camera input", mode: "STANDBY", previewOpen: false, detections: [] },
  arcade: { open: false, game: "face_filter", score: 0, lives: 3, message: "Ready" },
  setup: { visible: false },
  inspector: { open: false, tab: "", props: {} },
  theme: { name: "siri", pri: "#06F7FF", mid: "#1E5FFF", acc: "#FF2D9F", acc2: "#B030FF", text: "#F5F5F7", green: "#30D158", red: "#FF6961" },
  wake: { active: false, status: "Engine idle", phrase: "Hey Friday" },
  amplitude: 0,
  liveTranscript: "",
  toast: null,
  ready: false,
};

const MAX_TRANSCRIPT = 400;

/**
 * Pure reducer for server messages. Exported so the protocol can be unit-tested
 * without a socket, a DOM or Electron.
 */
export function applyMessage(ui: UiState, message: ServerMessage): UiState {
  switch (message.t) {
    case "snapshot":
      return {
        ...ui,
        state: { ...ui.state, ...message.state },
        cards: message.cards ?? [],
        activities: message.activities ?? [],
        activeCount: message.activeCount ?? 0,
        transcript: (message.transcript ?? []).slice(-MAX_TRANSCRIPT),
        persona: message.persona ?? ui.persona,
        vision: message.vision ?? ui.vision,
        arcade: message.arcade ?? ui.arcade,
        setup: message.setup ?? ui.setup,
        inspector: message.inspector ?? ui.inspector,
        theme: { ...ui.theme, ...(message.theme ?? {}) },
        wake: message.wake ?? ui.wake,
        amplitude: message.state?.micAmplitude ?? ui.amplitude,
        ready: true,
      };
    case "state": {
      const { t: _ignored, ...patch } = message;
      return {
        ...ui,
        state: { ...ui.state, ...patch },
        amplitude:
          typeof patch.micAmplitude === "number" ? patch.micAmplitude : ui.amplitude,
      };
    }
    case "amplitude":
      return {
        ...ui,
        amplitude: message.value,
        state: { ...ui.state, micAmplitude: message.value },
        audioBands: message.low !== undefined || message.mid !== undefined || message.high !== undefined
          ? {
              rms: message.value,
              low: message.low ?? 0,
              mid: message.mid ?? 0,
              high: message.high ?? 0,
              source: message.source ?? "mic",
              ts: Date.now(),
            }
          : ui.audioBands,
      };
    case "live_transcript":
      return { ...ui, liveTranscript: message.text };
    case "transcript_append": {
      const { t: _ignored, ...item } = message;
      const transcript = [...ui.transcript, item].slice(-MAX_TRANSCRIPT);
      return { ...ui, transcript };
    }
    case "transcript_clear":
      return { ...ui, transcript: [] };
    case "cards":
      return { ...ui, cards: message.items ?? [] };
    case "activities":
      return {
        ...ui,
        activities: message.items ?? [],
        activeCount: message.activeCount ?? (message.items ?? []).filter((a) => a.active).length,
      };
    case "persona":
      return { ...ui, persona: { ...ui.persona, ...message } };
    case "vision":
      return { ...ui, vision: { ...ui.vision, ...message } };
    case "arcade":
      return { ...ui, arcade: { ...ui.arcade, ...message } };
    case "setup":
      return { ...ui, setup: { visible: message.visible } };
    case "inspector":
      return { ...ui, inspector: { ...ui.inspector, ...message } };
    case "toast":
      return { ...ui, toast: { text: message.text, severity: message.severity } };
    case "wake":
      return { ...ui, wake: { ...ui.wake, ...message } };
    case "theme":
      return {
        ...ui,
        theme: { ...ui.theme, ...(message as unknown as UiState["theme"]) },
      };
    case "shutdown":
    case "pong":
    default:
      return ui;
  }
}

export interface UseFridaySocketOptions {
  onFrame?: (stream: StreamId, jpeg: Uint8Array) => void;
  onShutdown?: () => void;
  /** Skip the socket entirely (dev preview drives local state). */
  paused?: boolean;
}

export interface FridaySocket {
  ui: UiState;
  connected: boolean;
  send: (message: ClientMessage) => boolean;
  dispatchLocal: (message: ServerMessage) => void;
}

function resolveConnection(): Partial<WsConnection> | null {
  const api = typeof window !== "undefined" ? window.fridayAPI : undefined;
  if (api) {
    // Async, resolved in an effect; start from nothing.
    return null;
  }
  const env = (import.meta as unknown as { env?: Record<string, string> }).env ?? {};
  const url = env.VITE_FRIDAY_WS_URL ?? "";
  const token = env.VITE_FRIDAY_WS_TOKEN ?? "";
  return url ? { url, token } : null;
}

function decodeFrame(data: ArrayBuffer): { stream: StreamId; jpeg: Uint8Array } | null {
  const bytes = new Uint8Array(data);
  if (bytes.length < 3) return null;
  const stream = bytes[0];
  if (stream !== STREAM_VISION && stream !== STREAM_ARCADE) return null;
  return { stream: stream as StreamId, jpeg: bytes.subarray(1) };
}

/**
 * The renderer's single connection to Python.
 *
 * Reconnects with a short backoff while it has ever been connected, and exits
 * the app ~1.2 s after an explicit `shutdown` message so an abrupt Python exit
 * can never leave a zombie overlay window on screen.
 */
export function useFridaySocket(
  options: UseFridaySocketOptions = {},
): FridaySocket {
  const { onFrame, onShutdown, paused = false } = options;
  const [ui, setUi] = useState<UiState>(initialUiState);
  const [connected, setConnected] = useState(false);
  const socketRef = useRef<WebSocket | null>(null);
  const closingRef = useRef(false);
  const onFrameRef = useRef(onFrame);
  const onShutdownRef = useRef(onShutdown);
  onFrameRef.current = onFrame;
  onShutdownRef.current = onShutdown;

  const dispatchLocal = useCallback((message: ServerMessage) => {
    setUi((current) => applyMessage(current, message));
  }, []);

  const send = useCallback((message: ClientMessage): boolean => {
    const socket = socketRef.current;
    if (!socket || socket.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(message));
    return true;
  }, []);

  useEffect(() => {
    if (paused) return;
    let cancelled = false;
    let retry = 0;
    let retryTimer: number | undefined;
    let shutdownTimer: number | undefined;
    let everConnected = false;

    const quit = () => {
      const api = window.fridayAPI;
      if (api) api.quitApp();
    };

    const open = async (): Promise<void> => {
      let connection: Partial<WsConnection> | null = resolveConnection();
      if (window.fridayAPI) {
        try {
          connection = await window.fridayAPI.getConnection();
        } catch {
          connection = null;
        }
      }
      if (cancelled) return;
      if (!connection || !connection.url) {
        // No transport (browser dev preview): stay quiet and let the local
        // state drive the UI.
        setConnected(false);
        return;
      }

      const separator = connection.url.includes("?") ? "&" : "?";
      const socket = new WebSocket(
        `${connection.url}${separator}token=${encodeURIComponent(connection.token ?? "")}`,
      );
      socket.binaryType = "arraybuffer";
      socketRef.current = socket;

      socket.onopen = () => {
        retry = 0;
        everConnected = true;
        setConnected(true);
      };

      socket.onmessage = (event: MessageEvent<string | ArrayBuffer>) => {
        if (typeof event.data === "string") {
          let message: ServerMessage;
          try {
            message = JSON.parse(event.data) as ServerMessage;
          } catch {
            return;
          }
          if (message.t === "shutdown") {
            closingRef.current = true;
            setConnected(false);
            try {
              socket.close();
            } catch {
              /* already gone */
            }
            onShutdownRef.current?.();
            // Python is gone: do not leave an orphan window behind.
            shutdownTimer = window.setTimeout(quit, 1200);
            return;
          }
          setUi((current) => applyMessage(current, message));
          return;
        }
        const frame = decodeFrame(event.data);
        if (frame) onFrameRef.current?.(frame.stream, frame.jpeg);
      };

      socket.onclose = () => {
        setConnected(false);
        socketRef.current = null;
        if (cancelled || closingRef.current) return;
        // Reconnect while Python may still be alive. Once it has *ever*
        // connected, keep trying: a crash-restart of the backend is common
        // during development.
        if (everConnected) {
          retry = Math.min(retry + 1, 20);
          retryTimer = window.setTimeout(open, Math.min(250 * retry, 2000));
        }
      };

      socket.onerror = () => {
        // onclose follows; nothing to do here.
      };
    };

    void open();

    return () => {
      cancelled = true;
      if (retryTimer) window.clearTimeout(retryTimer);
      if (shutdownTimer) window.clearTimeout(shutdownTimer);
      const socket = socketRef.current;
      socketRef.current = null;
      if (socket) {
        socket.onclose = null;
        socket.close();
      }
    };
  }, [paused]);

  return useMemo(
    () => ({ ui, connected, send, dispatchLocal }),
    [ui, connected, send, dispatchLocal],
  );
}
