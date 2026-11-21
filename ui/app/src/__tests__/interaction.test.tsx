import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DevPreviewPanel } from "../components/DevPreviewPanel";
import { TypeToSiriPill } from "../components/TypeToSiriPill";
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
