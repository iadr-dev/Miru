"use client";

/**
 * Prompts the browser's native "Share your screen" picker via `getDisplayMedia` — the Screen
 * Capture API, the only web platform mechanism for arbitrary screen capture (there is no
 * separate "take a screenshot" API; this is the same permission UI Chrome/Claude Desktop show
 * for screen-share flows). Once the user grants a source, grabs exactly one still frame off the
 * resulting video track by drawing it to an offscreen `<canvas>`, then immediately stops every
 * track — a live feed is never needed, only a single frame.
 *
 * Resolves to a data URL on success, or `null` if the user cancels/denies the picker or capture
 * otherwise fails — callers treat `null` as a silent no-op (this codebase doesn't toast on
 * non-fatal UI dismissals).
 */
export async function captureScreenshot(): Promise<string | null> {
  let stream: MediaStream | null = null;
  try {
    stream = await navigator.mediaDevices.getDisplayMedia({ video: true });
    const [track] = stream.getVideoTracks();
    if (!track) {
      return null;
    }
    const video = document.createElement("video");
    video.srcObject = stream;
    await video.play();
    // A freshly-played video may report its first frame a tick late; `requestVideoFrameCallback`
    // (falling back to a microtask) ensures the canvas draw below isn't racing an empty frame.
    await new Promise<void>((resolve) => {
      if ("requestVideoFrameCallback" in video) {
        (video as HTMLVideoElement).requestVideoFrameCallback(() => resolve());
      } else {
        resolve();
      }
    });
    const canvas = document.createElement("canvas");
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext("2d");
    if (!ctx || canvas.width === 0 || canvas.height === 0) {
      return null;
    }
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL("image/png");
  } catch {
    // User cancelled/denied the picker, or the platform doesn't support it — no error toast,
    // matching how this codebase treats other non-fatal UI dismissals.
    return null;
  } finally {
    stream?.getTracks().forEach((t) => {
      t.stop();
    });
  }
}
