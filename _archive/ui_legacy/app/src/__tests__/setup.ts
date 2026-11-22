import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// jsdom ships neither WebGL2 nor createImageBitmap; every test that touches the
// orb supplies its own stub, so provide inert defaults here.
if (!("createImageBitmap" in window)) {
  Object.defineProperty(window, "createImageBitmap", {
    writable: true,
    value: vi.fn(async () => ({ width: 2, height: 2, close: () => undefined })),
  });
}

afterEach(() => {
  cleanup();
});
