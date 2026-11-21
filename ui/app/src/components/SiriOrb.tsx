import { motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useInteractiveRegion } from "../hooks/useInteractiveRegion";
import type { AssistantState } from "../types/protocol";
import fragmentShaderSource from "../shaders/siriOrb.frag.glsl?raw";

/** Canvas is 204×204 CSS px; the 92 px sphere is drawn by the shader. */
export const ORB_CANVAS_SIZE = 204;

const VERTEX_SHADER = `#version 300 es
precision highp float;
const vec2 kPositions[3] = vec2[3](vec2(-1.0, -1.0), vec2(3.0, -1.0), vec2(-1.0, 3.0));
void main() {
  gl_Position = vec4(kPositions[gl_VertexID], 0.0, 1.0);
}`;

const STATE_CODE: Record<AssistantState, number> = {
  BOOTING: 0,
  LISTENING: 0,
  THINKING: 1,
  PROCESSING: 2,
  SPEAKING: 3,
  MUTED: 4,
};

export interface SiriOrbProps {
  invoked: boolean;
  state: AssistantState;
  energy: number;
  amplitude: number;
  muted: boolean;
  attention: boolean;
  /** Increment to fire the 900 ms rose chromatic slip. */
  errorSignal: number;
  reducedMotion: boolean;
  onActivate: () => void;
  /** Called once the dismissal contraction has finished. */
  onDismissed: () => void;
  onInteraction?: (inside: boolean) => void;
}

interface Uniforms {
  uResolution: WebGLUniformLocation | null;
  uTime: WebGLUniformLocation | null;
  uVoice: WebGLUniformLocation | null;
  uEnergy: WebGLUniformLocation | null;
  uState: WebGLUniformLocation | null;
  uMuted: WebGLUniformLocation | null;
  uAttention: WebGLUniformLocation | null;
  uError: WebGLUniformLocation | null;
  uFlash: WebGLUniformLocation | null;
  uReducedMotion: WebGLUniformLocation | null;
}

function compile(gl: WebGL2RenderingContext, type: number, source: string): WebGLShader | null {
  const shader = gl.createShader(type);
  if (!shader) return null;
  gl.shaderSource(shader, source);
  gl.compileShader(shader);
  if (!gl.getShaderParameter(shader, gl.COMPILE_STATUS)) {
    // A shader failure must never blank the whole overlay silently.
    console.error("[SiriOrb] shader compile failed:", gl.getShaderInfoLog(shader));
    gl.deleteShader(shader);
    return null;
  }
  return shader;
}

function buildProgram(gl: WebGL2RenderingContext): WebGLProgram | null {
  const vertex = compile(gl, gl.VERTEX_SHADER, VERTEX_SHADER);
  const fragment = compile(gl, gl.FRAGMENT_SHADER, fragmentShaderSource);
  if (!vertex || !fragment) return null;
  const program = gl.createProgram();
  if (!program) return null;
  gl.attachShader(program, vertex);
  gl.attachShader(program, fragment);
  gl.linkProgram(program);
  gl.deleteShader(vertex);
  gl.deleteShader(fragment);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS)) {
    console.error("[SiriOrb] program link failed:", gl.getProgramInfoLog(program));
    gl.deleteProgram(program);
    return null;
  }
  return program;
}

/**
 * 1:1 volumetric Siri orb.
 *
 * The render loop is *owned by `invoked`*: when the orb is dormant the
 * `requestAnimationFrame` loop is not scheduled at all, so a sleeping FRIDAY
 * costs 0 % CPU and 0 % GPU and leaves nothing on the desktop.
 */
export function SiriOrb({
  invoked,
  state,
  energy,
  amplitude,
  muted,
  attention,
  errorSignal,
  reducedMotion,
  onActivate,
  onDismissed,
  onInteraction,
}: SiriOrbProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const glRef = useRef<WebGL2RenderingContext | null>(null);
  const programRef = useRef<WebGLProgram | null>(null);
  const uniformsRef = useRef<Uniforms | null>(null);
  const frameRef = useRef<number | null>(null);
  const [supported, setSupported] = useState(true);
  const [dismissed, setDismissed] = useState(false);
  // Bumped when a lost WebGL context is restored, to re-arm the loop.
  const [, setContextEpoch] = useState(0);

  const invokedRef = useRef(invoked);
  invokedRef.current = invoked;

  const propsRef = useRef({
    state, energy, amplitude, muted, attention, reducedMotion,
  });
  propsRef.current = { state, energy, amplitude, muted, attention, reducedMotion };

  const smoothed = useRef({
    voice: 0,
    voiceVelocity: 0,
    energy: 0.24,
    state: 0,
    muted: 0,
    attention: 0,
    error: 0,
    flash: 0,
    time: 0,
  });

  // No pinning: the stage stays click-through while the orb is *idle*, and the
  // disc claims input on hover (Electron forwards mouse moves while ignoring
  // clicks). Pinning for the whole summon would swallow desktop clicks.
  const region = useInteractiveRegion({
    onChange: (inside) => onInteraction?.(inside),
  });

  // ── WebGL lifecycle (context loss included) ───────────────────────────────
  const initGL = useCallback(() => {
    const canvas = canvasRef.current;
    if (!canvas) return false;
    const dpr = Math.min(window.devicePixelRatio || 1, 3);
    canvas.width = Math.round(ORB_CANVAS_SIZE * dpr);
    canvas.height = Math.round(ORB_CANVAS_SIZE * dpr);

    const gl = canvas.getContext("webgl2", {
      alpha: true,
      antialias: true,
      premultipliedAlpha: true,
      powerPreference: "high-performance",
      desynchronized: true,
      preserveDrawingBuffer: false,
    });
    if (!gl) {
      setSupported(false);
      return false;
    }
    glRef.current = gl;
    gl.viewport(0, 0, canvas.width, canvas.height);
    gl.disable(gl.DEPTH_TEST);
    gl.disable(gl.CULL_FACE);
    gl.enable(gl.BLEND);
    gl.blendFunc(gl.ONE, gl.ONE_MINUS_SRC_ALPHA);
    gl.clearColor(0, 0, 0, 0);

    const program = buildProgram(gl);
    if (!program) {
      setSupported(false);
      return false;
    }
    programRef.current = program;
    gl.useProgram(program);
    uniformsRef.current = {
      uResolution: gl.getUniformLocation(program, "uResolution"),
      uTime: gl.getUniformLocation(program, "uTime"),
      uVoice: gl.getUniformLocation(program, "uVoice"),
      uEnergy: gl.getUniformLocation(program, "uEnergy"),
      uState: gl.getUniformLocation(program, "uState"),
      uMuted: gl.getUniformLocation(program, "uMuted"),
      uAttention: gl.getUniformLocation(program, "uAttention"),
      uError: gl.getUniformLocation(program, "uError"),
      uFlash: gl.getUniformLocation(program, "uFlash"),
      uReducedMotion: gl.getUniformLocation(program, "uReducedMotion"),
    };
    gl.uniform2f(uniformsRef.current.uResolution, canvas.width, canvas.height);
    setSupported(true);
    return true;
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const created = initGL();

    const onLost = (event: Event) => {
      event.preventDefault();
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
    };
    const onRestored = () => {
      initGL();
      // The loop is re-armed by the `invoked` effect on the next render; kick
      // it manually so a restored context paints immediately.
      setContextEpoch((value) => value + 1);
    };
    canvas.addEventListener("webglcontextlost", onLost);
    canvas.addEventListener("webglcontextrestored", onRestored);
    return () => {
      canvas.removeEventListener("webglcontextlost", onLost);
      canvas.removeEventListener("webglcontextrestored", onRestored);
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
      const gl = glRef.current;
      if (created && gl) {
        const program = programRef.current;
        if (program) gl.deleteProgram(program);
        gl.getExtension("WEBGL_lose_context")?.loseContext();
      }
      programRef.current = null;
      uniformsRef.current = null;
      glRef.current = null;
    };
    // initGL is stable; this must run exactly once per mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initGL]);

  // ── summon flash + error slip triggers ───────────────────────────────────
  const lastInvoked = useRef(invoked);
  useEffect(() => {
    if (invoked && !lastInvoked.current) {
      smoothed.current.flash = reducedMotion ? 0 : 1;
      smoothed.current.time = 0;
    }
    lastInvoked.current = invoked;
    if (!invoked) setDismissed(false);
  }, [invoked, reducedMotion]);

  const errorSeen = useRef(errorSignal);
  useEffect(() => {
    if (errorSignal > errorSeen.current) {
      smoothed.current.error = reducedMotion ? 0.45 : 1;
    }
    errorSeen.current = errorSignal;
  }, [errorSignal, reducedMotion]);

  // ── the render loop (only while invoked) ─────────────────────────────────
  useEffect(() => {
    if (!invoked) {
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      return;
    }

    let last = performance.now();
    const tick = (now: number) => {
      // A frame that was already queued when the orb was dismissed must never
      // paint: the loop is the *only* thing keeping the GPU awake.
      if (!invokedRef.current) {
        frameRef.current = null;
        return;
      }
      const gl = glRef.current;
      const program = programRef.current;
      const uniforms = uniformsRef.current;
      const canvas = canvasRef.current;
      if (!gl || !program || !uniforms || !canvas) {
        frameRef.current = requestAnimationFrame(tick);
        return;
      }
      const dt = Math.min((now - last) / 1000, 0.05);
      last = now;
      const props = propsRef.current;
      const s = smoothed.current;

      // Exponential smoothing for scalars (frame-rate independent).
      const approach = (current: number, target: number, tau: number) =>
        current + (target - current) * (1 - Math.exp(-dt / tau));

      // Critically damped spring for the voice level (stiffness 180,
      // damping 26.8 ⇒ ωn≈13.4, ζ≈1). No overshoot: the ribbons must never
      // snap past the input and look jittery on plosives.
      const omega = 13.4164;
      const damping = 26.8328;
      const targetVoice = Math.max(0, Math.min(1, props.amplitude));
      s.voiceVelocity += (omega * omega * (targetVoice - s.voice) - 2 * damping * s.voiceVelocity) * dt;
      s.voice += s.voiceVelocity * dt;

      s.energy = approach(s.energy, Math.max(0, Math.min(1, props.energy)), 0.18);
      s.state = approach(s.state, STATE_CODE[props.state] ?? 0, 0.22);
      s.muted = approach(s.muted, props.muted ? 1 : 0, 0.20);
      s.attention = approach(s.attention, props.attention ? 1 : 0, 0.25);
      s.error = Math.max(0, s.error - dt / 0.9);
      s.flash = Math.max(0, s.flash - dt / 1.1);
      s.time += dt * (props.reducedMotion ? 0.22 : 1);

      gl.useProgram(program);
      gl.uniform1f(uniforms.uTime, s.time);
      gl.uniform1f(uniforms.uVoice, s.voice);
      gl.uniform1f(uniforms.uEnergy, s.energy);
      gl.uniform1f(uniforms.uState, s.state);
      gl.uniform1f(uniforms.uMuted, s.muted);
      gl.uniform1f(uniforms.uAttention, s.attention);
      gl.uniform1f(uniforms.uError, s.error);
      gl.uniform1f(uniforms.uFlash, s.flash);
      gl.uniform1f(uniforms.uReducedMotion, props.reducedMotion ? 1 : 0);

      gl.clear(gl.COLOR_BUFFER_BIT);
      gl.drawArrays(gl.TRIANGLES, 0, 3);

      frameRef.current = requestAnimationFrame(tick);
    };

    frameRef.current = requestAnimationFrame(tick);
    return () => {
      if (frameRef.current !== null) cancelAnimationFrame(frameRef.current);
      frameRef.current = null;
    };
  }, [invoked]);

  const transition = useMemo(
    () =>
      reducedMotion
        ? { duration: 0.18, ease: "easeOut" as const }
        : { type: "spring" as const, stiffness: 320, damping: 18, mass: 0.9 },
    [reducedMotion],
  );

  // `onDismissed` must fire exactly once per summon — never on the initial
  // dormant mount, or Python would be told the window vanished before it was
  // ever shown.
  const wasInvoked = useRef(false);
  useEffect(() => {
    if (invoked) wasInvoked.current = true;
  }, [invoked]);

  const handleAnimationComplete = useCallback(() => {
    if (!invoked && wasInvoked.current) {
      wasInvoked.current = false;
      setDismissed(true);
      onDismissed();
    }
  }, [invoked, onDismissed]);

  return (
    <motion.div
      className="relative select-none"
      style={{ width: ORB_CANVAS_SIZE, height: ORB_CANVAS_SIZE }}
      initial={{ scale: 0, opacity: 0 }}
      animate={
        invoked
          ? { scale: 1, opacity: 1 }
          : { scale: 0.3, opacity: 0 }
      }
      transition={invoked ? transition : { duration: reducedMotion ? 0.06 : 0.36, ease: "easeIn" }}
      onAnimationComplete={handleAnimationComplete}
      aria-hidden={!invoked}
    >
      {/* The interactive disc is a circle, matching the sphere's footprint. */}
      <div
        {...region.regionProps}
        className={`${region.regionProps.className} absolute left-1/2 top-1/2 rounded-full`}
        style={{
          width: 118,
          height: 118,
          transform: "translate(-50%, -50%)",
          // Non-zero alpha: Windows drops clicks on fully transparent pixels.
          backgroundColor: "rgba(0, 0, 0, 0.01)",
        }}
        role="button"
        tabIndex={-1}
        aria-label="FRIDAY — click to type"
        title="Click to type to FRIDAY"
        onClick={() => {
          if (!dismissed) onActivate();
        }}
      />
      {supported ? (
        <canvas
          ref={canvasRef}
          className="pointer-events-none absolute inset-0"
          style={{ width: ORB_CANVAS_SIZE, height: ORB_CANVAS_SIZE }}
          data-testid="siri-orb-canvas"
        />
      ) : (
        // No WebGL2 (rare: remote sessions, broken drivers). A soft radial
        // gradient keeps the identity instead of showing an empty hole.
        <div
          className="pointer-events-none absolute inset-0"
          data-testid="siri-orb-fallback"
          style={{
            background:
              "radial-gradient(circle at 50% 48%, rgba(6,247,255,0.85) 0%, rgba(30,95,255,0.65) 26%, rgba(176,48,255,0.45) 42%, rgba(255,45,159,0.18) 55%, rgba(0,0,0,0) 72%)",
            filter: "blur(0.5px)",
            opacity: muted ? 0.5 : 1,
          }}
        />
      )}
    </motion.div>
  );
}

export default SiriOrb;
