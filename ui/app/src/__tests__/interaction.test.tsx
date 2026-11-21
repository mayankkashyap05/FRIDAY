import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DevPreviewPanel } from "../components/DevPreviewPanel";
import { LiveActivityBar } from "../components/LiveActivityBar";
import { LiveTranscript } from "../components/LiveTranscript";
import { SnippetStack } from "../components/SnippetStack";
import { TypeToSiriPill } from "../components/TypeToSiriPill";
import { BaseGlassCard } from "../components/cards/BaseGlassCard";
import type { ActivityItem, CardItem } from "../types/protocol";
import {
  __resetInteractiveRegions,
  useInteractiveRegion,
} from "../hooks/useInteractiveRegion";

function Region({ label }: { label: string }) {
  const region = useInteractiveRegion({});
  return (
    <div data-testid={label} {...region.regionProps}>
      {label}
    </div>
  );
}

describe("useInteractiveRegion", () => {
  const setInteractive = vi.fn();

  beforeEach(() => {
    __resetInteractiveRegions();
    setInteractive.mockClear();
    (window as unknown as { fridayAPI: unknown }).fridayAPI = {
      setInteractive,
      quitApp: vi.fn(),
      setFocusable: vi.fn(),
      setEscapeArmed: vi.fn(),
    };
  });

  afterEach(() => {
    delete (window as unknown as { fridayAPI?: unknown }).fridayAPI;
  });

  it("makes the window interactive on hover and click-through when empty", () => {
    render(
      <>
        <Region label="orb" />
      </>,
    );
    fireEvent.pointerEnter(screen.getByTestId("orb"));
    expect(setInteractive).toHaveBeenLastCalledWith(true);
    fireEvent.pointerLeave(screen.getByTestId("orb"));
    expect(setInteractive).toHaveBeenLastCalledWith(false);
  });

  it("stays interactive while the pointer moves between two surfaces", () => {
    render(
      <>
        <Region label="orb" />
        <Region label="card" />
      </>,
    );
    fireEvent.pointerEnter(screen.getByTestId("orb"));
    fireEvent.pointerEnter(screen.getByTestId("card"));
    // Leaving the orb must not drop input while the card still holds it.
    fireEvent.pointerLeave(screen.getByTestId("orb"));
    expect(setInteractive).toHaveBeenCalledTimes(1);
    expect(setInteractive).toHaveBeenLastCalledWith(true);
    fireEvent.pointerLeave(screen.getByTestId("card"));
    expect(setInteractive).toHaveBeenLastCalledWith(false);
  });
});

describe("TypeToSiriPill", () => {
  it("submits the typed text on Enter and closes", () => {
    const onSubmit = vi.fn();
    const onClose = vi.fn();
    render(<TypeToSiriPill open onSubmit={onSubmit} onClose={onClose} />);
    const input = screen.getByLabelText("Message FRIDAY");
    fireEvent.change(input, { target: { value: "  summarise my screen  " } });
    fireEvent.submit(input.closest("form") as HTMLFormElement);
    expect(onSubmit).toHaveBeenCalledWith("summarise my screen");
    expect(onClose).toHaveBeenCalled();
  });

  it("closes on Escape", () => {
    const onClose = vi.fn();
    render(<TypeToSiriPill open onSubmit={vi.fn()} onClose={onClose} />);
    fireEvent.keyDown(screen.getByLabelText("Message FRIDAY"), { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
  });

  it("renders nothing while closed", () => {
    const { container } = render(
      <TypeToSiriPill open={false} onSubmit={vi.fn()} onClose={vi.fn()} />,
    );
    expect(container.querySelector("form")).toBeNull();
  });
});

describe("DevPreviewPanel", () => {
  it("drives the same state and card messages the socket would", () => {
    const onState = vi.fn();
    const onCard = vi.fn();
    render(
      <DevPreviewPanel
        open
        onOpenChange={vi.fn()}
        onState={onState}
        onCard={onCard}
        onAmplitude={vi.fn()}
        onToggleInspector={vi.fn()}
        onError={vi.fn()}
        onShutdown={vi.fn()}
        onClear={vi.fn()}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "SPEAKING" }));
    expect(onState).toHaveBeenCalledWith("SPEAKING");
    fireEvent.click(screen.getByRole("button", { name: "weather" }));
    expect(onCard).toHaveBeenCalledWith("weather");
  });
});


/* ── interactive glass surfaces ─────────────────────────────────────────── */

function makeCard(uid: string, title = "Card"): CardItem {
  return {
    uid,
    kind: "text",
    title,
    body: "body",
    footer: "footer",
    payload: {},
    severity: "info",
    ts: 1,
    ttl: 0,
    dismissible: true,
  };
}

function makeActivity(aid: string, title: string): ActivityItem {
  return {
    aid,
    title,
    kind: "task",
    detail: "working",
    progress: -1,
    active: true,
    ts: Date.now() / 1000,
  };
}

describe("interactive glass surfaces", () => {
  const setInteractive = vi.fn();

  beforeEach(() => {
    __resetInteractiveRegions();
    setInteractive.mockClear();
    (window as unknown as { fridayAPI: unknown }).fridayAPI = {
      setInteractive,
      setFocusable: vi.fn(),
      setEscapeArmed: vi.fn(),
      setInvoked: vi.fn(),
    };
  });

  afterEach(() => {
    delete (window as unknown as { fridayAPI?: unknown }).fridayAPI;
  });

  it("BaseGlassCard claims input on hover and releases it on leave", () => {
    render(
      <BaseGlassCard card={makeCard("c1")} index={0} onDismiss={vi.fn()}>
        <p>hi</p>
      </BaseGlassCard>,
    );
    const card = screen.getByTestId("card-text");
    fireEvent.pointerEnter(card);
    expect(setInteractive).toHaveBeenLastCalledWith(true);
    fireEvent.pointerLeave(card);
    expect(setInteractive).toHaveBeenLastCalledWith(false);
  });

  it("SnippetStack keeps the window interactive while any card is hovered", () => {
    render(
      <SnippetStack cards={[makeCard("c1"), makeCard("c2")]} onDismiss={vi.fn()} />,
    );
    const cards = screen.getAllByTestId("card-text");
    fireEvent.pointerEnter(cards[0]);
    fireEvent.pointerEnter(cards[1]);
    fireEvent.pointerLeave(cards[0]);
    // One card still holds the region: the window must not drop input.
    expect(setInteractive).toHaveBeenCalledTimes(1);
    expect(setInteractive).toHaveBeenLastCalledWith(true);
    fireEvent.pointerLeave(cards[1]);
    expect(setInteractive).toHaveBeenLastCalledWith(false);
  });

  it("LiveActivityBar expands on hover and cancels a job", () => {
    const onCancel = vi.fn();
    render(
      <LiveActivityBar
        activities={[makeActivity("a", "Indexing files"), makeActivity("b", "Warming models")]}
        activeCount={2}
        onCancel={onCancel}
      />,
    );
    expect(screen.queryByTestId("activity-job-a")).toBeNull();

    const capsule = document.querySelector('[data-expanded="false"]') as HTMLElement;
    fireEvent.pointerEnter(capsule);
    expect(screen.getByTestId("activity-job-a")).toBeInTheDocument();
    expect(screen.getByTestId("activity-job-b")).toBeInTheDocument();
    expect(setInteractive).toHaveBeenLastCalledWith(true);

    fireEvent.click(screen.getByRole("button", { name: "Cancel Indexing files" }));
    expect(onCancel).toHaveBeenCalledWith("a");
  });

  it("LiveActivityBar expands on click too", () => {
    render(
      <LiveActivityBar
        activities={[makeActivity("a", "Indexing files")]}
        activeCount={1}
        onCancel={vi.fn()}
      />,
    );
    fireEvent.click(document.querySelector('[data-expanded="false"]') as HTMLElement);
    expect(screen.getByTestId("activity-job-a")).toBeInTheDocument();
  });

  it("LiveTranscript morphs streaming words without remounting", () => {
    const { rerender } = render(<LiveTranscript text="hello" speaking />);
    const first = screen.getByText("hello");
    rerender(<LiveTranscript text="hello there" speaking />);
    const second = screen.getByText("hello there");
    // Same DOM node: the pill re-renders, it never unmounts mid-utterance.
    expect(second).toBe(first);
    expect(second.className).toContain("line-clamp-3");
    expect(screen.getByTestId("transcript-pulse")).toHaveAttribute("data-active", "true");
  });

  it("renders three centred lines at most", () => {
    const long = "word ".repeat(120).trim();
    render(<LiveTranscript text={long} />);
    const paragraph = screen.getByText(long);
    expect(paragraph.className).toContain("line-clamp-3");
    expect(paragraph.className).toContain("break-words");
  });
});
