#version 300 es
/*
 * FRIDAY — 1:1 volumetric Apple-Siri orb.
 *
 * Optical anatomy, in the order it is composited:
 *   1. feathered multi-spectral ambient halo (cyan → violet → rose), decaying
 *      exactly to zero before the canvas boundary;
 *   2. deep translucent liquid-crystal core with an analytic sphere normal, so
 *      the interior reads as glass with depth instead of a flat disc;
 *   3. five additive, domain-warped iridescent silk ribbons whose crossings
 *      tone-map to white-hot silk;
 *   4. pole shading + chromatic Fresnel dispersion + a lower-contour colour
 *      bounce, which is what makes the silk look suspended *inside* the glass;
 *   5. a razor-thin upper specular arc and a soft upper-left crescent sheen,
 *      strictly clipped by the silhouette;
 *   6. 1/255 hash dither, so an 8-bit dark desktop never bands.
 *
 * Output is premultiplied (`vec4(color * alpha, alpha)`), and the canvas is
 * created with `premultipliedAlpha: true`, so the feathered halo blends with no
 * dark or grey fringe over both pitch-black and pure-white wallpapers.
 *
 * State uniforms (all smoothed on the CPU before upload, so a state change is
 * interpolated rather than popped):
 *   uState     0 LISTENING · 1 THINKING · 2 PROCESSING · 3 SPEAKING · 4 MUTED
 *   uVoice     0..1 live microphone level
 *   uEnergy    0..1 state-machine visual energy
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
uniform float uReducedMotion;

/** Sphere radius: 92 px of a 204 px canvas ⇒ 92/204 of the half-extent. */
const float R = 0.4510;
const float PI = 3.14159265359;
const float TAU = 6.28318530718;

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

float fbm(vec2 p) {
  float value = 0.0;
  float amplitude = 0.5;
  mat2 spin = mat2(0.80, 0.60, -0.60, 0.80);
  for (int i = 0; i < 4; i++) {
    value += amplitude * vnoise(p);
    p = spin * p * 2.03 + 17.1;
    amplitude *= 0.5;
  }
  return value;
}

/** The Apple Siri chromatic loop: cyan → sapphire → violet → magenta → cyan. */
vec3 palette(float t) {
  t = fract(t);
  vec3 cyan = vec3(0.024, 0.969, 1.000);   // #06F7FF
  vec3 sapphire = vec3(0.118, 0.373, 1.000); // #1E5FFF
  vec3 violet = vec3(0.690, 0.188, 1.000); // #B030FF
  vec3 magenta = vec3(1.000, 0.176, 0.624); // #FF2D9F
  if (t < 0.25) return mix(cyan, sapphire, smoothstep(0.0, 0.25, t));
  if (t < 0.50) return mix(sapphire, violet, smoothstep(0.25, 0.50, t));
  if (t < 0.75) return mix(violet, magenta, smoothstep(0.50, 0.75, t));
  return mix(magenta, cyan, smoothstep(0.75, 1.0, t));
}

/** Filmic curve: overlapping ribbons bloom into white-hot silk, not clipping. */
vec3 tonemap(vec3 x) {
  x = max(x, 0.0);
  return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}

/** Triangular blend between discrete state codes (uState is smoothed). */
float stateWeight(float value) {
  return clamp(1.0 - abs(uState - value), 0.0, 1.0);
}

void main() {
  vec2 halfRes = 0.5 * uResolution;
  float minHalf = max(min(halfRes.x, halfRes.y), 1.0);
  vec2 p = (gl_FragCoord.xy - halfRes) / minHalf;
  float r = length(p);
  float theta = atan(p.y, p.x);
  float ring = theta / TAU + 0.5;

  float time = uTime * mix(1.0, 0.15, uReducedMotion);
  float voice = clamp(uVoice, 0.0, 1.0);
  float energy = clamp(uEnergy, 0.0, 1.0);
  float muted = clamp(uMuted, 0.0, 1.0);

  float wThink = stateWeight(1.0);
  float wProcess = stateWeight(2.0);
  float wSpeak = stateWeight(3.0);
  float wMuted = max(stateWeight(4.0), muted);

  // ── 1. feathered multi-spectral ambient halo ────────────────────────────
  // One periodic palette lookup: `ring` wraps 1 -> 0 at the atan2 branch cut,
  // so a piecewise mix of three stops would leave a hard seam across the halo.
  vec3 haloColor = palette(ring + 0.08);

  float haloFalloff = exp(-pow(max(r - R, 0.0) / 0.185, 1.55));
  // Hard guarantee: nothing survives past 86 % of the half-extent, so the
  // canvas boundary can never be visible as a box or a clipped circle.
  float haloEdge = smoothstep(1.0, 0.86, r);
  float haloGain = (0.16 + 0.55 * energy + 0.55 * voice + 0.40 * uFlash) *
                   (1.0 - 0.72 * wMuted);
  vec3 halo = haloColor * haloFalloff * haloEdge * haloGain;
  // Periodic angular domain (cos/sin, never theta itself) for the same reason.
  vec2 haloNoise = vec2(cos(theta), sin(theta)) * 2.0 + r * 1.3
                 + vec2(time * 0.05, -time * 0.04);
  halo *= 0.85 + 0.32 * fbm(haloNoise);

  vec3 body = vec3(0.0);
  float sphereAlpha = 0.0;

  // ── 2..5. everything inside the glass ───────────────────────────────────
  float aa = max(fwidth(r) * 1.25, 0.0015);
  float silhouette = smoothstep(R + aa, R - aa, r);

  if (silhouette > 0.0) {
    vec2 q = p / R;
    float rr = dot(q, q);
    float z = sqrt(max(0.0, 1.0 - rr));
    vec3 n = vec3(q, z);
    float fresnel = pow(1.0 - z, 3.0);

    // Deep translucent liquid-crystal core (#18104A → #0B1644 → #210B3E).
    vec3 core = mix(vec3(0.043, 0.017, 0.212),
                    vec3(0.094, 0.063, 0.290),
                    smoothstep(0.0, 1.0, z));
    core = mix(core, vec3(0.129, 0.043, 0.243), smoothstep(0.35, 1.0, 1.0 - z));
    // Pole shading: silk suspended inside a sphere, not painted on a disc.
    core *= mix(0.55, 1.0, smoothstep(0.0, 0.6, z));
    core *= 0.88 + 0.42 * energy;
    body = core;

    float speed = mix(0.34, 1.05, energy) * mix(1.0, 1.75, wSpeak) *
                  mix(1.0, 0.42, wMuted);
    float tighten = mix(1.0, 1.55, max(wThink, wProcess));

    // Slow rigid spin of the whole silk field around the view axis.
    float rot = time * 0.07;
    float cr = cos(rot);
    float sr = sin(rot);
    float rx = cr * n.x - sr * n.y;
    float ry = sr * n.x + cr * n.y;

    vec3 silk = vec3(0.0);
    for (int i = 0; i < 5; i++) {
      float fi = float(i);

      // Irrational per-band speeds keep the ribbons from ever re-aligning.
      float phase = time * speed * (0.31 + 0.14 * fi) + fi * 1.70;

      // Domain warp sampled on the sphere's own surface, so the bands wrap
      // around it instead of collapsing into a pinwheel at the pole.
      vec2 warp = vec2(
        fbm(vec2(rx * 2.0 + n.z * 0.7 + phase * 0.13,
                 ry * 2.2 + n.z * 0.9 + fi * 2.7)),
        fbm(vec2(rx * 1.7 - phase * 0.11,
                 ry * 1.9 + n.z * 0.6 + 4.1 + fi * 1.3))
      ) - 0.5;

      // Sphere-space harmonics only: `theta` is undefined at the centre and any
      // angular term there would pinch into a star.
      float wobble = sin(n.y * 5.0 + phase + fi * 1.3 + warp.x * 2.4)
                   + 0.6 * sin(n.x * 6.0 - phase * 1.1 + fi * 0.7 + warp.y * 2.0);

      float lift = (ry * 1.9 + rx * 0.35) * (1.5 + fi * 0.26) * tighten
                 + wobble * (0.45 + 0.25 * voice)
                 + warp.y * 1.1;

      float ribbon = sin(lift * 2.8 + fi * 0.8);
      ribbon = pow(smoothstep(-0.15, 1.0, ribbon), 2.2);
      // Hairline strands riding each ribbon: the fine silk texture of iOS Siri.
      ribbon += 0.35 * pow(smoothstep(0.2, 1.0, sin(lift * 6.1 + fi * 2.1)), 3.0);
      // Speaking: ribbons widen and lift toward the rim.
      ribbon *= mix(1.0, 0.70 + 0.62 * fresnel, wSpeak);

      float colorPhase = fract(0.09 * fi + 0.35 * warp.y + 0.20 * n.x +
                               0.12 * n.y + time * 0.03 * (0.7 + fi * 0.11));
      vec3 tint = palette(colorPhase);
      tint = mix(tint, vec3(0.95, 0.45, 0.55), wMuted * 0.55);
      silk += tint * ribbon * (0.5 + 0.5 * z);
    }

    // Additive accumulation, then filmic tone-mapping: crossings bloom to white.
    silk *= 0.55 + 0.75 * energy + 0.50 * voice;
    body += silk * 0.90;

    // Internal refraction: colour bounce along the lower interior contour.
    float lower = smoothstep(0.15, -0.85, n.y);
    body += palette(0.55 + 0.08 * sin(time * 0.35)) * lower * fresnel * 0.30;

    // Chromatic dispersion along the rim (RGB split of the Fresnel term).
    float dispersion = fresnel * fresnel;
    body.r += dispersion * 0.20;
    body.b += dispersion * 0.32;

    // Chromatic silhouette glow: the sphere's own coloured rim light.
    float rimPhase = 0.5 + 0.5 * n.x * cos(1.1 * sin(time * 0.2)) + 0.35 * n.y;
    body += palette(rimPhase) * pow(fresnel, 2.0) * 0.45;

    // Apple glass specular signature.
    vec3 lightDir = normalize(vec3(-0.42, 0.68, 0.60));
    float spec = pow(max(dot(n, lightDir), 0.0), 90.0);
    float crescent = smoothstep(0.45, 0.92, n.y) * smoothstep(0.02, 0.5, z) *
                     pow(max(dot(normalize(n.xy + vec2(1e-5)),
                                 normalize(vec2(-0.55, 0.85))), 0.0), 8.0);
    body += vec3(1.0) * (spec * 1.45 + crescent * 0.22) * (1.0 - 0.65 * wMuted);

    body = tonemap(body * 1.10);

    // Saturation lift: tone-mapping flattens the palette toward pastel; the
    // Apple orb keeps its cyan / violet / rose separation at full strength.
    float luma = dot(body, vec3(0.2126, 0.7152, 0.0722));
    body = clamp(vec3(luma) + (body - vec3(luma)) * 1.22, 0.0, 1.0);

    // Attention (confirmation waiting): warm magenta breathing pulse.
    float pulse = 0.5 + 0.5 * sin(time * 2.2);
    body = mix(body, body * vec3(1.30, 0.74, 1.02), uAttention * (0.40 + 0.38 * pulse));
    // Error: a brief rose chromatic slip.
    body = mix(body, body * vec3(1.38, 0.55, 0.80), clamp(uError, 0.0, 1.0));

    sphereAlpha = 0.92 * silhouette;
  }

  // ── composite: glass core over additive halo ────────────────────────────
  vec3 premultiplied = body * sphereAlpha + halo * (1.0 - silhouette);

  // Anti-banding: triangular-ish hash dither at 1/255.
  float dither = (hash12(gl_FragCoord.xy + fract(uTime) * 137.31) - 0.5) / 255.0;
  premultiplied = max(premultiplied + dither, 0.0);

  // Coverage: the glass keeps its 0.92, the halo is exactly its own brightness
  // (so it reads as a pure glow), and any emissive specular lifts it locally.
  float brightness = max(premultiplied.r, max(premultiplied.g, premultiplied.b));
  float alpha = clamp(max(sphereAlpha, max(brightness * (1.0 - silhouette),
                                          brightness * silhouette)), 0.0, 1.0);
  alpha = clamp(max(alpha, brightness), 0.0, 1.0);

  // Un-premultiply to straight colour, then multiply back on output: the
  // canonical premultiplied contract, with colour <= alpha so no fringe,
  // clamp artefact or grey halo edge can appear on any wallpaper.
  vec3 color = premultiplied / max(alpha, 1e-4);

  fragColor = vec4(color * alpha, alpha);
}
