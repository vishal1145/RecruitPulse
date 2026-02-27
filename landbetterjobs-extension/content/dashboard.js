/**
 * content/dashboard.js
 */

const ALLOW_REVIEWED_FOR_TESTING = false;

const MSG = {
    // Popup <-> Background
    START_QUEUE: 'START_QUEUE',
    STOP_QUEUE: 'STOP_QUEUE',
    GET_STATUS: 'GET_STATUS',

    // Dashboard Content Script <-> Background
    COLLECT_JOBS: 'COLLECT_JOBS',
    JOBS_COLLECTED: 'JOBS_COLLECTED',
    CLICK_JOB_ACTION: 'CLICK_JOB_ACTION',
    JOB_POPUP_DATA: 'JOB_POPUP_DATA',
    EXTRACT_EMAIL_DATA: 'EXTRACT_EMAIL_DATA',
    EMAIL_DATA: 'EMAIL_DATA',
    EXTRACT_OUTREACH_DATA: 'EXTRACT_OUTREACH_DATA',
    OUTREACH_DATA: 'OUTREACH_DATA',

    // External tab <-> Background
    EXTERNAL_DATA: 'EXTERNAL_DATA',

    // Interception (Bypassing CSP)
    PREPARE_INTERCEPTION: 'PREPARE_INTERCEPTION',
    CLEANUP_INTERCEPTION: 'CLEANUP_INTERCEPTION',

    // Background <-> Popup
    STATUS_UPDATE: 'STATUS_UPDATE',
    QUEUE_STATE: 'QUEUE_STATE',
};

// ─── Logger ──────────────────────────────────────────────────────────────────

function log(level, message, data = null) {
    const timestamp = new Date().toISOString();
    const prefix = `[RecruitPulse][Dashboard][${level}][${timestamp}]`;
    if (data) {
        console.log(prefix, message, data);
    } else {
        console.log(prefix, message);
    }
}

// ─── Utils ───────────────────────────────────────────────────────────────────

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

/**
 * Polls for a condition to be true.
 */
async function waitForCondition(fn, timeout = 5000) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
        if (fn()) return true;
        await sleep(200);
    }
    throw new Error("Condition timeout");
}

/**
 * Polls for a condition to be true.
 */
async function waitForCondition(fn, timeout = 5000) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
        if (fn()) return true;
        await sleep(200);
    }
    throw new Error("Condition timeout");
}

function waitForElement(selector, root = document, timeout = 3000) {
    return new Promise((resolve) => {
        const el = root.querySelector(selector);
        if (el) return resolve(el);

        const observer = new MutationObserver((mutations, obs) => {
            const el = root.querySelector(selector);
            if (el) {
                obs.disconnect();
                resolve(el);
            }
        });

        observer.observe(root, {
            childList: true,
            subtree: true
        });

        setTimeout(() => {
            observer.disconnect();
            resolve(null);
        }, timeout);
    });
}

/**
 * Wait for *any* of the provided selectors to appear.
 */
function waitForAny(selectors, root = document, timeout = 3000) {
    return new Promise((resolve) => {
        for (const sel of selectors) {
            const el = root.querySelector(sel);
            if (el) return resolve(el);
        }

        const observer = new MutationObserver((mutations, obs) => {
            for (const sel of selectors) {
                const el = root.querySelector(sel);
                if (el) {
                    obs.disconnect();
                    resolve(el);
                    return;
                }
            }
        });

        observer.observe(root, { childList: true, subtree: true });

        setTimeout(() => {
            observer.disconnect();
            resolve(null);
        }, timeout);
    });
}

function safeText(el) {
    return el ? (el.innerText || el.textContent || '').trim() : '';
}

function findFirst(selectors, root = document) {
    for (const sel of selectors) {
        const el = root.querySelector(sel);
        if (el) return el;
    }
    return null;
}

/**
 * Simulates a real user click by dispatching mouse events.
 */
function simulateRealClick(element) {
    if (!element) return;
    const rect = element.getBoundingClientRect();
    const x = rect.left + rect.width / 2;
    const y = rect.top + rect.height / 2;

    ["mouseover", "mousedown", "mouseup", "click"].forEach(type => {
        element.dispatchEvent(
            new MouseEvent(type, {
                view: window,
                bubbles: true,
                cancelable: true,
                clientX: x,
                clientY: y
            })
        );
    });
}

// ─── Main Script ─────────────────────────────────────────────────────────────

log('INFO', 'Dashboard content script initialized. Waiting for commands from background…');

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    switch (msg.type) {
        case MSG.COLLECT_JOBS:
            collectJobRows()
                .then(jobs => {
                    log('INFO', `Found ${jobs.length} job row(s)`);
                    chrome.runtime.sendMessage({
                        type: MSG.JOBS_COLLECTED,
                        jobs
                    });
                    sendResponse({ ok: true, count: jobs.length });
                })
                .catch(err => {
                    log('ERROR', 'Failed to collect jobs', err);
                    sendResponse({ ok: false, error: err.message });
                });
            return true; // Keep channel open

        case MSG.CLICK_JOB_ACTION:
            handleJobClick(msg.rowIndex)
                .then(data => {
                    log('INFO', 'Extracted initial popup data', data);
                    chrome.runtime.sendMessage({
                        type: MSG.JOB_POPUP_DATA,
                        data
                    });
                    sendResponse({ ok: true });
                })
                .catch(err => {
                    log('ERROR', `Failed to process row ${msg.rowIndex}`, err);
                    chrome.runtime.sendMessage({
                        type: MSG.JOB_POPUP_DATA,
                        data: { error: err.message }
                    });
                    sendResponse({ ok: false, error: err.message });
                });
            return true;

        case MSG.EXTRACT_EMAIL_DATA:
            handleEmailExtraction()
                .then(data => {
                    log('INFO', 'Extracted email data', data);
                    chrome.runtime.sendMessage({
                        type: MSG.EMAIL_DATA,
                        data
                    });
                    sendResponse({ ok: true });
                })
                .catch(err => {
                    log('ERROR', 'Failed to extract email data', err);
                    chrome.runtime.sendMessage({
                        type: MSG.EMAIL_DATA,
                        data: { error: err.message }
                    });
                    sendResponse({ ok: false, error: err.message });
                });
            return true;

        case MSG.EXTRACT_OUTREACH_DATA:
            handleOutreachExtraction()
                .then(data => {
                    log('INFO', 'Extracted outreach data', data);
                    chrome.runtime.sendMessage({
                        type: MSG.OUTREACH_DATA,
                        data
                    });
                    sendResponse({ ok: true });
                })
                .catch(err => {
                    log('ERROR', 'Failed to extract outreach data', err);
                    chrome.runtime.sendMessage({
                        type: MSG.OUTREACH_DATA,
                        data: { error: err.message }
                    });
                    sendResponse({ ok: false, error: err.message });
                });
            return true;

        default:
            break;
    }
});

// ─── Job Collection ──────────────────────────────────────────────────────────

async function collectJobRows() {
    // Selector for the job table rows
    // Based on inspection: rows are inside a table body
    const rows = Array.from(document.querySelectorAll('tbody tr'));

    if (rows.length === 0) {
        log('WARN', 'No job rows found in tbody. Trying generic tr...');
        // Fallback
        const allRows = Array.from(document.querySelectorAll('tr'));
        // Filter out header rows (usually th)
        return allRows.filter(r => r.querySelector('td')).map(parseRow);
    }

    // Parse all rows
    const parsedJobs = rows.map((row, index) => parseRow(row, index));

    // Filter: Process ONLY "New" jobs (unless testing flag is ON)
    const newJobs = parsedJobs.filter(job => {
        const status = job.status ? job.status.trim() : "";
        const isNew = status === 'New';

        if (isNew) return true;

        if (ALLOW_REVIEWED_FOR_TESTING) {
            log('WARN', `⚠ Testing Mode: Processing Reviewed Job → ${job.title} [Status: "${status}"]`);
            return true;
        }

        log('INFO', `Skipping (Not New): ${job.title} [Status: "${status}"]`);
        return false;
    });

    return newJobs;
}

function parseRow(row, index) {
    const cells = Array.from(row.querySelectorAll('td'));

    // Column Mapping based on User's verified screenshots:
    // 0: Status (Badge)
    // 1: Job & Company (Two lines)
    // 2: Hiring Manager
    // 3: Action (External Link SVG) - DO NOT used for scraping text, but is the link

    let status = '';
    let title = '';
    let company = '';
    let hiringManager = '';

    // Status
    if (cells[0]) {
        status = safeText(cells[0]).trim();
        // log('DEBUG', `Row ${index} Status Raw: "${status}"`); // Debugging
    }

    // Job & Company
    if (cells[1]) {
        const text = cells[1].innerText || '';
        const parts = text.split('\n').map(s => s.trim()).filter(Boolean);
        if (parts.length > 0) title = parts[0];
        if (parts.length > 1) company = parts[1];
    }

    // Hiring Manager
    if (cells[2]) {
        hiringManager = safeText(cells[2]);
    }

    return {
        index, // Store original index for clicking later
        status,
        title,
        company,
        hiringManager
        // We don't extract the URL here because it requires a click -> popup
    };
}

// ─── Action Handling ─────────────────────────────────────────────────────────

async function handleJobClick(rowIndex) {
    // Note: We no longer click here. The background script injects a Main World script
    // to simulate a real user click (scroll + hover + mousedown/up + click).
    // This function now simply waits for the result of that action (the popup).

    log('INFO', `Waiting for popup data for row ${rowIndex}...`);

    // Wait for the popup/drawer to appear
    const popup = await waitForPopup();
    if (!popup) {
        throw new Error('Popup did not appear after interaction');
    }

    // Extract details
    const data = await extractFromPopup(popup);

    // [MODIFIED] DO NOT close popup here yet. 
    // We will extract email data later from the same popup.
    // await closePopup(popup);

    return data;
}

/**
 * Switches to the Email tab in the popup and extracts the template.
 */
async function handleEmailExtraction() {
    log('INFO', 'Starting email template extraction...');

    // 1. Find the dialog strictly
    const dialog = document.querySelector('[role="dialog"]');
    if (!dialog) throw new Error('Popup/Dialog not found for email extraction');

    // 2. Find and click Email tab
    const emailTabBtn = dialog.querySelector('[role="tab"][aria-controls*="email"]');
    if (!emailTabBtn) {
        log('WARN', 'Email tab button not found inside dialog');
        return { emailSubject: '', emailBody: '' };
    }

    log('INFO', 'Switching to Email tab...');
    emailTabBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
    await sleep(300);
    simulateRealClick(emailTabBtn);

    // 3. WAIT until tab becomes active (strict check)
    log('INFO', 'Waiting for tab activation state...');
    try {
        await waitForCondition(() =>
            emailTabBtn.getAttribute("data-state") === "active",
            5000
        );
        log('INFO', 'Email tab marked as "active"');
    } catch (err) {
        log('WARN', 'Email tab activation timeout, proceeding anyway...');
    }

    // 4. Wait for Content Render inside dialog
    const textarea = await waitForElement('textarea', dialog, 5000);
    if (!textarea) {
        log('WARN', 'Email textareas did not appear in time');
        await closePopup(dialog);
        return { emailSubject: '', emailBody: '' };
    }

    await sleep(800); // UI breathing room

    // 5. Extract Subject and Body (Scoped to dialog only)
    const textareas = Array.from(dialog.querySelectorAll('textarea'));

    // Subject = index 0, Body = index 1 (usually)
    const emailSubject = textareas[0] ? textareas[0].value.trim() : '';
    const emailBody = textareas[1] ? textareas[1].value.trim() : '';

    if (!emailSubject && !emailBody) {
        log('WARN', 'Extracted email fields are empty!');
    } else {
        log('INFO', `Extracted Email Data Successfully: ${emailSubject.slice(0, 30)}...`);
    }

    // NOTE: Do NOT close popup here — outreach extraction needs it open
    return { emailSubject, emailBody };
}

/**
 * Switches to the Outreach tab in the popup and extracts initial + follow-up messages.
 */
async function handleOutreachExtraction() {
    log('INFO', 'Starting outreach data extraction...');

    // 1. Find the dialog strictly
    const dialog = document.querySelector('[role="dialog"]');
    if (!dialog) throw new Error('Popup/Dialog not found for outreach extraction');

    // 2. Find and click Outreach tab
    let outreachTabBtn = dialog.querySelector('[role="tab"][aria-controls*="outreach"]');

    // Fallback: search by text content
    if (!outreachTabBtn) {
        const allTabs = Array.from(dialog.querySelectorAll('[role="tab"]'));
        outreachTabBtn = allTabs.find(tab =>
            (tab.innerText || tab.textContent || '').toLowerCase().includes('outreach')
        );
    }

    if (!outreachTabBtn) {
        log('WARN', 'Outreach tab button not found inside dialog');
        return { initialMessage: '', followUpMessage1: '' };
    }

    log('INFO', 'Switching to Outreach tab...');
    outreachTabBtn.scrollIntoView({ behavior: 'smooth', block: 'center' });
    await sleep(300);
    simulateRealClick(outreachTabBtn);

    // 3. Wait until tab becomes active
    log('INFO', 'Waiting for Outreach tab activation...');
    try {
        await waitForCondition(() =>
            outreachTabBtn.getAttribute("data-state") === "active",
            5000
        );
        log('INFO', 'Outreach tab marked as "active"');
    } catch (err) {
        log('WARN', 'Outreach tab activation timeout, proceeding anyway...');
    }

    // 4. Wait for content render inside dialog
    const textarea = await waitForElement('textarea', dialog, 5000);
    if (!textarea) {
        log('WARN', 'Outreach textareas did not appear in time');
        return { initialMessage: '', followUpMessage1: '' };
    }

    await sleep(800); // UI breathing room

    // 5. Extract messages using the actual DOM structure:
    //    Each message block is: <div class="space-y-2">
    //      <div class="flex items-center justify-between">
    //        <label class="text-sm font-medium">Label Text</label>
    //      </div>
    //      <div class="relative">
    //        <textarea>...</textarea>
    //      </div>
    //    </div>
    let initialMessage = '';
    let followUpMessage1 = '';

    const messageBlocks = Array.from(dialog.querySelectorAll('div.space-y-2'));
    log('INFO', `Found ${messageBlocks.length} space-y-2 message blocks`);

    for (const block of messageBlocks) {
        const label = block.querySelector('label.text-sm.font-medium');
        if (!label) continue;

        const labelText = (label.innerText || label.textContent || '').trim().toLowerCase();
        const ta = block.querySelector('textarea');
        if (!ta) continue;

        log('INFO', `Found label: "${labelText}" with textarea (${ta.value.length} chars)`);

        if (labelText === 'initial message') {
            initialMessage = ta.value.trim();
            log('INFO', `Extracted Initial Message (${initialMessage.length} chars)`);
        } else if (labelText === 'follow-up message 1') {
            followUpMessage1 = ta.value.trim();
            log('INFO', `Extracted Follow-up Message 1 (${followUpMessage1.length} chars)`);
        }
    }

    // Fallback: if blocks didn't work, try by label text matching
    if (!initialMessage && !followUpMessage1) {
        const allLabels = Array.from(dialog.querySelectorAll('label'));
        for (const label of allLabels) {
            const labelText = (label.innerText || '').trim().toLowerCase();
            // Walk up to parent space-y-2, then find textarea
            const parentBlock = label.closest('.space-y-2') || label.parentElement?.parentElement;
            const ta = parentBlock ? parentBlock.querySelector('textarea') : null;
            if (!ta) continue;

            if (labelText.includes('initial message')) {
                initialMessage = ta.value.trim();
            } else if (labelText.includes('follow-up message 1')) {
                followUpMessage1 = ta.value.trim();
            }
        }
        log('INFO', `Fallback label scan: initial=${initialMessage.length}chars, followup=${followUpMessage1.length}chars`);
    }

    // 6. Close the popup after extraction
    await closePopup(dialog);
    await sleep(500);

    return { initialMessage, followUpMessage1 };
}

/**
 * Find the clickable action element inside a job row.
 * Prioritize Job/Company cell (cells[1]) to open side panel.
 */
function findActionButton(row) {
    const cells = Array.from(row.querySelectorAll('td'));

    // Click cells[1] (Job & Company) – safest
    if (cells[1]) return cells[1];

    // Fallback: click cells[0] (Status badge)
    if (cells[0]) return cells[0];

    // Final fallback: click the row itself
    return row;
}

const POPUP_WAIT_MS = 5000;

async function waitForPopup() {
    const popupSelectors = [
        // Radix / Shadcn UI — most likely on this site
        '[data-state="open"][role="dialog"]',
        '[data-radix-dialog-content]',
        '[data-radix-sheet-content]',
        '[data-radix-popper-content-wrapper]',

        // Standard ARIA
        '[role="dialog"]',

        // Generic
        '[class*="sheet"]',
        '[class*="Sheet"]',
        '[class*="drawer"]',
        '[class*="Drawer"]',
        '[class*="modal"]',
        '[class*="sidepanel"]',
        '[class*="detail-panel"]',
    ];
    return waitForAny(popupSelectors, document, POPUP_WAIT_MS);
}

/**
 * Extract structured fields from the open popup/dialog.
 */
async function extractFromPopup(popup) {
    log('INFO', 'Extracting data from popup', popup.className);

    // ── Wait for Content to Load ─────────────────────────────────────────────
    // The popup opens initially with "Loading..." or skeleton content.
    // We must wait until real content appears.

    let bodyText = '';
    let foundContent = false;

    // Retry for up to 5 seconds
    for (let i = 0; i < 25; i++) { // 25 * 200ms = 5000ms
        bodyText = popup.innerText || popup.textContent || '';

        // If it says "Loading...", keep waiting
        if (bodyText.includes('Loading...')) {
            await sleep(200);
            continue;
        }

        // Look for key headers or buttons that indicate readiness
        const hasHiring = /Hiring\s*:/i.test(bodyText);
        const hasManager = /Hiring\s*Manager/i.test(bodyText);
        const hasViewPost = /View\s*Full\s*Post/i.test(bodyText);

        if (hasHiring || hasManager || hasViewPost) {
            foundContent = true;
            break; // Content loaded!
        }

        await sleep(200);
    }

    if (!foundContent) {
        log('WARN', 'Popup content timeout or still loading', bodyText.slice(0, 50));
    } else {
        log('INFO', 'Popup content loaded');
    }

    const bodyLines = bodyText.split('\n').map(l => l.trim()).filter(Boolean);

    // ── 2. Parse emoji-prefixed fields ──────────────────────────────────────
    function extractField(text, ...patterns) {
        for (const pattern of patterns) {
            const m = text.match(pattern);
            if (m && m[1]) return m[1].trim();
        }
        return '';
    }

    // ── 2. Parse Structured Fields ──────────────────────────────────────────

    // Title: "🚀 Hiring: ..."
    let title = extractField(bodyText,
        /🚀\s*(?:Hiring|Job)\s*[:]\s*([^\n📍✅🚀💰]+)/i,
    ) || safeText(findFirst(['h1', 'h2', 'h3', '[class*="title"]'], popup));

    // Hiring Manager
    // DOM: <h3 class="text-lg font-semibold mb-3">Hiring Manager</h3>
    //       <div class="bg-muted/50 rounded-lg p-4">
    //         <h4 class="font-semibold text-lg">Name's</h4>
    //         <button>View Profile</button>
    let hiringManager = '';
    let hiringManagerProfileUrl = '';

    // Step 1: Find the h3 that says "Hiring Manager"
    const allH3s = Array.from(popup.querySelectorAll('h3'));
    const hmH3 = allH3s.find(h3 => {
        const txt = (h3.innerText || h3.textContent || '').trim();
        return txt === 'Hiring Manager';
    });

    if (hmH3) {
        // Step 2: The name is inside the next sibling container (bg-muted card)
        const hmContainer = hmH3.nextElementSibling;
        if (hmContainer) {
            // Look for h4 with the name
            const nameEl = hmContainer.querySelector('h4.font-semibold');
            if (nameEl) {
                // Strip possessive 's — handle both ASCII ' and Unicode \u2019 (right single quote)
                hiringManager = safeText(nameEl)
                    .replace(/[\u2018\u2019']s$/i, '')
                    .trim();
                log('INFO', `Hiring Manager Name: "${hiringManager}"`);
            }

            // Look for "View Profile" button/link
            const allBtnsAndLinks = Array.from(hmContainer.querySelectorAll('button, a'));
            const viewProfileBtn = allBtnsAndLinks.find(el =>
                (el.innerText || '').toLowerCase().includes('view profile')
            );
            if (viewProfileBtn) {
                // Direct href (if it's an <a>)
                hiringManagerProfileUrl = viewProfileBtn.href || viewProfileBtn.getAttribute('href') || '';

                // Check common data attributes
                if (!hiringManagerProfileUrl) {
                    const attrs = ['data-url', 'data-href', 'url', 'href', 'data-link'];
                    for (const attr of attrs) {
                        const val = viewProfileBtn.getAttribute(attr);
                        if (val && val.startsWith('http')) {
                            hiringManagerProfileUrl = val;
                            break;
                        }
                    }
                }

                // If still no URL, use MAIN world interception (same as View Full Post)
                if (!hiringManagerProfileUrl) {
                    log('INFO', 'View Profile has no href — using MAIN world interception...');

                    // 1. Tell background to prep the MAIN world interceptor
                    await chrome.runtime.sendMessage({ type: MSG.PREPARE_INTERCEPTION });

                    // 2. Click the button
                    log('INFO', 'Clicking "View Profile" button...');
                    viewProfileBtn.click();

                    // 3. Poll for result (DOM-based bridge via data-rp-url)
                    for (let i = 0; i < 20; i++) {
                        await sleep(100);
                        hiringManagerProfileUrl = document.body.getAttribute('data-rp-url') || '';
                        if (hiringManagerProfileUrl) break;
                    }

                    // 4. Tell background to cleanup
                    await chrome.runtime.sendMessage({ type: MSG.CLEANUP_INTERCEPTION });

                    log('INFO', hiringManagerProfileUrl
                        ? `View Profile intercepted → ${hiringManagerProfileUrl}`
                        : 'View Profile window.open NOT called by button');
                }

                log('INFO', `Hiring Manager Profile URL: "${hiringManagerProfileUrl}"`);
            }
        }
    }

    // Fallback: scan all divs (old approach)
    if (!hiringManager) {
        const allDivs = Array.from(popup.querySelectorAll('div'));
        const managerHeaderIdx = allDivs.findIndex(d => {
            const txt = (d.innerText || '').trim();
            return txt === 'Hiring Manager';
        });
        if (managerHeaderIdx !== -1) {
            for (let i = 1; i <= 5; i++) {
                const candidate = allDivs[managerHeaderIdx + i];
                if (candidate) {
                    const h4 = candidate.querySelector('h4');
                    if (h4) {
                        hiringManager = safeText(h4)
                            .replace(/[\u2018\u2019']s$/i, '')
                            .trim();
                        break;
                    }
                }
            }
        }
    }

    // Company
    // Strategy: Often the second line or near the title. If we can't find it, use "Confidential"
    // const companyEl = findFirst(['[class*="company"]', '[class*="employer"]'], popup);
    // let company = safeText(companyEl);
    // if (!company) {
    //     // Try to parse from title line if format is "Role - Company"
    //     if (title.includes(' – ')) {
    //         const parts = title.split(' – ');
    //         if (parts.length > 1) company = parts[1].trim();
    //     } else if (title.includes(' at ')) {
    //         const parts = title.split(' at ');
    //         if (parts.length > 1) company = parts[1].trim();
    //     }
    // }


    // Extract Title from popup header
    const headerContainer = popup.querySelector('div.flex-1');

    // let title = '';
    let company = '';

    if (headerContainer) {
        // const titleEl = headerContainer.querySelector('h2');
        const companyEl = headerContainer.querySelector('p.text-muted-foreground');

        // title = safeText(titleEl);
        company = safeText(companyEl);
    }

    // Short description
    let shortDescription = '';
    // Capture text between "Experience:" or "Responsibilities:" and "View Full Post"
    const startRegex = /(?:✅\s*Experience|Experience|Responsibilities|Requirements)\s*[:]/i;
    const endRegex = /View\s*Full\s*Post|Hiring\s*Manager/i;

    const startMatch = bodyText.match(startRegex);
    if (startMatch) {
        const startIndex = startMatch.index;
        const remainingText = bodyText.slice(startIndex);
        const endMatch = remainingText.match(endRegex);
        const endIndex = endMatch ? endMatch.index : remainingText.length;

        shortDescription = remainingText.slice(0, endIndex).replace(startMatch[0], '').trim();
    }

    if (!shortDescription) shortDescription = bodyText.slice(0, 500);

    // ── 3. Handle "View Full Post" Button & Interception ───────────────────
    let viewFullPostBtn = null;
    const candidates = Array.from(popup.querySelectorAll('button, a'));
    for (const el of candidates) {
        const txt = el.innerText.toLowerCase();
        if (txt.includes('view full post') || txt.includes('view post')) {
            viewFullPostBtn = el;
            log('INFO', 'Found View Full Post button');
            break;
        }
    }

    let viewFullPostUrl = '';
    if (viewFullPostBtn) {
        // Check for direct href first (in case it's actually an <a>)
        if (viewFullPostBtn.tagName === 'A' && (viewFullPostBtn.href || viewFullPostBtn.getAttribute('href'))) {
            viewFullPostUrl = viewFullPostBtn.href || viewFullPostBtn.getAttribute('href');
        }

        // Check common data attributes
        if (!viewFullPostUrl) {
            const attrs = ['data-url', 'data-href', 'url', 'href', 'data-link'];
            for (const attr of attrs) {
                const val = viewFullPostBtn.getAttribute(attr);
                if (val && val.startsWith('http')) {
                    viewFullPostUrl = val;
                    break;
                }
            }
        }

        if (!viewFullPostUrl) {
            log('INFO', 'Attempting main-world interception (via background script) for URL capture...');

            // 1. Tell background to prep the MAIN world interceptor
            await chrome.runtime.sendMessage({ type: MSG.PREPARE_INTERCEPTION });

            // 2. Trigger the click
            log('INFO', 'Clicking "View Full Post" button...');
            viewFullPostBtn.click();

            // 3. Poll for result (DOM-based bridge)
            for (let i = 0; i < 20; i++) {
                await sleep(100);
                viewFullPostUrl = document.body.getAttribute('data-rp-url') || '';
                if (viewFullPostUrl) break;
            }

            // 4. Tell background to cleanup
            await chrome.runtime.sendMessage({ type: MSG.CLEANUP_INTERCEPTION });

            log('INFO', viewFullPostUrl
                ? `window.open intercepted → ${viewFullPostUrl}`
                : 'window.open NOT called by button');
        }
    }

    // Final fallback: all external links
    if (!viewFullPostUrl) {
        const externalLink = Array.from(popup.querySelectorAll('a[href]'))
            .find(a => a.href.startsWith('http') && !a.href.includes('landbetterjobs.com'));
        if (externalLink) viewFullPostUrl = externalLink.href;
    }

    return {
        title: title || 'Unknown Title',
        company: company || 'Confidential',
        hiringManager: {
            name: hiringManager || 'N/A',
            profileUrl: hiringManagerProfileUrl || '',
        },
        shortDescription,
        viewFullPostUrl,
    };
}

async function closePopup(popup) {
    if (!popup) return;

    // Look for close button first
    const closeBtn = popup.querySelector('button[aria-label="Close"], button.close, .close-icon');
    if (closeBtn) {
        closeBtn.click();
        await sleep(300);
        return;
    }

    // Try ESC key
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', code: 'Escape', bubbles: true }));
    await sleep(300);
}
