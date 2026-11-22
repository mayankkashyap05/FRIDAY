#version 300 es
/*
 * FRIDAY — 1:1 Apple 3D volumetric silk Siri orb.
 *
 * Optical anatomy, in the order it is composited:
 *
 *   1. a restrained ambient halo, outside the silhouette, that echoes the live
 *      ribbon colours and is *exactly* 0.0 by r = 0.88 (no box, no seam);
 *   2. a deep crystal void core — obsidian-indigo, #0A0724 centre → #081134
 *      mid → #16072C rim — with spherical depth shading. The high contrast in
 *      the core is what makes the ribbons read as luminous instead of pastel;
 *   3. four broad volumetric silk petals, each a 3D-tilted Gaussian sheet
 *      (soft body + inner core filament) that wraps the glass sphere, its
 *      domain warped through the spherical lens, additively accumulated so
 *      crossings climb past 1.0 and ACES tone-mapping turns them white-hot
 *      incandescent silk;
 *   4. a razor-thin iridescent Fresnel rim with RGB dispersion in the outer
 *      few percent, plus a lower-hemisphere chromatic caustic bounce;
 *   5. an upper-left glass glint and a broad upper crescent sheen, strictly
 *      inside the silhouette;
 *   6. 1/255 hash dither, so an 8-bit wallpaper never bands.
 *
 * Output is canonical premultiplied (`vec4(color * alpha, alpha)`), and the
 * canvas is created with `premultipliedAlpha: true`, so the feathered halo
 * blends with no dark or grey fringe over both pitch-black and white.
 *
 * Uniforms (all smoothed on the CPU before upload, so state changes are
 * interpolated rather than popped):
 *   uState     0 LISTENING · 1 THINKING · 2 PROCESSING · 3 SPEAKING · 4 MUTED
 *   uVoice     0..1 live mic level *or* the synthesized speech cadence
 *   uEnergy    0..1 state-machine visual energy
 *   uActing    0..1 background work running (rim shimmer + internal energy)
 *   uAttention 0..1 confirmation card waiting
 *   uError     0..1 recent error, decays over ~900 ms
 *   uFlash     0..1 summon bloom
 */

precision highp float;
precision highp int;

out vec4 fragColor;

uniform vec2 uResolution;
uniform float uTime;
uniform float uVoice;
uniform float uEnergy;
uniform float uState;
uniform float uMuted;
uniform float uAttention;
uniform float uError;
uniform float uFlash;
uniform float uActing;
uniform float uReducedMotion;

const float PI = 3.14159265359;
const float TAU = 6.28318530718;

/** Sphere radius: 92 px of a 204 px canvas ⇒ 92/204 of the half-extent. */
const float BASE_R = 0.4510;
/** The halo must be gone before the canvas edge: exact 0.0 beyond this. */
const float HALO_EDGE = 0.88;

/* ── deep crystal void core ─────────────────────────────────────────────── */
const vec3 CORE_CENTER = vec3(0.039, 0.027, 0.141); // #0A0724
const vec3 CORE_MID = vec3(0.031, 0.067, 0.204);    // #081134
const vec3 CORE_RIM = vec3(0.086, 0.027, 0.173);    // #16072C

/* ── the four silk ribbons ──────────────────────────────────────────────── */
const vec3 RIBBON0_A = vec3(0.024, 0.969, 1.000); // #06F7FF electric cyan
const vec3 RIBBON0_B = vec3(0.220, 0.910, 1.000); // #38E8FF aqua
const vec3 RIBBON1_A = vec3(0.118, 0.373, 1.000); // #1E5FFF sapphire
const vec3 RIBBON1_B = vec3(0.302, 0.545, 1.000); // #4D8BFF cobalt
const vec3 RIBBON2_A = vec3(1.000, 0.176, 0.624); // #FF2D9F magenta
const vec3 RIBBON2_B = vec3(1.000, 0.369, 0.722); // #FF5EB8 rose
const vec3 RIBBON3_A = vec3(0.690, 0.188, 1.000); // #B030FF amethyst
const vec3 RIBBON3_B = vec3(0.561, 0.357, 1.000); // #8F5BFF violet

float hash12(vec2 p) {
  vec3 p3 = fract(vec3(p.xyx) * 0.1031);
  p3 += dot(p3, p3.yzx + 33.33);
  return fract((p3.x + p3.y) * p3.z);
}

float vnoise(vec2 p) {
  vec2 i = floor(p);
  vec2 f = fract(p);
  vec2 u = f * f * (3.0 - 2.0 * f);
  float a = hash12(i);
  float b = hash12(i + vec2(1.0, 0.0));
  float c = hash12(i + vec2(0.0, 1.0));
  float d = hash12(i + vec2(1.0, 1.0));
  return mix(mix(a, b, u.x), mix(c, d, u.x), u.y);
}

/** Two octaves is all the domain warp needs; the ribbons carry the detail. */
float fbm(vec2 p) {
  float value = 0.0;
  float amplitude = 0.55;
  mat2 spin = mat2(0.80, 0.60, -0.60, 0.80);
  for (int i = 0; i < 2; i++) {
    value += amplitude * vnoise(p);
    p = spin * p * 2.03 + 17.1;
    amplitude *= 0.5;
  }
  return value;
}

/** ACES filmic curve: overlapping ribbons bloom to white-hot silk. */
vec3 tonemap(vec3 x) {
  x = max(x, 0.0);
  return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}

/** Triangular blend between discrete state codes (uState is smoothed). */
float stateWeight(float value) {
  return clamp(1.0 - abs(uState - value), 0.0, 1.0);
}

/** Slow gradient across a ribbon: front-facing silk is brighter. */
vec3 ribbonColor(vec3 a, vec3 b, float z, float theta, float time, float seed) {
  float t = clamp(0.32 + 0.62 * z + 0.10 * sin(theta * 1.7 + time * 0.21 + seed),
                  0.0, 1.0);
  return mix(a, b, t);
}

/**
 * One broad silk petal.
 *
 * The sheet is a *plane through the sphere*: `dot(n, dir)` measures how far the
 * surface point sits from it, so the ribbon is a great-circle band that wraps
 * the glass (and therefore fades toward the silhouette and darkens the front
 * pole) instead of a flat stripe painted on a disc. Two octaves of value noise
 * — sampled in the spherical-lens frame, so the band bends with the glass —
 * keep it organic. The Gaussian of that distance is the cross-section; a
 * fourth-power-narrower Gaussian rides inside it as the bright core filament.
 */
vec3 silkRibbon(vec3 n, vec2 lens, float angle, float offset, float tilt,
                float sharpen, float warpAmp, float phase, float gain,
                vec3 colA, vec3 colB, float z, float theta, float time,
                float seed, float thinkShift, float speakBoost) {
  float ca = cos(angle);
  float sa = sin(angle);
  // The lensed sample coordinate is rotated into the ribbon's own frame; that
  // is what makes the warp bend around the curved wall of the glass.
  vec2 rp = vec2(ca * lens.x - sa * lens.y, sa * lens.x + ca * lens.y);

  // Organic 2-octave domain warp.
  vec2 sway = vec2(sin(phase * 0.73 + rp.y * 1.15),
                   cos(phase * 0.61 - rp.x * 1.35));
  float w1 = fbm(rp * 1.85 + sway + vec2(0.0, n.z * 0.6));
  float w2 = fbm(rp * 3.05 - sway * 0.7 + vec2(5.2, 1.7 + n.z * 0.4));

  // 3D-tilted plane through the sphere: a great-circle band, not a stripe.
  vec3 dir = normalize(vec3(ca, sa, tilt));
  float d = dot(n, dir) - offset
          + (w1 - 0.5) * warpAmp
          + (w2 - 0.5) * warpAmp * 0.45;

  float body = exp(-d * d * sharpen);
  float filament = exp(-d * d * sharpen * 4.5);
  // Attenuate toward the sphere edge: the sheet is inside glass, not on it.
  // A steeper falloff than a bare pow(z, 0.45) stops the ribbons from tracing
  // a neon outline around the silhouette; they sink into the glass instead.
  float edge = pow(z, 0.80);

  vec3 tint = ribbonColor(colA, colB, z, theta, time, seed);
  // THINKING: ribbons pull toward luminous amethyst / sapphire.
  tint = mix(tint, mix(RIBBON1_B, RIBBON3_A, 0.55), 0.42 * thinkShift);
  // SPEAKING: a touch brighter and warmer at the core.
  tint = mix(tint, tint * 1.08 + vec3(0.02), 0.55 * speakBoost);

  // A soft broad sheet with a hotter filament riding its spine: that contrast
  // is what makes the silk look lit from within rather than flatly painted.
  return tint * (0.85 * body + 0.45 * filament) * edge * gain;
}

void main() {
  vec2 halfRes = 0.5 * uResolution;
  float minHalf = max(min(halfRes.x, halfRes.y), 1.0);
  vec2 p = (gl_FragCoord.xy - halfRes) / minHalf;
  float r = length(p);
  float theta = atan(p.y, p.x);

  float time = uTime * mix(1.0, 0.15, uReducedMotion);
  float voice = clamp(uVoice, 0.0, 1.0);
  float energy = clamp(uEnergy, 0.0, 1.0);
  float muted = clamp(uMuted, 0.0, 1.0);
  float acting = clamp(uActing, 0.0, 1.0);

  float wThink = stateWeight(1.0);
  float wProcess = stateWeight(2.0);
  float wSpeak = stateWeight(3.0);
  float wMuted = max(stateWeight(4.0), muted);

  // SPEAKING breathes: the whole glass swells 2.8 % with each syllable.
  float R = BASE_R * (1.0 + 0.028 * voice * wSpeak);

  // ── 1. ambient halo: echoes the live ribbon colours ──────────────────────
  // Periodic angular weights (cos of theta, never theta itself) so the atan2
  // branch cut cannot leave a seam across the glow.
  float a0 = 0.5 + 0.5 * cos(theta - 0.00);
  float a1 = 0.5 + 0.5 * cos(theta - 1.18);
  float a2 = 0.5 + 0.5 * cos(theta - 2.31);
  float a3 = 0.5 + 0.5 * cos(theta - 3.74);
  float aSum = max(a0 + a1 + a2 + a3, 1e-4);
  vec3 haloColor = (RIBBON0_A * a0 + RIBBON1_A * a1 +
                    RIBBON2_A * a2 + RIBBON3_A * a3) / aSum;

  float outside = max(r - R, 0.0);
  float haloFalloff = exp(-outside / 0.085);
  // Exactly zero at and beyond HALO_EDGE — smoothstep returns 1.0 there, so
  // the (1.0 - …) factor is a hard 0.0, not "very small".
  float haloCut = 1.0 - smoothstep(R + 0.015, HALO_EDGE, r);
  float haloGain = (0.10 + 0.50 * energy + 0.52 * voice + 0.35 * uFlash) *
                   (1.0 - 0.72 * wMuted);
  haloGain *= 1.0 + uAttention * 0.50 * (0.5 + 0.5 * sin(time * 2.0));
  vec3 halo = haloColor * haloFalloff * haloCut * haloGain;
  vec2 haloNoise = vec2(cos(theta), sin(theta)) * 2.0 + r * 1.3
                 + vec2(time * 0.05, -time * 0.04);
  halo *= 0.86 + 0.30 * fbm(haloNoise);

  vec3 body = vec3(0.0);
  float sphereAlpha = 0.0;

  // ── 2..5. everything inside the glass ────────────────────────────────────
  float aa = max(fwidth(r) * 1.25, 0.0015);
  float silhouette = smoothstep(R + aa, R - aa, r);

  if (silhouette > 0.0) {
    vec2 q = p / R;
    float rr = dot(q, q);
    float z = sqrt(max(0.0, 1.0 - rr));
    vec3 n = vec3(q, z);
    float fres = pow(1.0 - z, 3.8);

    // Spherical lensing: the internal coordinates are projected *through* the
    // curved glass, so ribbons bend, stretch and compress around the walls
    // instead of sliding across a flat disc.
    vec2 lens = q / (0.62 + 0.38 * z);
    float tighten = mix(1.0, 1.55, max(wThink, wProcess));
    lens *= tighten;

    // ── deep crystal void core, with spherical depth shading ──────────────
    vec3 core = mix(CORE_RIM, CORE_MID, smoothstep(0.0, 0.55, z));
    core = mix(core, CORE_CENTER, smoothstep(0.45, 1.0, z));
    core *= 0.42 + 0.45 * pow(z, 0.85);
    core *= 0.90 + 0.35 * energy;

    // ── four volumetric silk petals ───────────────────────────────────────
    float speed = mix(0.34, 1.05, energy) * mix(1.0, 1.65, wSpeak) *
                  mix(1.0, 0.38, wMuted) * mix(1.0, 1.28, wThink);
    float drift = time * speed * 0.16;
    // Lower sharpness ⇒ a wider sheet; voice opens them up.
    float sharpen = 40.0 * mix(1.0, 0.62, voice);
    float warpAmp = 0.22 + 0.25 * voice;
    float gain = 0.85 + 0.30 * energy + 0.25 * voice;
    float thinkShift = wThink;
    float speakBoost = wSpeak;

    vec3 silk = vec3(0.0);
    // Irrational base angles: the four sheets never re-align into stripes.
    silk += silkRibbon(n, lens, 0.00 + drift,        0.06, 1.35,
                       sharpen, warpAmp, time * 0.22, gain,
                       RIBBON0_A, RIBBON0_B, z, theta, time, 0.0,
                       thinkShift, speakBoost);
    silk += silkRibbon(n, lens, 1.18 - drift * 0.86, 0.18, 0.55,
                       sharpen * 0.94, warpAmp, time * 0.19, gain * 0.96,
                       RIBBON1_A, RIBBON1_B, z, theta, time, 1.7,
                       thinkShift, speakBoost);
    silk += silkRibbon(n, lens, 2.31 + drift * 0.71, -0.14, 0.95,
                       sharpen * 1.06, warpAmp * 0.9, time * 0.26, gain * 0.92,
                       RIBBON2_A, RIBBON2_B, z, theta, time, 3.4,
                       thinkShift, speakBoost);
    silk += silkRibbon(n, lens, 3.74 - drift * 0.61, 0.24, 0.30,
                       sharpen * 0.88, warpAmp * 1.1, time * 0.17, gain * 0.90,
                       RIBBON3_A, RIBBON3_B, z, theta, time, 5.1,
                       thinkShift, speakBoost);

    // PROCESSING / uActing: brisk cyan-violet internal energy.
    silk += mix(RIBBON0_A, RIBBON3_A, 0.5 + 0.5 * sin(time * 1.3)) *
            acting * 0.12 * z;

    // Additive accumulation: wherever sheets cross, radiance climbs past 1.0
    // and ACES turns the intersection into white-hot incandescent silk.
    body = core + silk * 1.15;

    // ── lower-hemisphere chromatic caustic bounce ─────────────────────────
    float lower = smoothstep(0.15, -0.85, n.y);
    body += mix(RIBBON1_B, RIBBON2_A, 0.45) * lower * fres * 0.34;

    // ── razor-thin iridescent Fresnel rim + RGB dispersion ────────────────
    float shimmer = 1.0 + acting * 0.55 *
                    (0.5 + 0.5 * sin(theta * 3.0 - time * 2.1 + n.y * 2.0));
    float rim = fres * shimmer;
    body += mix(RIBBON0_A, RIBBON3_A, 0.5 + 0.5 * sin(theta * 2.0 + time * 0.3)) *
            rim * 0.40;
    // Delicate spectral split at the outer edge: red and blue separate, the
    // outer 5 % reads as real glass rather than a painted outline.
    body.r += rim * 0.12;
    body.b += rim * 0.20;

    // ── upper-left glass specular ────────────────────────────────────────
    vec3 lightDir = normalize(vec3(-0.42, 0.68, 0.60));
    float spec = pow(max(dot(n, lightDir), 0.0), 90.0);
    float crescent = smoothstep(0.45, 0.92, n.y) * smoothstep(0.02, 0.5, z) *
                     pow(max(dot(normalize(n.xy + vec2(1e-5)),
                                 normalize(vec2(-0.55, 0.85))), 0.0), 8.0);
    body += vec3(1.0) * (spec * 1.55 + crescent * 0.20) * (1.0 - 0.65 * wMuted);

    // ── filmic tone-map + saturation retention ───────────────────────────
    body = tonemap(body * 1.15);
    float luma = dot(body, vec3(0.2126, 0.7152, 0.0722));
    body = clamp(vec3(luma) + (body - vec3(luma)) * 1.20, 0.0, 1.0);

    // MUTED: desaturated cool twilight glass with a rose edge tint.
    vec3 twilight = vec3(luma) * 0.38 + vec3(0.085, 0.028, 0.065);
    body = mix(body, twilight, wMuted * 0.88);

    // uAttention: warm rose-magenta breathing aura.
    float pulse = 0.5 + 0.5 * sin(time * 2.2);
    body = mix(body,
               body * vec3(1.30, 0.78, 1.02) + vec3(0.055, 0.0, 0.02),
               uAttention * (0.38 + 0.36 * pulse));

    // uError: a brief rose chromatic ripple.
    float ripple = 0.5 + 0.5 * sin(r * 42.0 - time * 12.0);
    body = mix(body,
               body * vec3(1.35, 0.60, 0.82) + vec3(0.18, 0.02, 0.06) * ripple,
               clamp(uError, 0.0, 1.0) * 0.55);

    sphereAlpha = 0.93 * silhouette;
  }

  // The error slip also ripples the halo, so it is not a sphere-only effect.
  float errorRipple = 0.5 + 0.5 * sin(r * 42.0 - time * 12.0);
  halo += RIBBON2_B * haloFalloff * haloCut * uError * 0.30 * errorRipple;

  // ── composite: glass core over additive halo ────────────────────────────
  vec3 premultiplied = body * sphereAlpha + halo * (1.0 - silhouette);

  // Anti-banding: triangular-ish hash dither at 1/255.
  float dither = (hash12(gl_FragCoord.xy + fract(uTime) * 137.31) - 0.5) / 255.0;
  premultiplied = max(premultiplied + dither, 0.0);

  // Coverage: the glass keeps its 0.93, and the halo is its own brightness, so
  // the glow reads as light instead of a grey disc.
  float brightness = max(premultiplied.r, max(premultiplied.g, premultiplied.b));
  float alpha = clamp(max(sphereAlpha, brightness), 0.0, 1.0);

  // Canonical premultiplied contract, with colour <= alpha so no fringe, clamp
  // artefact or grey halo edge can appear on any wallpaper.
  vec3 color = premultiplied / max(alpha, 1e-4);

  fragColor = vec4(color * alpha, alpha);
}
