/**
 * Tracks the longest gap with no user activity while a claim is open, so sessions interrupted
 * for more than a few minutes can be excluded from the timing analysis. The last-activity time is
 * kept in storage, so time spent away from the page (closed tab, reload) counts as a gap too.
 */

export interface KeyValueStore {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

interface State {
  last: number;
  longestMs: number;
}

export class IdleTracker {
  private state: State;

  constructor(
    private readonly key: string,
    private readonly now: () => number = Date.now,
    private readonly store: KeyValueStore | null = null,
  ) {
    this.state = this.load() ?? { last: this.now(), longestMs: 0 };
    // Opening the page is itself activity; the time since the stored one is a gap.
    this.activity();
  }

  /** Call on any user input (mouse, key, scroll, touch, tab becoming visible). */
  activity(): void {
    const t = this.now();
    this.state = { last: t, longestMs: Math.max(this.state.longestMs, t - this.state.last) };
    this.save();
  }

  /** Longest gap so far, in whole seconds, including the one still running. */
  longestIdleSeconds(): number {
    const running = this.now() - this.state.last;
    return Math.round(Math.max(this.state.longestMs, running) / 1000);
  }

  private load(): State | null {
    try {
      const raw = this.store?.getItem(this.key);
      if (!raw) return null;
      const parsed = JSON.parse(raw) as Partial<State>;
      return typeof parsed.last === "number" && typeof parsed.longestMs === "number"
        ? { last: parsed.last, longestMs: parsed.longestMs }
        : null;
    } catch {
      return null;
    }
  }

  private save(): void {
    try {
      this.store?.setItem(this.key, JSON.stringify(this.state));
    } catch {
      /* storage unavailable: the in-memory value still works within this page */
    }
  }
}
