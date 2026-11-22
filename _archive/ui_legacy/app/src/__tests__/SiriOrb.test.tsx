import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SiriOrb } from "../components/SiriOrb";
import fragmentSource from "../shaders/siriOrb.frag.glsl?raw";

/** A WebGL2 context just rich enough to compile and drive the orb's program. */
function fakeGl() {
  const shaderSources: string[] = [];
  return {
    shaderSources,
    VERTEX_SHADER: 1,
    FRAGMENT_SHADER: 2,
    COMPILE_STATUS: 3,
    LINK_STATUS: 4,
    DEPTH_TEST: 5,
    CULL_FACE: 6,
    BLEND: 7,
    ONE: 8,
    ONE_MINUS_SRC_ALPHA: 9,
    COLOR_BUFFER_BIT: 10,
    TRIANGLES: 11,
    ARRAY_BUFFER: 12,
    createShader: vi.fn(() => ({})),
    shaderSource: vi.fn((_shader: unknown, source: string) => {
      shaderSources.push(source);
    }),
    compileShader: vi.fn(),
    getShaderParameter: vi.fn(() => true),
    getShaderInfoLog: vi.fn(() => ""),
    deleteShader: vi.fn(),
    createProgram: vi.fn(() => ({})),
    attachShader: vi.fn(),
    linkProgram: vi.fn(),
    getProgramParameter: vi.fn(() => true),
    getProgramInfoLog: vi.fn(() => ""),
    deleteProgram: vi.fn(),
    useProgram: vi.fn(),
    // Locations are the uniform *names*, so assertions can attribute values.
    getUniformLocation: vi.fn((_program: unknown, name: string) => name),
    uniform1f: vi.fn(),
    uniform2f: vi.fn(),
    viewport: vi.fn(),
    enable: vi.fn(),
    disable: vi.fn(),
    blendFunc: vi.fn(),
    clearColor: vi.fn(),
    clear: vi.fn(),
    drawArrays: vi.fn(),
    getExtension: vi.fn(() => null),
  };
}

let gl: ReturnType<typeof fakeGl>;
let frames: FrameRequestCallback[];
let now = 0;

beforeEach(() => {
  gl = fakeGl();
  frames = [];
  now = 1000;
  vi.spyOn(performance, "now").mockImplementation(() => now);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockImplementation(((
    type: string,
  ) => (type === "webgl2" ? (gl as unknown as WebGL2RenderingContext) : null)) as never);
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
    frames.push(cb);
    return frames.length;
  });
  vi.stubGlobal("cancelAnimationFrame", () => undefined);
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

/** Advance one animation frame (16 ms of wall clock, so springs integrate). */
function flushFrame(steps = 1) {
  for (let i = 0; i < steps; i++) {
    const pending = frames;
    frames = [];
    now += 16;
    act(() => {
      for (const cb of pending) cb(now);
    });
  }
}

function lastUniform(name: string): number | undefined {
  const calls = gl.uniform1f.mock.calls.filter((call) => call[0] === name);
  return calls.length ? (calls[calls.length - 1][1] as number) : undefined;
}

const baseProps = {
  state: "LISTENING" as const,
  energy: 0.4,
  amplitude: 0.2,
  muted: false,
  attention: false,
  errorSignal: 0,
  reducedMotion: false,
  onActivate: () => undefined,
  onDismissed: () => undefined,
};

describe("SiriOrb", () => {
  it("requests a premultiplied-alpha WebGL2 context", () => {
    render(<SiriOrb {...baseProps} invoked />);
    const canvas = screen.getByTestId("siri-orb-canvas") as HTMLCanvasElement;
    expect(canvas).toBeInTheDocument();
    // 204 CSS px at dpr 1 in jsdom.
    expect(canvas.width).toBe(204);
    expect(canvas.height).toBe(204);
    const options = (HTMLCanvasElement.prototype.getContext as unknown as {
      mock: { calls: Array<[string, Record<string, unknown>]> };
    }).mock.calls.find((call) => call[0] === "webgl2")?.[1];
    expect(options).toMatchObject({ premultipliedAlpha: true, alpha: true });
  });

  it("compiles the orb's GLSL 300 es fragment shader", () => {
    render(<SiriOrb {...baseProps} invoked />);
    expect(gl.shaderSources.length).toBe(2);
    const fragment = gl.shaderSources[1];
    expect(fragment).toBe(fragmentSource);
    expect(fragment).toContain("#version 300 es");
    expect(fragment).toContain("precision highp float;");
    expect(fragment).toContain("uniform float uVoice;");
    expect(fragment).toContain("uniform float uEnergy;");
    expect(fragment).toContain("uniform float uTime;");
    expect(fragment).toContain("uniform float uActing;");
    // The Apple 3D-silk optics the v1 stripe shader was missing.
    expect(fragment).toContain("0.62 + 0.38 * z");
    expect(fragment).toContain("exp(-d * d * sharpen)");
    expect(fragment).toContain("0.85 * body + 0.45 * filament");
    expect(fragment).toContain("pow(z, 0.80)");
    expect(fragment).toContain("mix(1.0, 0.38, wMuted)");
    expect(fragment).toContain("vec4(color * alpha, alpha)");
  });

  it("draws a frame while summoned", () => {
    render(<SiriOrb {...baseProps} invoked />);
    flushFrame();
    expect(gl.drawArrays).toHaveBeenCalled();
  });

  it("never schedules a frame while dormant before the first summon", () => {
    render(<SiriOrb {...baseProps} invoked={false} />);
    flushFrame(4);
    expect(gl.drawArrays).not.toHaveBeenCalled();
  });

  it("keeps the RAF loop painting through dismissal, then stops for good", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const onDismissed = vi.fn();
    const { rerender } = render(
      <SiriOrb {...baseProps} invoked onDismissed={onDismissed} />,
    );
    flushFrame(2);
    const drawn = gl.drawArrays.mock.calls.length;

    rerender(<SiriOrb {...baseProps} invoked={false} onDismissed={onDismissed} />);
    flushFrame(3);
    // The 360 ms contraction must animate, not freeze on frame 1.
    expect(gl.drawArrays.mock.calls.length).toBeGreaterThan(drawn);
    expect(onDismissed).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(700);
    });
    expect(onDismissed).toHaveBeenCalledTimes(1);

    const settled = gl.drawArrays.mock.calls.length;
    flushFrame(4);
    // Dormant again: 0 % CPU / 0 % GPU.
    expect(gl.drawArrays.mock.calls.length).toBe(settled);
  });

  it("never reports a dismissal on the initial dormant mount", () => {
    vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
    const onDismissed = vi.fn();
    render(<SiriOrb {...baseProps} invoked={false} onDismissed={onDismissed} />);
    act(() => {
      vi.advanceTimersByTime(700);
    });
    expect(onDismissed).not.toHaveBeenCalled();
  });

  it("synthesizes an organic speech cadence while SPEAKING", () => {
    render(<SiriOrb {...baseProps} state="SPEAKING" amplitude={0} invoked />);
    flushFrame(14);
    // main.py mutes the mic during TTS; the orb must still move.
    expect(lastUniform("uVoice")).toBeGreaterThan(0.2);
  });

  it("stays still in LISTENING with no microphone input", () => {
    render(<SiriOrb {...baseProps} state="LISTENING" amplitude={0} invoked />);
    flushFrame(14);
    expect(lastUniform("uVoice")).toBeLessThan(0.02);
  });

  it("injects a syllabic impulse when FRIDAY's words grow", () => {
    const first = render(<SiriOrb {...baseProps} state="SPEAKING" amplitude={0} invoked />);
    flushFrame(6);
    const quietVoice = lastUniform("uVoice") ?? 0;
    first.unmount();

    const second = render(
      <SiriOrb {...baseProps} state="SPEAKING" amplitude={0} invoked speechPulse={0} />,
    );
    flushFrame(5);
    const beforePulse = lastUniform("uVoice") ?? 0;
    second.rerender(
      <SiriOrb {...baseProps} state="SPEAKING" amplitude={0} invoked speechPulse={1} />,
    );
    flushFrame(2);
    const afterPulse = lastUniform("uVoice") ?? 0;

    expect(beforePulse).toBeGreaterThan(0.05);
    // Same clock, one extra syllable: strictly ahead of the cadence-only run.
    expect(afterPulse).toBeGreaterThan(quietVoice);
  });

  it("uploads uActing while background work is running", () => {
    render(<SiriOrb {...baseProps} invoked acting />);
    flushFrame(14);
    expect(lastUniform("uActing") ?? 0).toBeGreaterThan(0.4);
  });

  it("rebuilds the backing buffer when devicePixelRatio changes", () => {
    Object.defineProperty(window, "devicePixelRatio", { value: 2, configurable: true });
    render(<SiriOrb {...baseProps} invoked />);
    const canvas = screen.getByTestId("siri-orb-canvas") as HTMLCanvasElement;
    expect(canvas.width).toBe(408);

    act(() => {
      Object.defineProperty(window, "devicePixelRatio", { value: 1, configurable: true });
      window.dispatchEvent(new Event("resize"));
    });
    expect(canvas.width).toBe(204);
  });

  it("falls back to a gradient when WebGL2 is unavailable", () => {
    (HTMLCanvasElement.prototype.getContext as unknown as { mockImplementation: (fn: unknown) => void }).mockImplementation(
      () => null,
    );
    render(<SiriOrb {...baseProps} invoked />);
    expect(screen.getByTestId("siri-orb-fallback")).toBeInTheDocument();
  });

  it("still runs with reduced motion enabled", () => {
    render(<SiriOrb {...baseProps} invoked reducedMotion />);
    flushFrame();
    expect(gl.drawArrays).toHaveBeenCalled();
  });
});
