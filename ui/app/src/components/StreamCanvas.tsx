import { useEffect, useRef } from "react";

export interface StreamCanvasProps {
  /** Raw JPEG bytes of a `<stream byte><jpeg>` frame, or null. */
  bytes: Uint8Array | null;
  className?: string;
  label?: string;
}

/**
 * Draws a JPEG stream frame. `createImageBitmap` decodes off the main paint
 * path, and the canvas adopts the frame's own aspect ratio so a 960 px vision
 * frame is never stretched.
 */
export function StreamCanvas({ bytes, className, label }: StreamCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);

  useEffect(() => {
    if (!bytes || bytes.byteLength < 4) return;
    let cancelled = false;
    const copy = new Uint8Array(bytes.byteLength);
    copy.set(bytes);
    const blob = new Blob([copy.buffer], { type: "image/jpeg" });

    const draw = async () => {
      try {
        const bitmap = await createImageBitmap(blob);
        if (cancelled) {
          bitmap.close();
          return;
        }
        const canvas = canvasRef.current;
        if (!canvas) {
          bitmap.close();
          return;
        }
        if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) {
          canvas.width = bitmap.width;
          canvas.height = bitmap.height;
        }
        const context = canvas.getContext("2d");
        context?.clearRect(0, 0, canvas.width, canvas.height);
        context?.drawImage(bitmap, 0, 0);
        bitmap.close();
      } catch {
        // A truncated frame is normal during shutdown; drop it silently and
        // wait for the next one (latest-frame-wins on the Python side too).
      }
    };

    void draw();
    return () => {
      cancelled = true;
    };
  }, [bytes]);

  return (
    <canvas
      ref={canvasRef}
      className={className}
      aria-label={label ?? "FRIDAY vision stream"}
      role="img"
    />
  );
}

export default StreamCanvas;
