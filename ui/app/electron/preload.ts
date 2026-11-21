import { contextBridge, ipcRenderer } from "electron";

/**
 * The complete, explicit bridge between the renderer and Electron.
 *
 * Nothing else is exposed: no `ipcRenderer`, no `require`, no `process`. Every
 * capability below is a named, auditable call — the renderer cannot reach the
 * filesystem, spawn a process, or open a window on its own.
 */

type WindowKind = "stage" | "inspector" | "dev";

function windowKind(): WindowKind {
  const flag = process.argv.find((arg) => arg.startsWith("--friday-window="));
  const kind = flag?.split("=")[1];
  if (kind === "inspector") return "inspector";
  if (kind === "dev") return "dev";
  return "stage";
}

function subscribe(channel: string, callback: (...args: unknown[]) => void): () => void {
  const listener = (_event: unknown, payload: unknown) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => {
    ipcRenderer.removeListener(channel, listener);
  };
}

const api = {
  windowKind: windowKind(),

  /** Resolved by the main process from FRIDAY_WS_URL / FRIDAY_WS_TOKEN. */
  getConnection: (): Promise<{ url: string; token: string } | null> =>
    ipcRenderer.invoke("friday:connection"),

  /** Click-through ↔ interactive. Reference-counted on the renderer side. */
  setInteractive: (interactive: boolean): void => {
    void ipcRenderer.invoke("friday:interactive", interactive);
  },

  /** The stage window may only take keyboard focus while summoned. */
  setFocusable: (focusable: boolean): void => {
    void ipcRenderer.invoke("friday:focusable", focusable);
  },

  setInvoked: (invoked: boolean): void => {
    void ipcRenderer.invoke("friday:invoked", invoked);
  },

  focusStage: (): void => {
    void ipcRenderer.invoke("friday:focus-stage");
  },

  openInspector: (tab: string): void => {
    void ipcRenderer.invoke("friday:open-inspector", tab);
  },

  closeInspector: (): void => {
    void ipcRenderer.invoke("friday:close-inspector");
  },

  setInspectorTab: (tab: string): void => {
    void ipcRenderer.invoke("friday:inspector-tab", tab);
  },

  setEscapeArmed: (armed: boolean): void => {
    void ipcRenderer.invoke("friday:escape-armed", armed);
  },

  quitApp: (): void => {
    void ipcRenderer.invoke("friday:quit");
  },

  onEscape: (callback: () => void): (() => void) => subscribe("friday:escape", callback),

  onInspectorTab: (callback: (tab: string) => void): (() => void) =>
    subscribe("friday:inspector-tab", (payload) => callback(String(payload ?? ""))),

  onInspectorClosed: (callback: () => void): (() => void) =>
    subscribe("friday:inspector-closed", callback),
};

contextBridge.exposeInMainWorld("fridayAPI", api);

export type FridayApi = typeof api;
