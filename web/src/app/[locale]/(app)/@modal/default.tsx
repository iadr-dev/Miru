/** Parallel-route default for the `@modal` slot — nothing to render when no intercepted route
 * (e.g. `/settings/*`) is active. Required by Next.js for any parallel-route slot without its
 * own matching segment on the current path. */
export default function ModalDefault(): null {
  return null;
}
