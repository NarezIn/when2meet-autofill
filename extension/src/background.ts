/**
 * Service worker: makes the toolbar button open the side panel.
 */

chrome.sidePanel.setPanelBehavior({ openPanelOnActionClick: true }).catch((err: unknown) => console.error(err));
