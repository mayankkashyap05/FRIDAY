import { act, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SiriOrb } from "../components/SiriOrb";
import fragmentSource from "../shaders/siriOrb.frag.glsl?raw";

/** A WebGL2 context just rich enough to compile the orb's program. */
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
    getUniformLocation: vi.fn(() => ({})),
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

beforeEach(() => {
  gl = fakeGl();
  frames = [];
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
});

function flushFrame() {
  const pending = frames;
  frames = [];
  act(() => {
    for (const cb of pending) cb(performance.now());
  });
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
    expect(fragment).toContain("vec4(color * alpha, alpha)");
  });

  it("draws a frame while summoned", () => {
    render(<SiriOrb {...baseProps} invoked />);
    flushFrame();
    expect(gl.drawArrays).toHaveBeenCalled();
  });

  it("stops the animation loop when dormant (0 % CPU while asleep)", () => {
    const { rerender } = render(<SiriOrb {...baseProps} invoked />);
    flushFrame();
    const drawn = gl.drawArrays.mock.calls.length;
    rerender(<SiriOrb {...baseProps} invoked={false} />);
    flushFrame();
    expect(gl.drawArrays.mock.calls.length).toBe(drawn);
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
