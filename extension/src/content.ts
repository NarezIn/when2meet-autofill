/**
 * Isolated-world content script on when2meet.com.
 *
 * - Relays side-panel requests to the MAIN-world script (which can see page globals).
 * - Draws low-confidence outlines on "your availability" cells. Outlines are a CSS
 *   class (outline, inset), so they take no layout space and never block clicks.
 */
import {
  PAGE_REQUEST_EVENT,
  PAGE_RESPONSE_EVENT,
  type PageRequest,
  type PanelRequest,
  type PanelResponse,
} from "./shared";

const OUTLINE_CLASS = "s2m-lowconf";
const PAGE_TIMEOUT_MS = 10 * 60 * 1000;

let outlined: number[] = [];

/**
 * Send a request to the MAIN-world script and wait for its answer.
 *
 * @param {PageRequest} request The request.
 * @returns {Promise<T>} The result, or rejects with the page script's error.
 */
function callPage<T>(request: PageRequest): Promise<T> {
  const id = crypto.randomUUID();
  return new Promise<T>((resolve, reject) => {
    const timer = setTimeout(() => {
      document.removeEventListener(PAGE_RESPONSE_EVENT, onResponse);
      reject(new Error("The page script didn't answer. Reload the When2meet tab."));
    }, PAGE_TIMEOUT_MS);

    /**
     * Match the response to our request id and settle the promise.
     *
     * @param {Event} event The s2m:response CustomEvent.
     * @returns {void}
     */
    function onResponse(event: Event): void {
      const msg = JSON.parse((event as CustomEvent<string>).detail) as {
        id: string;
        ok: boolean;
        result?: T;
        error?: string;
      };
      if (msg.id !== id) return;
      clearTimeout(timer);
      document.removeEventListener(PAGE_RESPONSE_EVENT, onResponse);
      if (msg.ok) resolve(msg.result as T);
      else reject(new Error(msg.error));
    }

    document.addEventListener(PAGE_RESPONSE_EVENT, onResponse);
    document.dispatchEvent(new CustomEvent(PAGE_REQUEST_EVENT, { detail: JSON.stringify({ id, request }) }));
  });
}

/**
 * Add the outline stylesheet to the page once.
 *
 * @returns {void}
 */
function ensureStyle(): void {
  if (document.getElementById("s2m-style")) return;
  const style = document.createElement("style");
  style.id = "s2m-style";
  style.textContent = `.${OUTLINE_CLASS} { outline: 2px dashed #f59e0b !important; outline-offset: -2px; }`;
  document.head.appendChild(style);
}

/**
 * Replace the set of outlined cells.
 *
 * @param {number[]} slots Slot timestamps to outline; empty clears all outlines.
 * @returns {void}
 */
function setOutlines(slots: number[]): void {
  ensureStyle();
  outlined = slots;
  paintOutlines();
}

/**
 * Apply the current outline set to the cells on the page (used again after the grid re-renders).
 *
 * @returns {void}
 */
function paintOutlines(): void {
  const wanted = new Set(outlined.map((ts) => `YouTime${ts}`));
  document.querySelectorAll(`.${OUTLINE_CLASS}`).forEach((el) => {
    if (!wanted.has(el.id)) el.classList.remove(OUTLINE_CLASS);
  });
  for (const id of wanted) document.getElementById(id)?.classList.add(OUTLINE_CLASS);
}

// When2meet re-renders the grid when the participant timezone changes; keep outlines.
new MutationObserver(() => {
  if (outlined.length && !document.querySelector(`.${OUTLINE_CLASS}`)) paintOutlines();
}).observe(document.body, { childList: true, subtree: true });

/**
 * Handle a request from the side panel.
 *
 * @param {PanelRequest} request The request.
 * @returns {Promise<unknown>} The result to send back.
 */
async function handle(request: PanelRequest): Promise<unknown> {
  switch (request.type) {
    case "getContext":
      return callPage({ type: "getContext" });
    case "apply":
      return callPage({ type: "apply", available: request.available });
    case "outline":
      setOutlines(request.slots);
      return null;
  }
}

chrome.runtime.onMessage.addListener((request: PanelRequest, _sender, sendResponse) => {
  handle(request).then(
    (result) => sendResponse({ ok: true, result } satisfies PanelResponse<unknown>),
    (err: unknown) =>
      sendResponse({ ok: false, error: err instanceof Error ? err.message : String(err) } satisfies PanelResponse<unknown>),
  );
  return true; // keep the channel open for the async response
});
