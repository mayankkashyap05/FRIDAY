import { app, BrowserWindow, ipcMain, screen, session, shell } from "electron";
import type { WebPreferences } from "electron";
import * as path from "node:path";

/**
 * FRIDAY's Electron shell.
 *
 * Security posture (non-negotiable — the renderer runs third-party JS):
 *   contextIsolation: true · nodeIntegration: false · sandbox: true
 *   preload is the *only* bridge, and it exposes an explicit allow-list.
 *
 * Window posture: a transparent, frameless, always-on-top panel anchored to the
 * bottom-centre of the primary display, hidden from the taskbar and excluded
 * from screen capture, that ignores the mouse unless a surface asks for input.
 */

const APP_DIR = app.getAppPath();
const INDEX_HTML = path.join(APP_DIR, "dist", "index.html");
const DEV_URL = process.env.FRIDAY_DEV_URL || "";

const STAGE_WIDTH = 520;
const STAGE_HEIGHT = 640;
const STAGE_MARGIN_BOTTOM = 24;

let stageWindow: BrowserWindow | null = null;
let inspectorWindow: BrowserWindow | null = null;
let interactive = false;
let focusable = false;
let escapeArmed = false;
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

function stagePosition(): { x: number; y: number } {
  const display = screen.getPrimaryDisplay();
  const area = display.workArea;
  const scale = display.scaleFactor || 1;
  const width = Math.round(STAGE_WIDTH * scale);
  const height = Math.round(STAGE_HEIGHT * scale);
  return {
    x: Math.round(area.x + (area.width - width) / 2),
    y: Math.round(area.y + area.height - height - STAGE_MARGIN_BOTTOM * scale),
  };
}

function createStage(): BrowserWindow {
  const { x, y } = stagePosition();
  const win = new BrowserWindow({
    width: STAGE_WIDTH,
    height: STAGE_HEIGHT,
    x,
    y,
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
    if (!quitting) {
      quitting = true;
      app.quit();
    }
  });

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

function createInspector(tab: string): BrowserWindow {
  inspectorTab = tab || "camera";
  if (inspectorWindow && !inspectorWindow.isDestroyed()) {
    inspectorWindow.show();
    inspectorWindow.focus();
    inspectorWindow.webContents.send("friday:inspector-tab", inspectorTab);
    return inspectorWindow;
  }

  const win = new BrowserWindow({
    width: 900,
    height: 620,
    minWidth: 720,
    minHeight: 480,
    show: false,
    frame: false,
    title: "FRIDAY Inspector",
    backgroundColor: "#0b0b0f",
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

function applyStageInputMode(): void {
  const win = stageWindow;
  if (!win || win.isDestroyed()) return;
  // Ignore mouse *unless* a surface claimed input; always forward, so leaving a
  // surface (and hovering the next one) keeps working.
  win.setIgnoreMouseEvents(!interactive, { forward: true });
}

function registerIpc(): void {
  ipcMain.handle("friday:connection", () => connection());

  ipcMain.handle("friday:interactive", (_event, value: unknown) => {
    interactive = Boolean(value);
    applyStageInputMode();
  });

  ipcMain.handle("friday:focusable", (_event, value: unknown) => {
    focusable = Boolean(value);
    const win = stageWindow;
    if (!win || win.isDestroyed()) return;
    win.setFocusable(focusable);
    if (focusable && !win.isFocused()) {
      win.focus();
    } else if (!focusable) {
      win.blur();
    }
  });

  ipcMain.handle("friday:invoked", (_event, value: unknown) => {
    const win = stageWindow;
    if (!win || win.isDestroyed()) return;
    if (value) {
      if (!win.isVisible()) win.showInactive();
      win.setAlwaysOnTop(true, "screen-saver");
    }
  });

  ipcMain.handle("friday:open-inspector", (_event, tab: unknown) => {
    createInspector(typeof tab === "string" ? tab : "camera");
  });

  ipcMain.handle("friday:close-inspector", () => {
    if (inspectorWindow && !inspectorWindow.isDestroyed()) inspectorWindow.close();
    inspectorWindow = null;
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
  });

  app.on("window-all-closed", () => {
    quitting = true;
    app.quit();
  });

  app.on("before-quit", () => {
    quitting = true;
  });

  // Never let a stray certificate error or crashed GPU take the overlay down.
  app.on("child-process-gone", (_event, details) => {
    if (details.type === "GPU") {
      // Chromium falls back to software WebGL; the shader is cheap enough.
      return;
    }
  });
}

export const __test = { STAGE_WIDTH, STAGE_HEIGHT, INDEX_HTML, rendererUrl };
