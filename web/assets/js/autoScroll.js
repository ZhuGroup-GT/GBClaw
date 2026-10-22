/**
 * Stick-to-bottom helpers for live-updating scrollable regions
 * (e.g. chat log, thinking-stream during a streaming run).
 *
 * Default behavior: auto-scroll to the bottom whenever content grows,
 * but yield to the user once they scroll up. When the user scrolls back
 * to the bottom, auto-scroll resumes on the next update.
 *
 * Two pieces:
 *   1. `installStickyScroll(container)` — bind once per container. It
 *      tracks whether the user has scrolled away from the bottom.
 *      Returns a cleanup function. The "user scrolled away" state
 *      defaults to `false` so the first content update auto-scrolls
 *      even before any user interaction.
 *   2. `stickToBottom(container)` — call after appending content.
 *      Auto-scrolls to the bottom only if the user has not scrolled
 *      away.
 *
 * The "at bottom" check uses a small pixel threshold so a few pixels of
 * slack (sub-pixel rounding, native scroll inertia) still counts as
 * pinned to the bottom.
 */

/** Pixels of slack tolerated before we treat the user as having scrolled away. */
export const STICK_THRESHOLD_PX = 24;

/** Internal: map container → { userScrolledAway: boolean }. */
const trackerState = new WeakMap();

/**
 * The document/viewport scroller used for the main chat page. Nested
 * regions (thinking streams, file preview) still pass their own element.
 */
export function getPageScroller() {
  return document.documentElement;
}

function isPageScroller(container) {
  return (
    container === document.documentElement ||
    container === document.body ||
    container === document
  );
}

function scrollMetrics(container) {
  if (isPageScroller(container)) {
    const el = document.documentElement;
    return {
      scrollTop: window.scrollY || el.scrollTop || document.body.scrollTop || 0,
      scrollHeight: Math.max(el.scrollHeight, document.body?.scrollHeight || 0),
      clientHeight: el.clientHeight,
    };
  }
  return {
    scrollTop: container.scrollTop,
    scrollHeight: container.scrollHeight,
    clientHeight: container.clientHeight,
  };
}

function scrollEventTarget(container) {
  return isPageScroller(container) ? window : container;
}

/**
 * Return true when the container is scrolled (or nearly) to the bottom,
 * or when it cannot scroll at all yet (e.g. before the first append).
 */
export function isAtBottom(container, threshold = STICK_THRESHOLD_PX) {
  if (!container) return true;
  const { scrollTop, scrollHeight, clientHeight } = scrollMetrics(container);
  const max = scrollHeight - clientHeight;
  if (max <= 0) return true;
  return max - scrollTop <= threshold;
}

/**
 * Get the state object for a container, creating it on first use so
 * the same reference is always returned. Mutating the object updates
 * what `stickToBottom` observes even if `installStickyScroll`'s
 * closure holds the same reference.
 */
function getState(container) {
  let state = trackerState.get(container);
  if (!state) {
    state = { userScrolledAway: false };
    trackerState.set(container, state);
  }
  return state;
}

/**
 * Force-scroll a container to the bottom. Use sparingly — only for
 * user-initiated actions (sending a message, opening a project, etc.)
 * where the user clearly expects the viewport to jump.
 *
 * Marks the container as "not user-scrolled-away" so subsequent
 * `stickToBottom` calls keep following content.
 */
export function scrollToBottom(container) {
  if (!container) return;
  setScrollTopInstant(container, scrollMetrics(container).scrollHeight);
  getState(container).userScrolledAway = false;
}

/**
 * Install a scroll listener that tracks whether the user has manually
 * scrolled the container away from the bottom. The initial state
 * defaults to "not scrolled away" (sticky=true), so the first content
 * append auto-scrolls even before any user interaction.
 *
 * Returns a cleanup function that detaches the listener.
 */
export function installStickyScroll(container, threshold = STICK_THRESHOLD_PX) {
  if (!container) return () => {};
  const state = getState(container);

  const onScroll = () => {
    state.userScrolledAway = !isAtBottom(container, threshold);
  };

  const target = scrollEventTarget(container);
  target.addEventListener("scroll", onScroll, { passive: true });
  return () => {
    target.removeEventListener("scroll", onScroll);
    trackerState.delete(container);
  };
}

/**
 * Auto-scroll a container to the bottom if the user has not scrolled
 * away. Call this after appending content. Safe to call before
 * `installStickyScroll` — defaults to "not scrolled away" so the first
 * call still auto-scrolls.
 *
 * Note: we trust the tracker (default sticky) over `isAtBottom`, so a
 * brand-new container that just started overflowing past the bottom
 * still gets pinned to the bottom — the user hadn't scrolled, the
 * content just grew past them.
 */
export function stickToBottom(container) {
  if (!container) return;
  const state = trackerState.get(container);
  if (state?.userScrolledAway) return;
  setScrollTopInstant(container, scrollMetrics(container).scrollHeight);
}

function setScrollTopInstant(container, value) {
  if (isPageScroller(container)) {
    const html = document.documentElement;
    const previousHtml = html.style.scrollBehavior;
    const previousBody = document.body?.style.scrollBehavior;
    html.style.scrollBehavior = "auto";
    if (document.body) document.body.style.scrollBehavior = "auto";
    html.scrollTop = value;
    if (document.body) document.body.scrollTop = value;
    window.scrollTo(0, value);
    html.style.scrollBehavior = previousHtml;
    if (document.body) document.body.style.scrollBehavior = previousBody;
    return;
  }
  const previousBehavior = container.style.scrollBehavior;
  container.style.scrollBehavior = "auto";
  container.scrollTop = value;
  container.style.scrollBehavior = previousBehavior;
}

export function captureScrollPosition(container) {
  if (!container) return { scrollTop: 0, userScrolledAway: false };
  const state = getState(container);
  return {
    scrollTop: scrollMetrics(container).scrollTop,
    userScrolledAway: state.userScrolledAway || !isAtBottom(container),
  };
}

export function restoreScrollPosition(container, snapshot) {
  if (!container || !snapshot?.userScrolledAway) {
    scrollToBottom(container);
    return;
  }
  setScrollTopInstant(container, snapshot.scrollTop);
  getState(container).userScrolledAway = true;
}
