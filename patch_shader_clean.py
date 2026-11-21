#!/usr/bin/env python3
import sys

path = "/home/user/FRIDAY/ui/app/src/shaders/siriOrb.frag.glsl"
with open(path, "r") as f:
    lines = f.readlines()

# Remove duplicate lines 237-240 (indices 236-239)
del lines[236:240]

with open(path, "w") as f:
    f.writelines(lines)

print("Removed duplicates")
