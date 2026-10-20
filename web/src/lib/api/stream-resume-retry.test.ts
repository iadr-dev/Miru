import { describe, expect, it, vi } from "vitest";
import type { AgentStreamFrame } from "@/lib/api/agent-stream";
import { streamWithResume } from "@/lib/api/stream-resume";

function frame(seq: number, kind = "message.delta", data: Record<string, unknown> = {}) {
  return { kind, envelope: { seq, ts: "t", data } } as AgentStreamFrame;
}

async function* scripted(
  frames: AgentStreamFrame[],
  then?: unknown,
): AsyncGenerator<AgentStreamFrame> {
  for (const f of frames) {
    yield f;
  }
  if (then !== undefined) {
    throw then;
  }
}

const noSleep = () => Promise.resolve();
const signal = new AbortController().signal;

async function collect(gen: AsyncGenerator<AgentStreamFrame>): Promise<number[]> {
  const seqs: number[] = [];
  for await (const f of gen) {
    seqs.push(f.envelope.seq);
  }
  return seqs;
}

describe("streamWithResume retry rules", () => {
  it("resumes with the thread known at start when no turn.start was read", async () => {
    const resume = vi.fn((_t: string, _l: number, _s: AbortSignal) =>
      scripted([frame(6, "turn.end")]),
    );
    const open = () => scripted([frame(5)], new TypeError("drop"));

    expect(
      await collect(
        streamWithResume(open, resume, signal, { threadIdAtStart: "start", sleep: noSleep }),
      ),
    ).toEqual([5, 6]);
    expect(resume).toHaveBeenCalledWith("start", 5, signal);
  });

  it("ignores a non-string thread_id on turn.start", async () => {
    const resume = vi.fn();
    const open = () => scripted([frame(0, "turn.start", { thread_id: 7 })]);

    expect(
      await collect(
        streamWithResume(open, resume, signal, { threadIdAtStart: null, sleep: noSleep }),
      ),
    ).toEqual([0]);
    expect(resume).not.toHaveBeenCalled();
  });

  it("ends quietly on a clean close before any frame", async () => {
    const resume = vi.fn();
    expect(
      await collect(
        streamWithResume(() => scripted([]), resume, signal, {
          threadIdAtStart: "th1",
          sleep: noSleep,
        }),
      ),
    ).toEqual([]);
    expect(resume).not.toHaveBeenCalled();
  });

  it("backs off linearly while re-attaches fail and resets after progress", async () => {
    const sleeps: number[] = [];
    const sleep = (ms: number): Promise<void> => {
      sleeps.push(ms);
      return Promise.resolve();
    };
    const boom = new TypeError("x");
    const rounds = [
      scripted([], boom),
      scripted([], boom),
      scripted([frame(2)], boom),
      scripted([frame(3, "turn.end")]),
    ];
    const resume = vi.fn(() => rounds.shift() as AsyncGenerator<AgentStreamFrame>);
    const open = () => scripted([frame(0, "turn.start", { thread_id: "th1" })], boom);

    await collect(
      streamWithResume(open, resume, signal, { threadIdAtStart: null, delayMs: 100, sleep }),
    );
    expect(sleeps).toEqual([100, 200, 300, 100]);
    expect(resume).toHaveBeenNthCalledWith(3, "th1", 0, signal);
    expect(resume).toHaveBeenNthCalledWith(4, "th1", 2, signal);
  });

  it("returns quietly when the attempt budget is zero and nothing failed", async () => {
    const resume = vi.fn();
    const open = () => scripted([frame(0, "turn.start", { thread_id: "th1" })]);
    expect(
      await collect(
        streamWithResume(open, resume, signal, {
          threadIdAtStart: null,
          maxAttempts: 0,
          sleep: noSleep,
        }),
      ),
    ).toEqual([0]);
    expect(resume).not.toHaveBeenCalled();
  });

  it("rethrows the failure when the signal is already aborted", async () => {
    const controller = new AbortController();
    controller.abort();
    const boom = new TypeError("x");
    await expect(
      collect(
        streamWithResume(
          () => scripted([frame(0, "turn.start", { thread_id: "th1" })], boom),
          vi.fn(),
          controller.signal,
          { threadIdAtStart: null, sleep: noSleep },
        ),
      ),
    ).rejects.toBe(boom);
  });
});
