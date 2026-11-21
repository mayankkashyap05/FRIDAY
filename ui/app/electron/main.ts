import {
  app,
  BrowserWindow,
  globalShortcut,
  ipcMain,
  screen,
  session,
  shell,
} from "electron";
import type { Display, WebPreferences } from "electron";
import * as path from "node:path";

/**
 * FRIDAY's Electron shell.
 *
 * Security posture (non-negotiable — the renderer runs third-party JS):
 *   contextIsolation: true · nodeIntegration: false · sandbox: true
 *   preload is the *only* bridge, and it exposes an explicit allow-list.
 *
 * Window posture: a transparent, frameless, always-on-top panel anchored to the
 * bottom-centre of the *active* display, hidden from the taskbar and excluded
 * from screen capture, that ignores the mouse unless a surface asks for input.
 *
 * Geometry note: `screen.*.workArea` and `BrowserWindow` bounds are already in
 * DIPs on every platform. Multiplying by `display.scaleFactor` (as v1 did)
 * double-scaled the stage on 125 %/150 %/175 % Windows displays and pushed it
 * off-centre; nothing here ever multiplies by the scale factor.
 */

const APP_DIR = app.getAppPath();
const INDEX_HTML = path.join(APP_DIR, "dist", "index.html");
const DEV_URL = process.env.FRIDAY_DEV_URL || "";

const STAGE_WIDTH = 520;
const STAGE_HEIGHT = 640;
const STAGE_MARGIN_BOTTOM = 24;

/** Inspector surface; centred on the display the user is actually using. */
const INSPECTOR_WIDTH = 900;
const INSPECTOR_HEIGHT = 620;
const INSPECTOR_MIN_WIDTH = 720;
const INSPECTOR_MIN_HEIGHT = 480;

let stageWindow: BrowserWindow | null = null;
let inspectorWindow: BrowserWindow | null = null;
let interactive = false;
let focusable = false;
let invoked = false;
let escapeArmed = false;
let escapeShortcutActive = false;
let inspectorTab = "camera";
let quitting = false;

function connection(): { url: string; token: string } | null {
  const url = process.env.FRIDAY_WS_URL || "";
  const token = process.env.FRIDAY_WS_TOKEN || "";
  if (!url) return null;
  return { url, token };
}

function rendererUrl(kind: "stage" | "inspector", tab?: string): string {
  if (DEV_URL) {
    const base = DEV_URL.replace(/\/$/, "");
    return kind === "inspector" ? `${base}/#/inspector?tab=${tab ?? "camera"}` : `${base}/#/`;
  }
  const file = `file://${INDEX_HTML.replace(/\\/g, "/")}`;
  return kind === "inspector"
    ? `${file}#/inspector?tab=${tab ?? "camera"}`
    : `${file}#/`;
}

function commonWebPreferences(kind: "stage" | "inspector"): WebPreferences {
  return {
    preload: path.join(__dirname, "preload.js"),
    contextIsolation: true,
    nodeIntegration: false,
    sandbox: true,
    webSecurity: true,
    spellcheck: false,
    devTools: Boolean(process.env.FRIDAY_UI_DEBUG),
    backgroundThrottling: false,
    additionalArguments: [`--friday-window=${kind}`],
  };
}

function harden(win: BrowserWindow): void {
  // No popups, no navigation away from the bundled renderer, no permissions.
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:/.test(url)) void shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    const allowed = DEV_URL ? url.startsWith(DEV_URL) : url.startsWith("file://");
    if (!allowed) event.preventDefault();
  });
  win.webContents.on("render-process-gone", () => {
    // A dead renderer means a dead UI: never leave an invisible process behind.
    quitting = true;
    app.quit();
  });
}

/* ── geometry ─────────────────────────────────────────────────────────────── */

/** The display under the cursor — where the user is actually looking. */
function activeDisplay(): Display {
  try {
    const point = screen.getCursorScreenPoint();
    return screen.getDisplayNearestPoint(point) ?? screen.getPrimaryDisplay();
  } catch {
    return screen.getPrimaryDisplay();
  }
}

export interface StageGeometry {
  x: number;
  y: number;
  width: number;
  height: number;
}

/**
 * Bottom-centre anchor inside the display's work area.
 *
 * Everything above is DIPs already, so this is pure arithmetic — the v1
 * `* scale` multiplication is gone for good.
 */
function stageGeometry(display: Display = activeDisplay()): StageGeometry {
  const area = display.workArea;
  const width = Math.min(STAGE_WIDTH, Math.max(240, area.width - 16));
  const height = Math.min(STAGE_HEIGHT, Math.max(380, area.height - 20));
  const x = Math.round(area.x + (area.width - width) / 2);
  const y = Math.round(
    Math.max(area.y, area.y + area.height - height - STAGE_MARGIN_BOTTOM),
  );
  return { x, y, width, height };
}

function stagePosition(): { x: number; y: number } {
  const { x, y } = stageGeometry();
  return { x, y };
}

/** Re-anchor a live stage to the active display without touching focus. */
function applyStageGeometry(): void {
  const win = stageWindow;
  if (!win || win.isDestroyed()) return;
  const geometry = stageGeometry();
  if (win.isMaximized() || win.isFullScreen()) return;
  win.setBounds(geometry, false);
}

function inspectorPosition(): { x: number; y: number } {
  const area = activeDisplay().workArea;
  const width = Math.min(INSPECTOR_WIDTH, Math.max(INSPECTOR_MIN_WIDTH, area.width - 40));
  const height = Math.min(INSPECTOR_HEIGHT, Math.max(INSPECTOR_MIN_HEIGHT, area.height - 60));
  return {
    x: Math.round(area.x + (area.width - width) / 2),
    y: Math.round(area.y + (area.height - height) / 2),
  };
}

/* ── windows ──────────────────────────────────────────────────────────────── */

function createStage(): BrowserWindow {
  const geometry = stageGeometry();
  const win = new BrowserWindow({
    width: geometry.width,
    height: geometry.height,
    x: geometry.x,
    y: geometry.y,
    show: false,
    transparent: true,
    frame: false,
    resizable: false,
    movable: false,
    minimizable: false,
    maximizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    hasShadow: false,
    roundedCorners: true,
    backgroundColor: "#00000000",
    focusable: false,
    alwaysOnTop: true,
    acceptFirstMouse: true,
    type: "toolbar",
    webPreferences: commonWebPreferences("stage"),
  });

  win.setAlwaysOnTop(true, "screen-saver");
  win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true });
  // Launchpad: the stage must never be captured in a screen recording.
  win.setContentProtection(true);
  // Click-through by default, with mouse *forwarding* so hover still works and
  // the orb can ask for input the moment the pointer touches it.
  win.setIgnoreMouseEvents(true, { forward: true });

  harden(win);
  void win.loadURL(rendererUrl("stage"));
  win.once("ready-to-show", () => win.showInactive());

  win.on("closed", () => {
    stageWindow = null;
    syncEscapeShortcut();
    if (!quitting) {
      quitting = true;
      app.quit();
    }
  });

  // Hidden means hidden: never leave an Escape hook (or an input grab) behind.
  win.on("hide", () => {
    interactive = false;
    focusable = false;
    syncEscapeShortcut();
  });
  win.on("show", () => syncEscapeShortcut());
  win.on("blur", () => syncEscapeShortcut());

  // Esc is handled here because a click-through window does not always own the
  // keyboard: forward it only while the renderer has armed it.
  win.webContents.on("before-input-event", (event, input) => {
    if (input.type === "keyDown" && input.key === "Escape" && escapeArmed) {
      event.preventDefault();
      win.webContents.send("friday:escape");
    }
  });

  return win;
}

/**
 * Windows 11 (build >= 22000) is the only place Electron can hand us real
 * Acrylic. On Windows 10 the material is a no-op, so we keep an opaque
 * `#0b0b0f` window instead of a translucent-but-blank one.
 */
function supportsAcrylic(): boolean {
  if (process.platform !== "win32") return false;
  try {
    const build = Number.parseInt(process.getSystemVersion().split(".")[2] ?? "0", 10);
    return Number.isFinite(build) && build >= 22000;
  } catch {
    return false;
  }
}

function createInspector(tab: string): BrowserWindow {
  inspectorTab = tab || "camera";
  if (inspectorWindow && !inspectorWindow.isDestroyed()) {
    inspectorWindow.show();
    inspectorWindow.focus();
    inspectorWindow.webContents.send("friday:inspector-tab", inspectorTab);
    return inspectorWindow;
  }

  const { x, y } = inspectorPosition();
  const acrylic = supportsAcrylic();
  const win = new BrowserWindow({
    width: INSPECTOR_WIDTH,
    height: INSPECTOR_HEIGHT,
    x,
    y,
    minWidth: INSPECTOR_MIN_WIDTH,
    minHeight: INSPECTOR_MIN_HEIGHT,
    show: false,
    frame: false,
    title: "FRIDAY Inspector",
    // Windows 11 Acrylic; older Windows 10 builds simply ignore the material
    // and fall back to the solid surface colour baked into index.css.
    backgroundColor: acrylic ? "#00000000" : "#0b0b0f",
    ...(acrylic ? { backgroundMaterial: "acrylic" as const } : {}),
    skipTaskbar: false,
    autoHideMenuBar: true,
    webPreferences: commonWebPreferences("inspector"),
  });

  harden(win);
  void win.loadURL(rendererUrl("inspector", inspectorTab));
  win.once("ready-to-show", () => win.show());

  win.on("closed", () => {
    inspectorWindow = null;
    // Tell the renderer's stage + Python that the inspector is gone.
    if (stageWindow && !stageWindow.isDestroyed()) {
      stageWindow.webContents.send("friday:inspector-closed");
    }
  });

  return win;
}

/* ── input / keyboard modes ───────────────────────────────────────────────── */

function applyStageInputMode(): void {
  const win = stageWindow;
  if (!win || win.isDestroyed()) return;
  // Ignore mouse *unless* a surface claimed input; always forward, so leaving a
  // surface (and hovering the next one) keeps working.
  win.setIgnoreMouseEvents(!interactive, { forward: true });
}

/**
 * The voice-summoned stage is intentionally *not focusable*, so a window-level
 * Escape handler would never see the key. Instead, a global Escape is armed
 * only while `invoked && !focusable && visible` and released on blur, hide,
 * dismissal, or quit. When the stage *is* focusable, `before-input-event`
 * handles Escape locally and this registration is deliberately absent.
 */
function syncEscapeShortcut(): void {
  const win = stageWindow;
  const wanted = Boolean(
    win && !win.isDestroyed() && win.isVisible() && invoked && !focusable,
  );
  if (wanted === escapeShortcutActive) return;

  if (wanted) {
    let registered = false;
    try {
      registered = globalShortcut.register("Escape", () => {
        const target = stageWindow;
        if (!target || target.isDestroyed()) return;
        target.webContents.send("friday:escape");
      });
    } catch {
      // Another app owns Escape; the orb simply keeps the pointer-only path.
      registered = false;
    }
    escapeShortcutActive = registered;
    return;
  }

  if (escapeShortcutActive) {
    try {
      globalShortcut.unregister("Escape");
    } catch {
      /* already released */
    }
  }
  escapeShortcutActive = false;
}

/** True 0 % dormant HWND footprint: hidden, click-through, no input hooks. */
function hideStage(): void {
  const win = stageWindow;
  interactive = false;
  focusable = false;
  invoked = false;
  if (win && !win.isDestroyed()) {
    applyStageInputMode();
    try {
      win.setFocusable(false);
      win.blur();
    } catch {
      /* window may already be closing */
    }
    syncEscapeShortcut();
    win.hide();
  } else {
    syncEscapeShortcut();
  }
}

function registerIpc(): void {
  ipcMain.handle("friday:connection", () => connection());

  ipcMain.handle("friday:interactive", (_event, value: unknown) => {
    interactive = Boolean(value);
    applyStageInputMode();
  });

  ipcMain.handle("friday:focusable", (_event, value: unknown) => {
    const win = stageWindow;
    if (!win || win.isDestroyed()) {
      focusable = Boolean(value);
      return;
    }
    const next = Boolean(value);
    if (!next) {
      // Release the keyboard *before* dropping focusability, so Windows returns
      // the caret to the user's foreground app instead of a hidden panel.
      if (focusable) win.blur();
      win.setFocusable(false);
      focusable = false;
    } else {
      focusable = true;
      win.setFocusable(true);
      if (!win.isVisible()) win.showInactive();
      win.focus();
    }
    syncEscapeShortcut();
  });

  ipcMain.handle("friday:invoked", (_event, value: unknown) => {
    const win = stageWindow;
    invoked = Boolean(value);
    if (!win || win.isDestroyed()) return;
    if (invoked) {
      // Re-anchor to whatever display the user is on right now, then summon
      // without stealing focus (`showInactive`, never `show`+`focus`).
      applyStageGeometry();
      if (!win.isVisible()) win.showInactive();
      win.setAlwaysOnTop(true, "screen-saver");
    }
    syncEscapeShortcut();
  });

  ipcMain.handle("friday:hide-stage", () => {
    hideStage();
  });

  ipcMain.handle("friday:open-inspector", (_event, tab: unknown) => {
    createInspector(typeof tab === "string" ? tab : "camera");
  });

  ipcMain.handle("friday:close-inspector", () => {
    if (inspectorWindow && !inspectorWindow.isDestroyed()) inspectorWindow.close();
    inspectorWindow = null;
    if (stageWindow && !stageWindow.isDestroyed()) {
      stageWindow.webContents.send("friday:inspector-closed");
    }
  });

  ipcMain.handle("friday:inspector-minimize", () => {
    if (inspectorWindow && !inspectorWindow.isDestroyed()) inspectorWindow.minimize();
  });

  ipcMain.handle("friday:inspector-maximize", () => {
    const win = inspectorWindow;
    if (!win || win.isDestroyed()) return false;
    if (win.isMaximized()) {
      win.unmaximize();
      return false;
    }
    win.maximize();
    return true;
  });

  ipcMain.handle("friday:inspector-tab", (_event, tab: unknown) => {
    inspectorTab = typeof tab === "string" ? tab : "camera";
    if (inspectorWindow && !inspectorWindow.isDestroyed()) {
      inspectorWindow.webContents.send("friday:inspector-tab", inspectorTab);
    }
  });

  ipcMain.handle("friday:escape-armed", (_event, value: unknown) => {
    escapeArmed = Boolean(value);
  });

  ipcMain.handle("friday:quit", () => {
    quitting = true;
    app.quit();
  });

  ipcMain.handle("friday:focus-stage", () => {
    if (stageWindow && !stageWindow.isDestroyed()) {
      stageWindow.showInactive();
      if (focusable) stageWindow.focus();
    }
  });
}

// ── app lifecycle ───────────────────────────────────────────────────────────

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => {
    if (stageWindow && !stageWindow.isDestroyed()) stageWindow.showInactive();
  });

  app.commandLine.appendSwitch("enable-features", "CanvasOopRasterization");

  void app.whenReady().then(() => {
    // The renderer never needs a device permission; deny everything by default.
    session.defaultSession.setPermissionRequestHandler((_wc, _permission, callback) => {
      callback(false);
    });
    session.defaultSession.setPermissionCheckHandler(() => false);

    registerIpc();
    stageWindow = createStage();

    // Monitors come and go (docking stations, RDP, projectors): the overlay
    // re-anchors itself instead of living off-screen forever.
    const reanchor = () => applyStageGeometry();
    screen.on("display-metrics-changed", reanchor);
    screen.on("display-added", reanchor);
    screen.on("display-removed", reanchor);
  });

  app.on("window-all-closed", () => {
    quitting = true;
    app.quit();
  });

  app.on("before-quit", () => {
    quitting = true;
  });

  app.on("will-quit", () => {
    // Never leave a global Escape hook installed after FRIDAY exits.
    try {
      globalShortcut.unregisterAll();
    } catch {
      /* nothing registered */
    }
  });

  // Never let a stray certificate error or crashed GPU take the overlay down.
  app.on("child-process-gone", (_event, details) => {
    if (details.type === "GPU") {
      // Chromium falls back to software WebGL; the shader is cheap enough.
      return;
    }
  });
}

export const __test = {
  STAGE_WIDTH,
  STAGE_HEIGHT,
  STAGE_MARGIN_BOTTOM,
  INSPECTOR_WIDTH,
  INSPECTOR_HEIGHT,
  INDEX_HTML,
  rendererUrl,
  stageGeometry,
  stagePosition,
};
