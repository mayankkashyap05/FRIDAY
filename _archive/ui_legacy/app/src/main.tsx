import { StrictMode } from "react";
import { createRoot } from "react-dom/client";

import "./index.css";
import { InspectorApp } from "./components/InspectorApp";
import { StageApp } from "./components/StageApp";

/**
 * Two entrypoints share one bundle:
 *   #/            → the transparent Siri stage (bottom-centre overlay)
 *   #/inspector   → the detail window (own BrowserWindow, own socket)
 * `#/dev` isn't a route: the DevPreviewPanel lives inside the stage and only
 * appears when no WebSocket is attached.
 */
function readRoute(): { kind: "stage" | "inspector"; tab: string } {
  const hash = window.location.hash.replace(/^#/, "");
  const [path, query = ""] = hash.split("?");
  const params = new URLSearchParams(query);
  if (path.startsWith("/inspector")) {
    return { kind: "inspector", tab: params.get("tab") ?? "camera" };
  }
  return { kind: "stage", tab: "camera" };
}

// In Electron the page must stay fully transparent; in a plain browser (the dev
// bench) a dark desktop stand-in makes the glass and the halo legible.
if (!window.fridayAPI) document.documentElement.dataset.shell = "browser";

const route = readRoute();
const container = document.getElementById("root");
if (!container) throw new Error("#root is missing from index.html");

createRoot(container).render(
  <StrictMode>
    {route.kind === "inspector" ? <InspectorApp initialTab={route.tab} /> : <StageApp />}
  </StrictMode>,
);
