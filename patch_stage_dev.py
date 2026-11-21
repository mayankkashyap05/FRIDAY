#!/usr/bin/env python3
import sys

path = "/home/user/FRIDAY/ui/app/src/components/StageApp.tsx"
with open(path, "r") as f:
    text = f.read()

old = 'onAmplitude={(value) => devDispatch({ t: "amplitude", value })}'
new = 'onAmplitude={(value, bands) => devDispatch({ t: "amplitude", value, ...bands ?? {} })}'

if old in text:
    text = text.replace(old, new)
    with open(path, "w") as f:
        f.write(text)
    print("Replaced successfully")
else:
    print("Old text not found")
    # Let us print context
    idx = text.find("onAmplitude=")
    if idx != -1:
        print("Context around it:")
        print(text[idx-50:idx+150])
