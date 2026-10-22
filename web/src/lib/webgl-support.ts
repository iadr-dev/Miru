/**
 * Whether this browser can create a WebGL context, so the 3D view is only offered where it can
 * run. Checked once on the client (never during render on the server). A throwaway canvas is
 * used and its context released at once.
 */
export function isWebGLAvailable(): boolean {
  if (typeof window === "undefined" || window.WebGLRenderingContext === undefined) {
    return false;
  }
  try {
    const canvas = document.createElement("canvas");
    const context = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
    if (!context) {
      return false;
    }
    context.getExtension("WEBGL_lose_context")?.loseContext();
    return true;
  } catch {
    return false;
  }
}
