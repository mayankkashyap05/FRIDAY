import { describe, expect, it } from "vitest";

import { applyMessage, initialUiState } from "../hooks/useFridaySocket";
import type { CardItem, ServerMessage } from "../types/protocol";

const card = (overrides: Partial<CardItem> = {}): CardItem => ({
  uid: "c1",
  kind: "text",
  title: "hi",
  body: "there",
  footer: "",
  payload: {},
  severity: "info",
  ts: 1,
  ttl: 0,
  dismissible: true,
  ...overrides,
});

describe("applyMessage", () => {
  it("hydrates everything from a snapshot and marks the state ready", () => {
    const message: ServerMessage = {
      t: "snapshot",
      state: { ...initialUiState.state, state: "LISTENING", statusLabel: "LISTENING", invoked: true },
      cards: [card()],
      activities: [],
      activeCount: 0,
      transcript: [],
      persona: { mode: "normal", mood: "calm", voice: "Aoede" },
      vision: { signal: "idle", mode: "STANDBY", previewOpen: false, detections: [] },
      arcade: { open: false, game: "face_filter", score: 0, lives: 3, message: "Ready" },
      setup: { visible: false },
      inspector: { open: false, tab: "", props: {} },
      theme: { name: "siri", pri: "#06F7FF", mid: "#1E5FFF", acc: "#FF2D9F", acc2: "#B030FF", text: "#F5F5F7", green: "#30D158", red: "#FF6961" },
      wake: { active: true, status: "listening", phrase: "Hey Friday" },
    };

    const ui = applyMessage(initialUiState, message);
    expect(ui.ready).toBe(true);
    expect(ui.state.state).toBe("LISTENING");
    expect(ui.state.invoked).toBe(true);
    expect(ui.cards).toHaveLength(1);
    expect(ui.wake.phrase).toBe("Hey Friday");
  });

  it("merges a partial state delta without dropping unrelated fields", () => {
    const ui = applyMessage(initialUiState, {
      t: "state",
      state: "SPEAKING",
      statusLabel: "SPEAKING",
      energy: 0.9,
    });
    expect(ui.state.state).toBe("SPEAKING");
    expect(ui.state.energy).toBe(0.9);
    expect(ui.state.muted).toBe(false);
    expect(ui.state.connectionLabel).toBe("OFFLINE");
  });

  it("tracks the amplitude stream on both fields", () => {
    const ui = applyMessage(initialUiState, { t: "amplitude", value: 0.42 });
    expect(ui.amplitude).toBe(0.42);
    expect(ui.state.micAmplitude).toBe(0.42);
  });

  it("appends transcript entries and caps the list at 400", () => {
    let ui = applyMessage(initialUiState, {
      t: "transcript_append",
      text: "hello",
      channel: "you",
      severity: "info",
      source: "mic",
      ts: 1,
      seq: 1,
      clock: "10:00",
    });
    expect(ui.transcript).toHaveLength(1);
    expect(ui.transcript[0].text).toBe("hello");

    for (let i = 0; i < 405; i++) {
      ui = applyMessage(ui, {
        t: "transcript_append",
        text: `line ${i}`,
        channel: "friday",
        severity: "info",
        source: "tts",
        ts: i,
        seq: i,
        clock: "",
      });
    }
    expect(ui.transcript).toHaveLength(400);
    expect(ui.transcript.at(-1)?.text).toBe("line 404");
  });

  it("replaces cards wholesale (CardsModel is the source of truth)", () => {
    const first = applyMessage(initialUiState, { t: "cards", items: [card()] });
    const second = applyMessage(first, { t: "cards", items: [card({ uid: "c2" })] });
    expect(second.cards).toHaveLength(1);
    expect(second.cards[0].uid).toBe("c2");
  });

  it("counts active activities when the backend omits the count", () => {
    const ui = applyMessage(initialUiState, {
      t: "activities",
      items: [
        { aid: "a", title: "run", kind: "task", detail: "", progress: 0.2, active: true, ts: 1 },
        { aid: "b", title: "done", kind: "task", detail: "", progress: 1, active: false, ts: 1 },
      ],
      activeCount: undefined as unknown as number,
    });
    expect(ui.activeCount).toBe(1);
  });

  it("ignores pong and unknown messages", () => {
    const ui = applyMessage(initialUiState, { t: "pong", ts: 2 });
    expect(ui).toBe(initialUiState);
    const weird = applyMessage(initialUiState, { t: "nope" } as unknown as ServerMessage);
    expect(weird).toBe(initialUiState);
  });

  it("stores live transcript text", () => {
    const ui = applyMessage(initialUiState, { t: "live_transcript", text: "play some jazz" });
    expect(ui.liveTranscript).toBe("play some jazz");
  });
});
