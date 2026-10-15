/**
 * Keeps a turn's answer coming when the connection carrying it drops.
 *
 * A phone that switches networks, a laptop that sleeps for a minute, a proxy that cuts an idle
 * connection: the answer is still being written on the server, but the stream the browser was
 * reading ends (cleanly, without `turn.end`) or throws. Without this the screen froze on a half
 * answer until a manual reload. Here the stream is re-attached with `Last-Event-ID` set to the last
 * frame applied, so the server replays only what was missed and the screen carries on.
 *
 * Resuming is only attempted once a frame of this turn has been read (so there is a turn to
 * re-attach to and a `seq` to continue from). A refusal from the server (a 4xx such as
 * `chat_limit_reached`, or an expired session) is not a dropped connection and is thrown as it
 * always was.
 */
import { AgentStreamError, type AgentStreamFrame } from "@/lib/api/agent-stream";

export const MAX_RESUME_ATTEMPTS = 4;
const BASE_DELAY_MS = 800;

type Open = (signal: AbortSignal) => AsyncGenerator<AgentStreamFrame>;
type Resume = (
  threadId: string,
  lastEventId: number,
  signal: AbortSignal,
) => AsyncGenerator<AgentStreamFrame>;

export type ResumeOptions = {
  /** The thread the turn runs on when it is already known (an existing chat). */
  readonly threadIdAtStart: string | null;
  readonly maxAttempts?: number;
  readonly delayMs?: number;
  /** Replaceable in tests. Rejects when `signal` aborts. */
  readonly sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
};

function isAbort(err: unknown): boolean {
  return err instanceof DOMException && err.name === "AbortError";
}

/** A failure that says "the server refused", not "the connection dropped". */
function isRefusal(err: unknown): boolean {
  return err instanceof AgentStreamError && err.status >= 400 && err.status < 500;
}

function abortableSleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal.aborted) {
      reject(new DOMException("aborted", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    const onAbort = (): void => {
      clearTimeout(timer);
      reject(new DOMException("aborted", "AbortError"));
    };
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export async function* streamWithResume(
  open: Open,
  resume: Resume,
  signal: AbortSignal,
  options: ResumeOptions,
): AsyncGenerator<AgentStreamFrame> {
  const maxAttempts = options.maxAttempts ?? MAX_RESUME_ATTEMPTS;
  const delayMs = options.delayMs ?? BASE_DELAY_MS;
  const sleep = options.sleep ?? abortableSleep;

  let threadId = options.threadIdAtStart;
  let lastSeq = -1;
  let source: AsyncGenerator<AgentStreamFrame> = open(signal);
  let attempt = 0;

  while (true) {
    let framesThisRound = 0;
    let failure: unknown = null;
    try {
      for await (const frame of source) {
        framesThisRound += 1;
        lastSeq = Math.max(lastSeq, frame.envelope.seq);
        if (frame.kind === "turn.start" && typeof frame.envelope.data.thread_id === "string") {
          threadId = frame.envelope.data.thread_id;
        }
        yield frame;
        if (frame.kind === "turn.end") {
          return;
        }
      }
    } catch (err) {
      if (isAbort(err) || isRefusal(err) || signal.aborted) {
        throw err;
      }
      failure = err;
    }

    // The stream ended without `turn.end`. Nothing to re-attach to before the first frame.
    if (threadId === null || lastSeq < 0) {
      if (failure !== null) {
        throw failure;
      }
      return;
    }
    // A re-attach that brought nothing back means nothing is running there: stop, and let the
    // caller fall back to the saved thread.
    if (attempt > 0 && framesThisRound === 0 && failure === null) {
      return;
    }
    attempt = framesThisRound > 0 ? 1 : attempt + 1;
    if (attempt > maxAttempts) {
      if (failure !== null) {
        throw failure;
      }
      return;
    }
    await sleep(delayMs * attempt, signal);
    source = resume(threadId, lastSeq, signal);
  }
}
