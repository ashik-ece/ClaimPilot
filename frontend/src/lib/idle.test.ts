import { IdleTracker } from "./idle";
import type { KeyValueStore } from "./idle";

function memoryStore(): KeyValueStore & { data: Map<string, string> } {
  const data = new Map<string, string>();
  return { data, getItem: (k) => data.get(k) ?? null, setItem: (k, v) => void data.set(k, v) };
}

describe("IdleTracker", () => {
  it("reports the longest gap between activity", () => {
    let t = 0;
    const tracker = new IdleTracker("k", () => t);
    t = 10_000;
    tracker.activity();
    t = 15_000;
    tracker.activity();
    t = 16_000;
    expect(tracker.longestIdleSeconds()).toBe(10);
  });

  it("counts a gap that is still running", () => {
    let t = 0;
    const tracker = new IdleTracker("k", () => t);
    t = 400_000;
    expect(tracker.longestIdleSeconds()).toBe(400);
  });

  it("counts time away from the page across a reload", () => {
    const store = memoryStore();
    let t = 1_000;
    new IdleTracker("claim-1", () => t, store);
    t = 601_000; // ten minutes later the page is opened again
    const reopened = new IdleTracker("claim-1", () => t, store);
    expect(reopened.longestIdleSeconds()).toBe(600);
  });

  it("keeps separate state per claim", () => {
    const store = memoryStore();
    let t = 0;
    new IdleTracker("a", () => t, store);
    t = 300_000;
    expect(new IdleTracker("b", () => t, store).longestIdleSeconds()).toBe(0);
  });

  it("works when storage is unavailable or corrupt", () => {
    const broken: KeyValueStore = {
      getItem: () => {
        throw new Error("blocked");
      },
      setItem: () => {
        throw new Error("blocked");
      },
    };
    let t = 0;
    const tracker = new IdleTracker("k", () => t, broken);
    t = 5_000;
    expect(tracker.longestIdleSeconds()).toBe(5);
    const corrupt = memoryStore();
    corrupt.data.set("k", "{not json");
    expect(new IdleTracker("k", () => 0, corrupt).longestIdleSeconds()).toBe(0);
  });
});
