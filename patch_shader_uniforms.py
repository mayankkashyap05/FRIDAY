#!/usr/bin/env python3
import sys

path = "/home/user/FRIDAY/ui/app/src/shaders/siriOrb.frag.glsl"
with open(path, "r") as f:
    lines = f.readlines()

# Find the line: uniform float uReducedMotion;
# It should be around line 75 (index 74)
for i, line in enumerate(lines):
    if line.strip() == "uniform float uReducedMotion;":
        print(f"Found at line {i+1}")
        # Insert the spectral uniforms right after it
        insertion = [
            "\n",
            "  // Spectral bands and source\n",
            "  uniform float uLow;\n",
            "  uniform float uMid;\n",
            "  uniform float uHigh;\n",
            "  uniform float uSource; // 0.0 = mic, 1.0 = tts\n",
            "\n"
        ]
        lines[i+1:i+1] = insertion
        break

with open(path, "w") as f:
    f.writelines(lines)

print("Successfully inserted spectral uniforms")
