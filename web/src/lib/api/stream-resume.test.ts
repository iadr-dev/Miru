import { describe, expect, it, vi } from "vitest";
import { AgentStreamError, type AgentStreamFrame } from "@/lib/api/agent-stream";
import { streamWithResume } from "@/lib/api/stream-resume";

function frame(seq: number, kind = "message.delta", data: Record<string, unknown> = {}) {
  return { kind, envelope: { seq, ts: "t", data } } as AgentStreamFrame;
}

/** A stream that yields `frames`, then ends cleanly or throws `then`. */
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

describe("streamWithResume", () => {
  it("passes a complete stream through without resuming", async () => {
    const resume = vi.fn();
    const open = () =>
      scripted([frame(0, "turn.start", { thread_id: "th1" }), frame(1, "turn.end")]);

    expect(
      await collect(
        streamWithResume(open, resume, signal, { threadIdAtStart: null, sleep: noSleep }),
      ),
    ).toEqual([0, 1]);
    expect(resume).not.toHaveBeenCalled();
  });

  it("re-attaches after the last frame when the stream drops without turn.end", async () => {
    const resume = vi.fn((_thread: string, _last: number) =>
      scripted([frame(3), frame(4, "turn.end")]),
    );
    const open = () =>
      scripted(
        [frame(0, "turn.start", { thread_id: "th1" }), frame(1), frame(2)],
        new TypeError("network"),
      );

    const seqs = await collect(
      streamWithResume(open, resume, signal, { threadIdAtStart: null, sleep: noSleep }),
    );

    expect(seqs).toEqual([0, 1, 2, 3, 4]); // no gap, no repeat
    expect(resume).toHaveBeenCalledWith("th1", 2, signal);
  });

  it("re-attaches after a clean close that never sent turn.end", async () => {
    const resume = vi.fn(() => scripted([frame(2, "turn.end")]));
    const open = () => scripted([frame(0, "turn.start", { thread_id: "th1" }), frame(1)]);

    expect(
      await collect(
        streamWithResume(open, resume, signal, { threadIdAtStart: null, sleep: noSleep }),
      ),
    ).toEqual([0, 1, 2]);
  });

  it("keeps trying through repeated drops while each one makes progress", async () => {
    const rounds = [
      scripted([frame(2)], new TypeError("drop")),
      scripted([frame(3)], new TypeError("drop")),
      scripted([frame(4, "turn.end")]),
    ];
    const resume = vi.fn(() => rounds.shift() as AsyncGenerator<AgentStreamFrame>);
    const open = () =>
      scripted([frame(0, "turn.start", { thread_id: "th1" }), frame(1)], new TypeError("drop"));

    expect(
      await collect(
        streamWithResume(open, resume, signal, { threadIdAtStart: null, sleep: noSleep }),
      ),
    ).toEqual([0, 1, 2, 3, 4]);
  });

  it("gives up after the attempts are used and throws the last failure", async () => {
    const boom = new TypeError("offline");
    const resume = vi.fn(() => scripted([], boom));
    const open = () => scripted([frame(0, "turn.start", { thread_id: "th1" })], boom);

    await expect(
      collect(
        streamWithResume(open, resume, signal, {
          threadIdAtStart: null,
          maxAttempts: 3,
          sleep: noSleep,
        }),
      ),
    ).rejects.toBe(boom);
    expect(resume).toHaveBeenCalledTimes(3);
  });

  it("stops quietly when a re-attach finds nothing running", async () => {
    const resume = vi.fn(() => scripted([]));
    const open = () => scripted([frame(0, "turn.start", { thread_id: "th1" }), frame(1)]);

    expect(
      await collect(
        streamWithResume(open, resume, signal, { threadIdAtStart: null, sleep: noSleep }),
      ),
    ).toEqual([0, 1]);
    expect(resume).toHaveBeenCalledTimes(1);
  });

  it("does not resume before any frame arrived (nothing to re-attach to)", async () => {
    const boom = new TypeError("offline");
    const resume = vi.fn();

    await expect(
      collect(
        streamWithResume(() => scripted([], boom), resume, signal, {
          threadIdAtStart: "th1",
          sleep: noSleep,
        }),
      ),
    ).rejects.toBe(boom);
    expect(resume).not.toHaveBeenCalled();
  });

  it("throws a server refusal as it always did, without resuming", async () => {
    const refusal = new AgentStreamError("limit", 429, "chat_limit_reached");
    const resume = vi.fn();

    await expect(
      collect(
        streamWithResume(() => scripted([], refusal), resume, signal, {
          threadIdAtStart: "th1",
          sleep: noSleep,
        }),
      ),
    ).rejects.toBe(refusal);
    expect(resume).not.toHaveBeenCalled();
  });

  it("does not resume after the user stopped it", async () => {
    const controller = new AbortController();
    const resume = vi.fn();
    const open = () =>
      scripted([frame(0, "turn.start", { thread_id: "th1" })], new DOMException("x", "AbortError"));
    controller.abort();

    await expect(
      collect(
        streamWithResume(open, resume, controller.signal, {
          threadIdAtStart: null,
          sleep: noSleep,
        }),
      ),
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(resume).not.toHaveBeenCalled();
  });
});
