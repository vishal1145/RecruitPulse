/**
 * helpers.js
 * Shared utility functions for the RecruitPulse Extension.
 */

import { STORAGE, ELEMENT_WAIT_TIMEOUT_MS, ELEMENT_POLL_INTERVAL_MS } from './constants.js';

// ─── Logging ───────────────────────────────────────────────────────────────

const LEVELS = { DEBUG: 0, INFO: 1, WARN: 2, ERROR: 3 };
const CURRENT_LEVEL = LEVELS.DEBUG;

export function log(level, message, data = null) {
    if (LEVELS[level] < CURRENT_LEVEL) return;
    const ts = new Date().toISOString();
    const prefix = `[RecruitPulse][${level}][${ts}]`;
    const args = data !== null ? [prefix, message, data] : [prefix, message];
    switch (level) {
        case 'ERROR': console.error(...args); break;
        case 'WARN': console.warn(...args); break;
        default: console.log(...args);
    }
}

// ─── Timing ────────────────────────────────────────────────────────────────

/**
 * Promise-based sleep.
 * @param {number} ms - Milliseconds to wait
 */
export function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

// ─── Retry ─────────────────────────────────────────────────────────────────

/**
 * Retry an async function with exponential backoff.
 * @param {Function} fn - Async function to retry
 * @param {number}   maxRetries - Max number of attempts
 * @param {number}   baseDelayMs - Base delay in ms (doubles each attempt)
 * @returns {Promise<*>} - Resolves with fn's return value
 */
export async function retry(fn, maxRetries = 3, baseDelayMs = 1000) {
    let lastError;
    for (let attempt = 1; attempt <= maxRetries; attempt++) {
        try {
            return await fn();
        } catch (err) {
            lastError = err;
            const delay = baseDelayMs * Math.pow(2, attempt - 1);
            log('WARN', `Attempt ${attempt}/${maxRetries} failed. Retrying in ${delay}ms…`, err.message);
            if (attempt < maxRetries) await sleep(delay);
        }
    }
    throw lastError;
}

// ─── Deduplication ─────────────────────────────────────────────────────────

/**
 * Generates a stable string hash from a job's title + company.
 * Used as a unique ID for deduplication.
 * @param {string} title
 * @param {string} company
 * @returns {string}
 */
export function generateJobId(title, company) {
    const raw = `${title.trim().toLowerCase()}::${company.trim().toLowerCase()}`;
    let hash = 0;
    for (let i = 0; i < raw.length; i++) {
        const char = raw.charCodeAt(i);
        hash = (hash << 5) - hash + char;
        hash |= 0; // Convert to 32-bit int
    }
    return `job_${Math.abs(hash)}`;
}

/**
 * Check if a job has already been processed.
 * @param {string} jobId
 * @returns {Promise<boolean>}
 */
export async function isAlreadyProcessed(jobId) {
    return new Promise(resolve => {
        chrome.storage.local.get([STORAGE.PROCESSED_IDS], result => {
            const ids = result[STORAGE.PROCESSED_IDS] || [];
            resolve(ids.includes(jobId));
        });
    });
}

/**
 * Mark a job as processed so it won't be reprocessed.
 * @param {string} jobId
 * @returns {Promise<void>}
 */
export async function markAsProcessed(jobId) {
    return new Promise(resolve => {
        chrome.storage.local.get([STORAGE.PROCESSED_IDS], result => {
            const ids = result[STORAGE.PROCESSED_IDS] || [];
            if (!ids.includes(jobId)) ids.push(jobId);
            chrome.storage.local.set({ [STORAGE.PROCESSED_IDS]: ids }, resolve);
        });
    });
}

/**
 * Clear all processed job IDs (reset deduplication state).
 * @returns {Promise<void>}
 */
export async function clearProcessedIds() {
    return new Promise(resolve => {
        chrome.storage.local.remove(STORAGE.PROCESSED_IDS, resolve);
    });
}

// ─── Stats ─────────────────────────────────────────────────────────────────

/**
 * Increment processed/failed counters in storage.
 * @param {'success'|'failed'} type
 */
export async function updateStats(type) {
    return new Promise(resolve => {
        chrome.storage.local.get([STORAGE.STATS], result => {
            const stats = result[STORAGE.STATS] || { success: 0, failed: 0, lastRun: null };
            if (type === 'success') stats.success++;
            if (type === 'failed') stats.failed++;
            stats.lastRun = new Date().toISOString();
            chrome.storage.local.set({ [STORAGE.STATS]: stats }, resolve);
        });
    });
}

/**
 * Retrieve current stats.
 * @returns {Promise<{success: number, failed: number, lastRun: string|null}>}
 */
export async function getStats() {
    return new Promise(resolve => {
        chrome.storage.local.get([STORAGE.STATS], result => {
            resolve(result[STORAGE.STATS] || { success: 0, failed: 0, lastRun: null });
        });
    });
}

// ─── DOM Utilities (content script context) ────────────────────────────────

/**
 * Wait for an element matching `selector` to appear in the DOM.
 * Returns the element or throws after timeout.
 * @param {string}  selector
 * @param {Element} [root=document]
 * @param {number}  [timeout]
 * @returns {Promise<Element>}
 */
export function waitForElement(selector, root = document, timeout = ELEMENT_WAIT_TIMEOUT_MS) {
    return new Promise((resolve, reject) => {
        const existing = root.querySelector(selector);
        if (existing) return resolve(existing);

        const observer = new MutationObserver(() => {
            const el = root.querySelector(selector);
            if (el) {
                observer.disconnect();
                clearTimeout(timer);
                resolve(el);
            }
        });

        observer.observe(root.nodeType === Node.ELEMENT_NODE ? root : document.body, {
            childList: true,
            subtree: true,
        });

        const timer = setTimeout(() => {
            observer.disconnect();
            reject(new Error(`Timeout waiting for selector: "${selector}" (${timeout}ms)`));
        }, timeout);
    });
}

/**
 * Wait for a predicate function to return true.
 * @param {Function} predicate
 * @param {number}   [timeout]
 * @param {number}   [interval]
 * @returns {Promise<void>}
 */
export async function waitForCondition(
    predicate,
    timeout = ELEMENT_WAIT_TIMEOUT_MS,
    interval = ELEMENT_POLL_INTERVAL_MS
) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
        if (await predicate()) return;
        await sleep(interval);
    }
    throw new Error(`Condition not met within ${timeout}ms`);
}

/**
 * Safely get trimmed text content from an element.
 * @param {Element|null} el
 * @returns {string}
 */
export function safeText(el) {
    return el ? el.textContent.trim() : '';
}

/**
 * Extract all email addresses from a block of text using regex.
 * @param {string} text
 * @returns {string[]}
 */
export function extractEmails(text) {
    const emailRegex = /[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g;
    const matches = text.match(emailRegex) || [];
    // Deduplicate
    return [...new Set(matches)];
}

/**
 * Determine the domain group of a URL for extractor routing.
 * @param {string} url
 * @returns {'linkedin'|'indeed'|'generic'}
 */
export function getDomainGroup(url) {
    try {
        const hostname = new URL(url).hostname.toLowerCase();
        if (hostname.includes('linkedin.com')) return 'linkedin';
        if (hostname.includes('indeed.com')) return 'indeed';
        return 'generic';
    } catch {
        return 'generic';
    }
}
