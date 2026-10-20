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

type TurnProgress = { threadId: string | null; lastSeq: number };

/** Records the thread and the last `seq` a frame carries, so a re-attach can continue from it. */
function trackFrame(progress: TurnProgress, frame: AgentStreamFrame): void {
  progress.lastSeq = Math.max(progress.lastSeq, frame.envelope.seq);
  if (frame.kind === "turn.start" && typeof frame.envelope.data.thread_id === "string") {
    progress.threadId = frame.envelope.data.thread_id;
  }
}

type RoundInput = {
  readonly progress: TurnProgress;
  readonly attempt: number;
  readonly maxAttempts: number;
  readonly framesThisRound: number;
  readonly failure: unknown;
};

type NextStep =
  | { readonly kind: "stop" }
  | { readonly kind: "fail"; readonly error: unknown }
  | { readonly kind: "retry"; readonly attempt: number; readonly threadId: string };

/** Stop quietly, surface the failure, or re-attach (with the new attempt count). */
function decideNextStep(input: RoundInput): NextStep {
  const { progress, attempt, maxAttempts, framesThisRound, failure } = input;
  const { threadId } = progress;
  const giveUp: NextStep = failure === null ? { kind: "stop" } : { kind: "fail", error: failure };
  // The stream ended without `turn.end`. Nothing to re-attach to before the first frame.
  if (threadId === null || progress.lastSeq < 0) {
    return giveUp;
  }
  // A re-attach that brought nothing back means nothing is running there: stop, and let the
  // caller fall back to the saved thread.
  if (attempt > 0 && framesThisRound === 0 && failure === null) {
    return { kind: "stop" };
  }
  const nextAttempt = framesThisRound > 0 ? 1 : attempt + 1;
  if (nextAttempt > maxAttempts) {
    return giveUp;
  }
  return { kind: "retry", attempt: nextAttempt, threadId };
}

type RoundResult = {
  /** `turn.end` was read: the turn is complete. */
  readonly finished: boolean;
  readonly framesThisRound: number;
  /** What cut the stream short (null when it closed cleanly). */
  readonly failure: unknown;
};

/**
 * Pass one connection's frames through, noting how far the turn got. Resolves with how the
 * round ended; an abort or a server refusal is thrown as it always was.
 */
async function* readRound(
  source: AsyncGenerator<AgentStreamFrame>,
  progress: TurnProgress,
  signal: AbortSignal,
): AsyncGenerator<AgentStreamFrame, RoundResult> {
  let framesThisRound = 0;
  try {
    for await (const frame of source) {
      framesThisRound += 1;
      trackFrame(progress, frame);
      yield frame;
      if (frame.kind === "turn.end") {
        return { finished: true, framesThisRound, failure: null };
      }
    }
  } catch (err) {
    if (isAbort(err) || isRefusal(err) || signal.aborted) {
      throw err;
    }
    return { finished: false, framesThisRound, failure: err };
  }
  return { finished: false, framesThisRound, failure: null };
}

function resolveOptions(options: ResumeOptions): Required<Omit<ResumeOptions, "threadIdAtStart">> {
  return {
    maxAttempts: options.maxAttempts ?? MAX_RESUME_ATTEMPTS,
    delayMs: options.delayMs ?? BASE_DELAY_MS,
    sleep: options.sleep ?? abortableSleep,
  };
}

export async function* streamWithResume(
  open: Open,
  resume: Resume,
  signal: AbortSignal,
  options: ResumeOptions,
): AsyncGenerator<AgentStreamFrame> {
  const { maxAttempts, delayMs, sleep } = resolveOptions(options);

  const progress: TurnProgress = { threadId: options.threadIdAtStart, lastSeq: -1 };
  let source: AsyncGenerator<AgentStreamFrame> = open(signal);
  let attempt = 0;

  while (true) {
    const round = yield* readRound(source, progress, signal);
    if (round.finished) {
      return;
    }

    const next = decideNextStep({
      progress,
      attempt,
      maxAttempts,
      framesThisRound: round.framesThisRound,
      failure: round.failure,
    });
    if (next.kind === "stop") {
      return;
    }
    if (next.kind === "fail") {
      throw next.error;
    }
    attempt = next.attempt;
    await sleep(delayMs * attempt, signal);
    source = resume(next.threadId, progress.lastSeq, signal);
  }
}
