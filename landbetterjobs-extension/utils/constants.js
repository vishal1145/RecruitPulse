/**
 * constants.js
 * Central configuration for RecruitPulse Extension.
 * Modify API_BASE_URL to point to your backend.
 */

export const API_BASE_URL = 'http://localhost:5000';
export const API_ENDPOINT = `${API_BASE_URL}/api/jobs`;

// Milliseconds to wait between processing each job
export const JOB_DELAY_MS = 5000;

// Retry configuration
export const MAX_RETRIES = 3;
export const RETRY_BASE_DELAY_MS = 1000;

// Timeout for waiting for popups/elements to appear (ms)
export const ELEMENT_WAIT_TIMEOUT_MS = 15000;
export const ELEMENT_POLL_INTERVAL_MS = 200;

// Tab load timeout (ms)
export const TAB_LOAD_TIMEOUT_MS = 30000;

// Message types (background ↔ content ↔ popup)
export const MSG = Object.freeze({
  // Popup → Background
  START_QUEUE: 'START_QUEUE',
  STOP_QUEUE: 'STOP_QUEUE',
  GET_STATUS: 'GET_STATUS',

  // Dashboard → Background
  JOBS_COLLECTED: 'JOBS_COLLECTED',
  JOB_POPUP_DATA: 'JOB_POPUP_DATA',
  EMAIL_DATA: 'EMAIL_DATA',

  // Background → Dashboard / Resume Builder
  COLLECT_JOBS: 'COLLECT_JOBS',
  CLICK_JOB_ACTION: 'CLICK_JOB_ACTION',
  EXTRACT_EMAIL_DATA: 'EXTRACT_EMAIL_DATA',
  BUILD_RESUME: 'BUILD_RESUME',
  DOWNLOAD_FILE: 'DOWNLOAD_FILE',

  // Resume Builder → Background
  RESUME_BUILT: 'RESUME_BUILT',

  // External tab → Background
  EXTERNAL_DATA: 'EXTERNAL_DATA',

  // Interception (Bypassing CSP)
  PREPARE_INTERCEPTION: 'PREPARE_INTERCEPTION',
  CLEANUP_INTERCEPTION: 'CLEANUP_INTERCEPTION',

  // Background → Popup
  STATUS_UPDATE: 'STATUS_UPDATE',
  QUEUE_STATE: 'QUEUE_STATE',
});

// Storage keys
export const STORAGE = Object.freeze({
  PROCESSED_IDS: 'recruitpulse_processed_ids',
  QUEUE_ACTIVE: 'recruitpulse_queue_active',
  STATS: 'recruitpulse_stats',
});

// CSS selectors for the LandBetterJobs dashboard
// Adjust these if the dashboard markup changes
export const SELECTORS = Object.freeze({
  // The heading that identifies the "Job Search AI Agent" section
  SECTION_HEADING: '[class*="job-search"], [class*="ai-agent"], h2, h3',
  SECTION_KEYWORD: 'Job Search AI Agent',

  // Job table rows
  JOB_TABLE: 'table, [class*="job-list"], [class*="jobs-table"]',
  JOB_ROW: 'tr[class*="job"], tr[data-job], tbody tr',

  // Action button inside a job row (the button that opens the popup)
  ACTION_BTN: 'button[class*="action"], button[class*="view"], button[class*="detail"], button',

  // Popup / modal that appears after clicking the action button
  POPUP_OVERLAY: '[class*="modal"], [class*="popup"], [class*="drawer"], [role="dialog"]',

  // Inside popup
  POPUP_TITLE: '[class*="job-title"], [class*="title"], h1, h2',
  POPUP_COMPANY: '[class*="company"], [class*="employer"]',
  POPUP_MANAGER: '[class*="hiring-manager"], [class*="manager"], [class*="contact"]',
  POPUP_DESC: '[class*="description"], [class*="summary"], p',
  POPUP_VIEW_LINK: 'a[href*="linkedin"], a[href*="indeed"], a[href*="apply"], a[target="_blank"]',
});

export const RESUME_BUILDER_SELECTORS = Object.freeze({
  CREATE_NEW_BTN: 'button', // Will filter by text "+ Create New"
  RESUME_COMBOBOX: 'button[role="combobox"]',
  DROPDOWN_OPTIONS: '[role="listbox"] [role="option"], [class*="select-content"] [role="option"]',
  COMPANY_INPUT: 'input#company',
  ROLE_INPUT: 'input#role',
  JD_TEXTAREA: 'textarea[placeholder*="Paste the complete job description"]',
  ANALYZE_JD_BTN: 'button', // Will filter by text "Analyze JD"
  LOADER: '[class*="spinner"], [class*="loading"], [role="status"]',
  MATCH_SCORE: '[class*="match-score"], [class*="score"]',
});
