import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { initialUiState } from "../hooks/useFridaySocket";
import type { UiState } from "../types/protocol";
import { __resetInteractiveRegions } from "../hooks/useInteractiveRegion";

/**
 * StageApp's window discipline, tested without a socket or a GPU.
 *
 * `useFridaySocket` is replaced by a controllable state object and `SiriOrb` by
 * two buttons (activate / finish-dismissal), so the assertions are about the
 * *wiring* — which IPC calls the stage makes, and when.
 */

const h = vi.hoisted(() => ({
  ui: { current: null as unknown },
  send: vi.fn(),
}));

vi.mock("../hooks/useFridaySocket", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../hooks/useFridaySocket")>();
  return {
    ...actual,
    useFridaySocket: () => ({
      ui: h.ui.current,
      connected: true,
      send: h.send,
      dispatchLocal: vi.fn(),
    }),
  };
});

vi.mock("../components/SiriOrb", () => ({
  SiriOrb: (props: {
    invoked: boolean;
    acting?: boolean;
    speechPulse?: number;
    onActivate: () => void;
    onDismissed: () => void;
  }) => (
    <div
      data-testid="orb-stub"
      data-invoked={String(props.invoked)}
      data-acting={String(Boolean(props.acting))}
      data-pulse={String(props.speechPulse ?? 0)}
    >
      <button type="button" onClick={props.onActivate}>
        activate
      </button>
      <button type="button" onClick={props.onDismissed}>
        finish-dismiss
      </button>
    </div>
  ),
}));

import { StageApp } from "../components/StageApp";

function ui(patch: Partial<UiState> = {}, state: Partial<UiState["state"]> = {}): UiState {
  return {
    ...initialUiState,
    ...patch,
    ready: true,
    state: { ...initialUiState.state, ...state, invoked: state.invoked ?? false },
  };
}

let api: Record<string, ReturnType<typeof vi.fn>>;

beforeEach(() => {
  __resetInteractiveRegions();
  h.send.mockClear();
  h.ui.current = ui();
  api = {
    setInteractive: vi.fn(),
    setFocusable: vi.fn(),
    setInvoked: vi.fn(),
    setEscapeArmed: vi.fn(),
    hideStage: vi.fn(),
    openInspector: vi.fn(),
    closeInspector: vi.fn(),
    getConnection: vi.fn(async () => null),
    onEscape: vi.fn(() => vi.fn()),
    onInspectorClosed: vi.fn(() => vi.fn()),
    onInspectorTab: vi.fn(() => vi.fn()),
  };
  (window as unknown as { fridayAPI: unknown }).fridayAPI = api;
});

describe("StageApp window discipline", () => {
  it("summons by voice without ever stealing focus", () => {
    h.ui.current = ui({}, { invoked: true, state: "LISTENING" });
    render(<StageApp />);

    expect(api.setFocusable).not.toHaveBeenCalledWith(true);
    expect(api.setFocusable).toHaveBeenLastCalledWith(false);
    expect(api.setInvoked).toHaveBeenLastCalledWith(true);
  });

  it("takes focus only when the type-to-Siri pill is open", () => {
    h.ui.current = ui({}, { invoked: true });
    render(<StageApp />);
    expect(api.setFocusable).not.toHaveBeenCalledWith(true);

    fireEvent.click(screen.getByRole("button", { name: "activate" }));
    expect(api.setFocusable).toHaveBeenLastCalledWith(true);

    fireEvent.keyDown(screen.getByLabelText("Message FRIDAY"), { key: "Escape" });
    expect(api.setFocusable).toHaveBeenLastCalledWith(false);
  });

  it("takes focus while the first-run setup card owns the stage", () => {
    h.ui.current = ui({ setup: { visible: true } }, { invoked: true });
    render(<StageApp />);
    expect(api.setFocusable).toHaveBeenLastCalledWith(true);
  });

  it("hides the stage HWND once the dismissal contraction completes", () => {
    h.ui.current = ui({}, { invoked: false });
    render(<StageApp />);
    expect(api.hideStage).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "finish-dismiss" }));
    expect(api.hideStage).toHaveBeenCalledTimes(1);
    expect(h.send).toHaveBeenCalledWith({ t: "window_dismissed" });
  });

  it("never hides a stage that is still summoned", () => {
    h.ui.current = ui({}, { invoked: true });
    render(<StageApp />);
    fireEvent.click(screen.getByRole("button", { name: "finish-dismiss" }));
    expect(api.hideStage).not.toHaveBeenCalled();
  });

  it("closes the inspector window on a true→false edge only", () => {
    h.ui.current = ui({ inspector: { open: true, tab: "camera", props: {} } });
    const { rerender } = render(<StageApp />);
    expect(api.openInspector).toHaveBeenCalledWith("camera");
    expect(api.closeInspector).not.toHaveBeenCalled();

    h.ui.current = ui({ inspector: { open: false, tab: "camera", props: {} } });
    rerender(<StageApp />);
    expect(api.closeInspector).toHaveBeenCalledTimes(1);
  });

  it("feeds the orb the active-work and speech-pulse signals", () => {
    h.ui.current = ui(
      {
        activeCount: 2,
        activities: [
          { aid: "a", title: "job", kind: "task", detail: "", progress: -1, active: true, ts: 0 },
        ],
        liveTranscript: "hello",
      },
      { invoked: true, state: "SPEAKING", speaking: true },
    );
    const { rerender } = render(<StageApp />);
    expect(screen.getByTestId("orb-stub")).toHaveAttribute("data-acting", "true");

    const before = Number(screen.getByTestId("orb-stub").getAttribute("data-pulse"));
    h.ui.current = {
      ...(h.ui.current as UiState),
      liveTranscript: "hello there, this is FRIDAY speaking",
    };
    rerender(<StageApp />);
    const after = Number(screen.getByTestId("orb-stub").getAttribute("data-pulse"));
    expect(after).toBeGreaterThan(before);
  });
});
