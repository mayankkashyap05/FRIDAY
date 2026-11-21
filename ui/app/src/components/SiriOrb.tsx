import { motion } from "motion/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useInteractiveRegion } from "../hooks/useInteractiveRegion";
import type { AssistantState } from "../types/protocol";
import fragmentShaderSource from "../shaders/siriOrb.frag.glsl?raw";

/** Canvas is 204×204 CSS px; the 92 px sphere is drawn by the shader. */
export const ORB_CANVAS_SIZE = 204;
/** Diameter of the circular hit disc that sits on the sphere. */
export const ORB_HIT_SIZE = 118;

/** The 360 ms dismissal contraction (Motion tween) plus a little slack. */
const DISMISS_CONTRACTION_MS = 480;
const DISMISS_CONTRACTION_REDUCED_MS = 140;

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

const TAU = Math.PI * 2;

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
  /** True while background activities run (drives `uActing`). */
  acting?: boolean;
  /** Bumped whenever FRIDAY's streaming words grow during SPEAKING. */
  speechPulse?: number;
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
  uActing: WebGLUniformLocation | null;
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

const clamp01 = (value: number) => Math.max(0, Math.min(1, value));

/**
 * 1:1 volumetric Siri orb.
 *
 * The render loop is owned by `invoked || contracting`: a dormant orb schedules
 * no `requestAnimationFrame` at all (0 % CPU / 0 % GPU), but the 360 ms
 * dismissal contraction keeps painting so the orb contracts smoothly instead of
 * freezing mid-shrink. Once the contraction completes, the loop is cancelled and
 * `onDismissed()` fires.
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
  acting = false,
  speechPulse = 0,
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
  const [contracting, setContracting] = useState(false);
  const [hovered, setHovered] = useState(false);
  const [pressed, setPressed] = useState(false);
  // Bumped when a lost WebGL context is restored, to re-arm the loop.
  const [, setContextEpoch] = useState(0);

  const invokedRef = useRef(invoked);
  invokedRef.current = invoked;
  const contractingRef = useRef(contracting);
  contractingRef.current = contracting;

  const propsRef = useRef({
    state, energy, amplitude, muted, attention, reducedMotion, acting,
  });
  propsRef.current = { state, energy, amplitude, muted, attention, reducedMotion, acting };

  const smoothed = useRef({
    voice: 0,
    voiceVelocity: 0,
    energy: 0.24,
    state: 0,
    muted: 0,
    attention: 0,
    error: 0,
    flash: 0,
    acting: 0,
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
      uActing: gl.getUniformLocation(program, "uActing"),
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

  // ── DPI / monitor changes ─────────────────────────────────────────────────
  // Moving the overlay between a 100 % and a 150 % display changes
  // devicePixelRatio under a *running* canvas; the backing buffer must follow,
  // or the sphere is resampled and soft on the new monitor.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return undefined;

    const resize = () => {
      const dpr = Math.min(window.devicePixelRatio || 1, 3);
      const width = Math.round(ORB_CANVAS_SIZE * dpr);
      if (canvas.width === width && canvas.height === width) return;
      canvas.width = width;
      canvas.height = width;
      const gl = glRef.current;
      const uniforms = uniformsRef.current;
      if (gl && uniforms) {
        gl.viewport(0, 0, width, width);
        gl.uniform2f(uniforms.uResolution, width, width);
      }
    };

    let query: MediaQueryList | null = null;
    let onDprChange: (() => void) | null = null;
    const watchDpr = () => {
      if (typeof window.matchMedia !== "function") return;
      if (query && onDprChange) query.removeEventListener?.("change", onDprChange);
      query = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
      onDprChange = () => {
        resize();
        watchDpr();
      };
      query.addEventListener?.("change", onDprChange);
    };

    resize();
    watchDpr();
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      if (query && onDprChange) query.removeEventListener?.("change", onDprChange);
    };
  }, []);

  // ── summon flash + error slip triggers ───────────────────────────────────
  const lastInvoked = useRef(invoked);
  useEffect(() => {
    if (invoked && !lastInvoked.current) {
      smoothed.current.flash = reducedMotion ? 0 : 1;
      // Deliberately *not* resetting `time`: the silk keeps flowing where it
      // left off, so re-summoning never snaps the ribbons back to frame 0.
    }
    lastInvoked.current = invoked;
  }, [invoked, reducedMotion]);

  const errorSeen = useRef(errorSignal);
  useEffect(() => {
    if (errorSignal > errorSeen.current) {
      smoothed.current.error = reducedMotion ? 0.45 : 1;
    }
    errorSeen.current = errorSignal;
  }, [errorSignal, reducedMotion]);

  // ── syllabic impulse from FRIDAY's own streaming words ───────────────────
  const pulseSeen = useRef(speechPulse);
  useEffect(() => {
    if (speechPulse > pulseSeen.current) {
      const props = propsRef.current;
      if (props.state === "SPEAKING" && !props.muted && !props.reducedMotion) {
        // A crisp pluck of the voice spring: the orb physically reacts to the
        // word that just arrived, on top of the synthesized cadence.
        smoothed.current.voiceVelocity = Math.min(
          smoothed.current.voiceVelocity + 0.85,
          3.2,
        );
      }
    }
    pulseSeen.current = speechPulse;
  }, [speechPulse]);

  // ── dismissal lifecycle ──────────────────────────────────────────────────
  // `wasInvoked` guarantees `onDismissed` fires exactly once per summon — never
  // on the initial dormant mount, where Python has not shown the window yet.
  const wasInvoked = useRef(false);

  const finishDismissal = useCallback(() => {
    if (!wasInvoked.current) return;
    wasInvoked.current = false;
    setContracting(false);
    setDismissed(true);
    onDismissed();
  }, [onDismissed]);

  useEffect(() => {
    if (invoked) {
      wasInvoked.current = true;
      setContracting(false);
      setDismissed(false);
      return undefined;
    }
    if (!wasInvoked.current) return undefined;
    // Paint through the contraction, then stop. Motion's
    // `onAnimationComplete` is the primary trigger; this timer is the
    // guarantee that a dropped completion (reduced motion, occluded window,
    // throttled tab) can never strand a live RAF loop.
    setContracting(true);
    const timer = window.setTimeout(
      finishDismissal,
      reducedMotion ? DISMISS_CONTRACTION_REDUCED_MS : DISMISS_CONTRACTION_MS,
    );
    return () => window.clearTimeout(timer);
  }, [invoked, reducedMotion, finishDismissal]);

  // ── the render loop (summoned *or* contracting) ──────────────────────────
  useEffect(() => {
    if (!invoked && !contracting) {
      if (frameRef.current !== null) {
        cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      return undefined;
    }

    let last = performance.now();
    const tick = (now: number) => {
      // A frame that was already queued when the orb finished dismissing must
      // never paint: the loop is the *only* thing keeping the GPU awake.
      if (!invokedRef.current && !contractingRef.current) {
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

      // ── voice spring ─────────────────────────────────────────────────────
      // ω = 15, ζ = 0.78: a crisp attack on consonants with a liquid release.
      // (The old code passed ζ = 2.0 — critically damped *twice over* — which
      // read as sluggish rubber instead of speech.)
      const omega = 15.0;
      const zeta = 0.78;
      let targetVoice = clamp01(props.amplitude);

      // main.py silences mic amplitude during TTS (feedback guard), so a
      // SPEAKING orb would otherwise sit perfectly still. A synthesized,
      // incommensurate multi-syllable cadence gives it an organic voice:
      // 3.7 / 6.1 / 9.4 Hz oscillators under a 0.85 Hz breath/pause envelope.
      const speaking = props.state === "SPEAKING" && !props.muted;
      if (speaking) {
        const t = s.time;
        const cadence =
          0.5 +
          0.34 * Math.sin(TAU * 3.7 * t) +
          0.20 * Math.sin(TAU * 6.1 * t + 1.7) +
          0.12 * Math.sin(TAU * 9.4 * t + 0.4);
        const breath = 0.72 + 0.28 * Math.sin(TAU * 0.85 * t + 0.9);
        targetVoice = Math.max(targetVoice, clamp01(cadence * breath) * 0.94);
      }

      const acceleration =
        omega * omega * (targetVoice - s.voice) - 2 * zeta * omega * s.voiceVelocity;
      s.voiceVelocity += acceleration * dt;
      s.voice = Math.max(0, Math.min(1.25, s.voice + s.voiceVelocity * dt));

      s.energy = approach(s.energy, clamp01(props.energy), 0.18);
      s.state = approach(s.state, STATE_CODE[props.state] ?? 0, 0.22);
      s.muted = approach(s.muted, props.muted ? 1 : 0, 0.2);
      s.attention = approach(s.attention, props.attention ? 1 : 0, 0.25);
      s.acting = approach(s.acting, props.acting ? 1 : 0, 0.22);
      s.error = Math.max(0, s.error - dt / 0.9);
      s.flash = Math.max(0, s.flash - dt / 1.1);
      s.time += dt * (props.reducedMotion ? 0.22 : 1);

      gl.useProgram(program);
      gl.uniform2f(uniforms.uResolution, canvas.width, canvas.height);
      gl.uniform1f(uniforms.uTime, s.time);
      gl.uniform1f(uniforms.uVoice, s.voice);
      gl.uniform1f(uniforms.uEnergy, s.energy);
      gl.uniform1f(uniforms.uState, s.state);
      gl.uniform1f(uniforms.uMuted, s.muted);
      gl.uniform1f(uniforms.uAttention, s.attention);
      gl.uniform1f(uniforms.uError, s.error);
      gl.uniform1f(uniforms.uFlash, s.flash);
      gl.uniform1f(uniforms.uActing, s.acting);
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
  }, [invoked, contracting]);

  const transition = useMemo(
    () =>
      reducedMotion
        ? { duration: 0.18, ease: "easeOut" as const }
        : { type: "spring" as const, stiffness: 320, damping: 18, mass: 0.9 },
    [reducedMotion],
  );

  const handleAnimationComplete = useCallback(() => {
    if (!invoked) finishDismissal();
  }, [invoked, finishDismissal]);

  // Tactile micro-interaction on the disc itself; kept on an inner layer so it
  // never fights the summon/dismiss scale on the outer wrapper.
  const innerScale = pressed ? 0.95 : hovered ? 1.045 : 1;

  return (
    <motion.div
      className="relative select-none"
      style={{ width: ORB_CANVAS_SIZE, height: ORB_CANVAS_SIZE }}
      initial={{ scale: 0, opacity: 0 }}
      animate={invoked ? { scale: 1, opacity: 1 } : { scale: 0.3, opacity: 0 }}
      transition={
        invoked ? transition : { duration: reducedMotion ? 0.06 : 0.36, ease: "easeIn" }
      }
      onAnimationComplete={handleAnimationComplete}
      aria-hidden={!invoked}
    >
      <motion.div
        className="absolute inset-0"
        animate={{ scale: innerScale }}
        transition={{ type: "spring", stiffness: 420, damping: 24 }}
      >
        {/* The interactive disc is a circle, matching the sphere's footprint. */}
        <div
          {...region.regionProps}
          onPointerEnter={() => {
            region.regionProps.onPointerEnter();
            setHovered(true);
          }}
          onPointerLeave={() => {
            region.regionProps.onPointerLeave();
            setHovered(false);
            setPressed(false);
          }}
          onPointerDown={() => setPressed(true)}
          onPointerUp={() => setPressed(false)}
          onPointerCancel={() => setPressed(false)}
          className={`${region.regionProps.className} absolute left-1/2 top-1/2 rounded-full`}
          style={{
            width: ORB_HIT_SIZE,
            height: ORB_HIT_SIZE,
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
    </motion.div>
  );
}

export default SiriOrb;
