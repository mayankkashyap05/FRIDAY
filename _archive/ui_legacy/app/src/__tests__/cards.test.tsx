import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { SnippetStack } from "../components/SnippetStack";
import { CalendarCard } from "../components/cards/CalendarCard";
import { ConfirmationCard } from "../components/cards/ConfirmationCard";
import { ControlPillCard } from "../components/cards/ControlPillCard";
import { DocumentQACard } from "../components/cards/DocumentQACard";
import { EmailDigestCard } from "../components/cards/EmailDigestCard";
import { FlightCard } from "../components/cards/FlightCard";
import { TelemetryCard } from "../components/cards/TelemetryCard";
import { TextCard } from "../components/cards/TextCard";
import { WeatherBriefingCard } from "../components/cards/WeatherBriefingCard";
import type { CardItem, CardKind } from "../types/protocol";

function makeCard(kind: CardKind, payload: Record<string, unknown> = {}, body = ""): CardItem {
  return {
    uid: `uid-${kind}`,
    kind,
    title: `${kind} title`,
    body,
    footer: "footer",
    payload,
    severity: "info",
    ts: 1,
    ttl: 0,
    dismissible: true,
  };
}

const noop = () => undefined;

describe("typed snippet cards", () => {
  it("renders weather payloads with the temperature and chips", () => {
    render(
      <WeatherBriefingCard
        card={makeCard("weather", { city: "Dibrugarh", temp_c: 27, humidity: 81, condition: "rain" })}
        index={0}
        onDismiss={noop}
      />,
    );
    expect(screen.getByText("27")).toBeInTheDocument();
    expect(screen.getByText("Dibrugarh")).toBeInTheDocument();
    expect(screen.getByText(/81% humidity/)).toBeInTheDocument();
  });

  it("renders calendar events in time order", () => {
    render(
      <CalendarCard
        card={makeCard("calendar", { events: [{ time: "09:00", title: "Standup" }] })}
        index={0}
        onDismiss={noop}
      />,
    );
    expect(screen.getByText("Standup")).toBeInTheDocument();
    expect(screen.getByText("09:00")).toBeInTheDocument();
    expect(screen.getByText("1 event")).toBeInTheDocument();
  });

  it("renders the email digest with an unread count", () => {
    render(
      <EmailDigestCard
        card={makeCard("email", { unread: 3, emails: [{ from: "Priya", subject: "Contract" }] })}
        index={0}
        onDismiss={noop}
      />,
    );
    expect(screen.getByText("3 unread")).toBeInTheDocument();
    expect(screen.getByText("Contract")).toBeInTheDocument();
  });

  it("renders a flight route and gate", () => {
    render(
      <FlightCard
        card={makeCard("flight", { code: "AI101", from: "DEL", to: "DXB", gate: "14A" })}
        index={0}
        onDismiss={noop}
      />,
    );
    expect(screen.getByText("DEL")).toBeInTheDocument();
    expect(screen.getByText("DXB")).toBeInTheDocument();
    expect(screen.getByText("AI101")).toBeInTheDocument();
    expect(screen.getByText("gate 14A")).toBeInTheDocument();
  });

  it("renders telemetry bars and clamps to 0..100", () => {
    render(
      <TelemetryCard card={makeCard("telemetry", { cpu: 42, ram: 140 })} index={0} onDismiss={noop} />,
    );
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.getByText("100%")).toBeInTheDocument();
  });

  it("renders a control value with its unit", () => {
    render(
      <ControlPillCard
        card={makeCard("control", { label: "Volume", value: 60, unit: "%" })}
        index={0}
        onDismiss={noop}
      />,
    );
    expect(screen.getByText("Volume")).toBeInTheDocument();
    expect(screen.getByText("60%")).toBeInTheDocument();
  });

  it("renders long documents collapsed with an excerpt", () => {
    const long = "Clause 7 ".repeat(60);
    render(
      <DocumentQACard card={makeCard("document", { pages: 12, excerpt: long })} index={0} onDismiss={noop} />,
    );
    expect(screen.getByText("12p")).toBeInTheDocument();
    expect(screen.getByText("Show full answer")).toBeInTheDocument();
  });

  it("sends the exact consent phrases from a confirmation card", () => {
    const onReply = vi.fn();
    render(
      <ConfirmationCard
        card={makeCard("confirmation", {}, "Delete 42 files?")}
        index={0}
        onDismiss={noop}
        onReply={onReply}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    expect(onReply).toHaveBeenCalledWith("yes, I approve");
  });

  it("sends the exact denial phrase", () => {
    const onReply = vi.fn();
    render(
      <ConfirmationCard
        card={makeCard("confirmation", {}, "Delete 42 files?")}
        index={0}
        onDismiss={noop}
        onReply={onReply}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(onReply).toHaveBeenCalledWith("no, cancel that");
  });

  it("locks both consent buttons after the first answer (double-click guard)", () => {
    const onReply = vi.fn();
    render(
      <ConfirmationCard
        card={makeCard("confirmation", {}, "Delete 42 files?")}
        index={0}
        onDismiss={noop}
        onReply={onReply}
      />,
    );
    const approve = screen.getByRole("button", { name: "Approve" });
    fireEvent.click(approve);
    fireEvent.click(approve);
    fireEvent.click(screen.getByRole("button", { name: "Deny" }));
    expect(onReply).toHaveBeenCalledTimes(1);
    expect(onReply).toHaveBeenCalledWith("yes, I approve");
  });

  it("emits real control commands from the volume steppers", () => {
    const onReply = vi.fn();
    render(
      <ControlPillCard
        card={makeCard("control", { label: "Volume", value: 60, unit: "%" })}
        index={0}
        onDismiss={noop}
        onReply={onReply}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Increase Volume" }));
    expect(onReply).toHaveBeenLastCalledWith("set volume to 65%");
    fireEvent.click(screen.getByRole("button", { name: "Decrease Volume" }));
    expect(onReply).toHaveBeenLastCalledWith("set volume to 55%");
  });

  it("keeps the steppers inert without a reply channel", () => {
    render(
      <ControlPillCard
        card={makeCard("control", { label: "Brightness", value: 40 })}
        index={0}
        onDismiss={noop}
      />,
    );
    expect(screen.getByRole("button", { name: "Increase Brightness" })).toBeDisabled();
  });

  it("expands a long document answer on demand", () => {
    render(
      <DocumentQACard
        card={makeCard("document", { pages: 12, excerpt: "Clause 7 ".repeat(60) })}
        index={0}
        onDismiss={noop}
      />,
    );
    const toggle = screen.getByRole("button", { name: "Show full answer" });
    fireEvent.click(toggle);
    expect(screen.getByRole("button", { name: "Show less" })).toBeInTheDocument();
  });

  it("shows an hourly strip when the weather payload has hours", () => {
    render(
      <WeatherBriefingCard
        card={makeCard("weather", {
          city: "Dibrugarh",
          temp_c: 27,
          condition: "rain",
          hours: [{ time: "18:00", temp_c: 26 }, { time: "21:00", temp_c: 24 }],
        })}
        index={0}
        onDismiss={noop}
      />,
    );
    expect(screen.getByText("18:00")).toBeInTheDocument();
    expect(screen.getByText("26°")).toBeInTheDocument();
  });

  it("renders plain prose for text cards", () => {
    render(<TextCard card={makeCard("text", {}, "hello world")} index={0} onDismiss={noop} />);
    expect(screen.getByText("hello world")).toBeInTheDocument();
  });

  it("dismisses on the close button and on right-click", () => {
    const onDismiss = vi.fn();
    const { container } = render(
      <TextCard card={makeCard("text", {}, "body")} index={0} onDismiss={onDismiss} />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Dismiss card" }));
    expect(onDismiss).toHaveBeenCalledWith("uid-text");
    const article = container.querySelector("article");
    expect(article).not.toBeNull();
    fireEvent.contextMenu(article as HTMLElement);
    expect(onDismiss).toHaveBeenCalledTimes(2);
  });
});

describe("SnippetStack", () => {
  it("shows at most three cards, newest nearest the orb", () => {
    const cards = Array.from({ length: 5 }, (_, i) => ({
      ...makeCard("text", {}, `body ${i}`),
      uid: `u${i}`,
      title: `card ${i}`,
    }));
    const { container } = render(
      <SnippetStack cards={cards} onDismiss={noop} />,
    );
    const rendered = container.querySelectorAll("article");
    expect(rendered).toHaveLength(3);
    expect(rendered[0].getAttribute("data-uid")).toBe("u0");
  });

  it("renders nothing when the stack is empty", () => {
    const { container } = render(<SnippetStack cards={[]} onDismiss={noop} />);
    expect(container.querySelectorAll("article")).toHaveLength(0);
  });
});
