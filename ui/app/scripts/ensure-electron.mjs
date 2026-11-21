#!/usr/bin/env node
/**
 * Guarantee that Electron's *binary* exists, not just its package.
 *
 * `npm install` reports success even when the electron package's postinstall
 * download fails, which leaves `node_modules/electron/dist` empty. FRIDAY then
 * cannot start its UI while every command the user ran looked like it worked.
 *
 * This runs as FRIDAY's own postinstall: if the binary is missing it retries the
 * download once, and if that fails it prints the exact repair commands instead
 * of failing `npm install` (the renderer build is still valid, and the app
 * prints the same instructions with resolved paths).
 */
import { existsSync } from "node:fs";
import { spawnSync } from "node:child_process";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const appDir = dirname(dirname(fileURLToPath(import.meta.url)));
const pkgDir = join(appDir, "node_modules", "electron");
const distDir = join(pkgDir, "dist");

const binaryName =
  process.platform === "win32"
    ? "electron.exe"
    : process.platform === "darwin"
      ? join("Electron.app", "Contents", "MacOS", "Electron")
      : "electron";

const binary = join(distDir, binaryName);

if (existsSync(binary)) process.exit(0);

if (!existsSync(pkgDir)) {
  // The package itself is not installed (fresh clone, trimmed deps). Nothing to
  // verify here; `npm install` or the app's error message will cover it.
  process.exit(0);
}

console.log("[friday] Electron binary is missing — retrying the download…");
const result = spawnSync(process.execPath, [join(pkgDir, "install.js")], {
  cwd: appDir,
  stdio: "inherit",
});

if (existsSync(binary)) {
  console.log("[friday] Electron binary ready.");
  process.exit(0);
}

console.warn(
  [
    "",
    `[friday] Electron's binary could not be downloaded (installer exit ${result.status ?? "?"}).`,
    "         FRIDAY's UI cannot start without it. Repair with:",
    "",
    "           cd ui\\app",
    "           npm rebuild electron",
    "",
    "         If that fails, run the installer directly to see the network error:",
    "           node node_modules/electron/install.js",
    "",
  ].join("\n"),
);

// Never fail the install: the renderer build is valid and `python main.py`
// reports the same fix with resolved absolute paths.
process.exit(0);
