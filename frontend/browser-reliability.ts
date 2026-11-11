/** Forged browser lifecycle helpers, shared by desktop and web. */
export function retryableOnce(start: () => Promise<unknown>): () => Promise<void> {
  let pending: Promise<void> | null = null;
  return () => {
    pending ??= Promise.resolve().then(start).then(() => undefined).catch((error: unknown) => {
      pending = null;
      throw error;
    });
    return pending;
  };
}

/** Optional initialization must not keep the whole application blank. */
export function waitForStartup(tasks: unknown[], timeoutMs = 8000): Promise<void> {
  return new Promise((resolve) => {
    const timer = setTimeout(resolve, timeoutMs);
    void Promise.allSettled(tasks).then((results) => {
      clearTimeout(timer);
      for (const result of results) {
        if (result.status === "rejected") console.warn("Forged startup initialization failed", result.reason);
      }
      resolve();
    });
  });
}

function pause(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    const aborted = () => { clearTimeout(timer); signal.removeEventListener("abort", aborted); reject(signal.reason ?? new DOMException("Aborted", "AbortError")); };
    const timer = setTimeout(() => { signal.removeEventListener("abort", aborted); resolve(); }, ms);
    signal.addEventListener("abort", aborted, { once: true });
    if (signal.aborted) aborted();
  });
}

/** Retry only read-only page fetches, never submissions or browser actions. */
export async function retryBrowserRead<T>(load: () => Promise<T>, signal: AbortSignal, safe: boolean): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    signal.throwIfAborted();
    try { return await load(); }
    catch (error) {
      const status = error && typeof error === "object" && "status" in error ? error.status : null;
      const blocked = error && typeof error === "object" && "botCheck" in error && error.botCheck === true;
      if (blocked || signal.aborted || !safe || attempt >= 2 || !(error instanceof TypeError || status === 502 || status === 503 || status === 504)) throw error;
      await pause(attempt === 0 ? 250 : 750, signal);
    }
  }
}
