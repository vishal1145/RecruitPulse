/**
 * background.js  –  RecruitPulse Service Worker
 *
 * Owns the *entire* processing pipeline:
 *   1. Receives job list from the dashboard content script
 *   2. Iterates sequentially
 *   3. Commands the dashboard content script to click each job's action button
 *      and extract the popup data
 *   4. Opens the "View Full Post" URL in a new tab
 *   5. Injects the right extractor into that tab
 *   6. Merges all data and POSTs to the backend API
 *   7. Closes the tab
 *   8. Waits JOB_DELAY_MS before the next job
 */
import {
    MSG, STORAGE, JOB_DELAY_MS, TAB_LOAD_TIMEOUT_MS, API_BASE_URL,
} from './utils/constants.js';
import {
    log, sleep, retry, generateJobId,
    isAlreadyProcessed, markAsProcessed, updateStats, getDomainGroup,
} from './utils/helpers.js';
import { sendJobToAPI } from './utils/api.js';

// ─── State ─────────────────────────────────────────────────────────────────

let queue = [];   // Array of raw job objects collected from dashboard
let isRunning = false; // Global lock to prevent overlapping runs
let stopRequested = false; // Manual stop flag (does NOT affect scheduler)
let dashboardTabId = null; // The tab running the dashboard content script
let processedCount = 0;
let failedCount = 0;
let isProcessing = false; // Sequential locking flag for single items

const _pendingResolvers = {
    popupData: null,
    externalData: null,
    emailData: null,
    outreachData: null,
};

// ─── Message Router ─────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    switch (msg.type) {

        // ── From Popup ────────────────────────────────────────────────────────
        case MSG.UPDATE_AUTOMATION:
            handleUpdateAutomation(true); // Force update when explicitly requested from popup
            sendResponse({ ok: true });
            break;

        case MSG.START_QUEUE:
            log('INFO', 'Manual Start triggered (testing)');
            runAgent();
            sendResponse({ ok: true });
            break;

        case MSG.STOP_QUEUE:
            stopRequested = true;
            log('INFO', 'Manual Stop requested (current run only)');
            sendResponse({ ok: true });
            break;

        case MSG.GET_STATUS:
            sendResponse({
                isRunning,
                processedCount,
                failedCount,
                queueLength: queue.length,
            });
            break;

        // ── From Dashboard Content Script ─────────────────────────────────────
        case MSG.JOBS_COLLECTED:
            log('INFO', `Dashboard reported ${msg.jobs.length} jobs`, msg.jobs.map(j => j.title));
            queue = msg.jobs;
            sendResponse({ ok: true });
            break;

        case MSG.JOB_POPUP_DATA:
            if (_pendingResolvers.popupData) {
                _pendingResolvers.popupData(msg.data);
                _pendingResolvers.popupData = null;
            }
            sendResponse({ ok: true });
            break;

        case MSG.EMAIL_DATA:
            if (_pendingResolvers.emailData) {
                _pendingResolvers.emailData(msg.data);
                _pendingResolvers.emailData = null;
            }
            sendResponse({ ok: true });
            break;

        case MSG.OUTREACH_DATA:
            if (_pendingResolvers.outreachData) {
                _pendingResolvers.outreachData(msg.data);
                _pendingResolvers.outreachData = null;
            }
            sendResponse({ ok: true });
            break;

        // ── From External Tab Extractor ───────────────────────────────────────
        case MSG.EXTERNAL_DATA:
            if (_pendingResolvers.externalData) {
                _pendingResolvers.externalData(msg.data);
                _pendingResolvers.externalData = null;
            }
            sendResponse({ ok: true });
            break;

        // ── For Interception (Bypassing CSP) ──────────────────────────────────
        case MSG.PREPARE_INTERCEPTION:
            handlePrepareInterception(sender.tab.id).then(() => sendResponse({ ok: true }));
            return true; // Keep channel open

        case MSG.CLEANUP_INTERCEPTION:
            handleCleanupInterception(sender.tab.id).then(() => sendResponse({ ok: true }));
            return true; // Keep channel open

        case MSG.DOWNLOAD_FILE:
            chrome.downloads.download({
                url: msg.url,
                filename: msg.filename,
                saveAs: false,
                conflictAction: 'overwrite'
            }, (downloadId) => {
                if (chrome.runtime.lastError) {
                    log('ERROR', 'Download failed to start:', chrome.runtime.lastError.message);
                    sendResponse({ ok: false, error: chrome.runtime.lastError.message });
                } else {
                    log('INFO', 'Download started:', downloadId);
                    sendResponse({ ok: true, downloadId });
                }
            });
            return true;

        default:
            break;
    }

    // Keep message channel open for async responses
    return true;
});

// ─── Automation ──────────────────────────────────────────────────────────────

chrome.alarms.onAlarm.addListener(async (alarm) => {
    if (alarm.name === 'agentAutomation') {
        const now = new Date().toISOString();
        log('INFO', `⏰ Automation alarm triggered at ${now}. Running agent...`);
        
        // Track execution
        await chrome.storage.local.set({ 
            lastAlarmExecution: now,
            lastAlarmScheduledTime: alarm.scheduledTime 
        });
        
        runAgent();
    }
    if (alarm.name === 'pendingActionPoll') {
        log('INFO', '🔍 Pending action poll alarm triggered.');
        pollPendingActions();
    }
});

chrome.runtime.onStartup.addListener(() => {
    log('INFO', '🔄 Chrome startup detected - reinitializing alarms');
    chrome.storage.local.get([STORAGE.AUTOMATION_ENABLED, STORAGE.AUTOMATION_GAP], (data) => {
        if (data[STORAGE.AUTOMATION_ENABLED] && data[STORAGE.AUTOMATION_GAP]) {
            setupAutomation(data[STORAGE.AUTOMATION_GAP], true);
        }
    });
});

chrome.runtime.onInstalled.addListener(() => {
    log('INFO', '🔄 Extension installed/updated - initializing alarms');
    chrome.storage.local.get([STORAGE.AUTOMATION_ENABLED, STORAGE.AUTOMATION_GAP], (data) => {
        if (data[STORAGE.AUTOMATION_ENABLED] && data[STORAGE.AUTOMATION_GAP]) {
            setupAutomation(data[STORAGE.AUTOMATION_GAP], true);
        }
    });
});

async function handleUpdateAutomation(force = false) {
    const data = await chrome.storage.local.get([STORAGE.AUTOMATION_ENABLED, STORAGE.AUTOMATION_GAP]);
    if (data[STORAGE.AUTOMATION_ENABLED] && data[STORAGE.AUTOMATION_GAP]) {
        setupAutomation(data[STORAGE.AUTOMATION_GAP], force);
    } else {
        log('INFO', 'Automation disabled. Clearing alarm.');
        chrome.alarms.clear('agentAutomation');
    }
}

async function setupAutomation(gapMinutes, force = false) {
    if (!force) {
        const existingAlarm = await chrome.alarms.get('agentAutomation');
        if (existingAlarm) {
            log('INFO', `Automation alarm already exists. Scheduled for: ${new Date(existingAlarm.scheduledTime).toLocaleString()}`);
            return;
        }
    }

    // Clear any existing alarm first to avoid duplicates
    await chrome.alarms.clear('agentAutomation');

    chrome.alarms.create('agentAutomation', {
        delayInMinutes: gapMinutes,
        periodInMinutes: gapMinutes
    });
    
    // Verify alarm was created
    const verifyAlarm = await chrome.alarms.get('agentAutomation');
    if (verifyAlarm) {
        const nextRun = new Date(verifyAlarm.scheduledTime).toLocaleString();
        log('INFO', `✅ Automation scheduled every ${gapMinutes} minutes. Next run: ${nextRun}`);
        
        // Store last setup time for debugging
        await chrome.storage.local.set({ 
            lastAlarmSetup: new Date().toISOString(),
            nextScheduledRun: verifyAlarm.scheduledTime 
        });
    } else {
        log('ERROR', '❌ Failed to create automation alarm!');
    }
}

// ─── Initialization ──────────────────────────────────────────────────────────

// Service Worker Keepalive: Prevent premature termination
let keepalivePort = null;

function ensureServiceWorkerAlive() {
    if (keepalivePort) return;
    
    keepalivePort = chrome.runtime.connect({ name: 'keepalive' });
    keepalivePort.onDisconnect.addListener(() => {
        keepalivePort = null;
        log('INFO', '🔌 Keepalive port disconnected - service worker may terminate');
    });
    
    log('INFO', '🔌 Keepalive port established');
}

// Check automation settings on load
chrome.storage.local.get([STORAGE.AUTOMATION_ENABLED, STORAGE.AUTOMATION_GAP], (data) => {
    if (data[STORAGE.AUTOMATION_ENABLED] && data[STORAGE.AUTOMATION_GAP]) {
        log('INFO', 'Restoring automation schedule on startup');
        setupAutomation(data[STORAGE.AUTOMATION_GAP], true);
        
        // Establish keepalive for long-interval alarms
        if (data[STORAGE.AUTOMATION_GAP] > 5) {
            ensureServiceWorkerAlive();
        }
    }
});

// ─── Core Agent Logic ────────────────────────────────────────────────────────

async function runAgent() {
    if (isRunning) {
        log('WARN', 'Agent is already running – ignoring run command');
        return;
    }

    log('INFO', '▶ Starting RecruitPulse agent cycle');
    isRunning = true;
    stopRequested = false;
    processedCount = 0;
    failedCount = 0;

    // Persist running state
    await chrome.storage.local.set({ [STORAGE.QUEUE_ACTIVE]: true });

    // Find or navigate to the job-search-ai-agent tab
    try {
        let tabs = await chrome.tabs.query({ url: 'https://landbetterjobs.com/job-search-ai-agent*' });

        if (tabs.length === 0) {
            // No job-search-ai-agent tab open — open one
            broadcastStatus('🔀 Opening /job-search-ai-agent in a new tab…', 'info');
            log('INFO', 'No job-search-ai-agent tab found. Opening new tab.');

            const newTab = await chrome.tabs.create({ url: 'https://landbetterjobs.com/job-search-ai-agent', active: true });
            await waitForTabComplete(newTab.id);
            await sleep(2000); // Give React time to mount

            dashboardTabId = newTab.id;
        } else {
            dashboardTabId = tabs[0].id;
        }

        log('INFO', `job-search-ai-agent tab: ${dashboardTabId}`);
    } catch (err) {
        log('ERROR', 'Failed to find/navigate to job-search-ai-agent tab', err);
        finishQueue();
        return;
    }

    // Ask the dashboard content script to ensure "New" filter is selected
    broadcastStatus('🔍 Ensuring "New" filter is selected on dashboard…', 'info');
    try {
        await requestJobCollection();
    } catch (err) {
        broadcastStatus(`❌ Failed to prepare dashboard: ${err.message}`, 'error');
        finishQueue();
        return;
    }

    // Phase 1: Sequential processing with dynamic fetching
    broadcastStatus('📋 Starting sequential processing (dynamic mode)…', 'info');
    await runDynamicQueue();
}

// ─── Job Collection ──────────────────────────────────────────────────────────

function requestJobCollection() {
    return new Promise((resolve, reject) => {
        chrome.tabs.sendMessage(dashboardTabId, { type: MSG.COLLECT_JOBS }, response => {
            if (chrome.runtime.lastError) {
                return reject(new Error(chrome.runtime.lastError.message));
            }
            resolve(response);
        });
    });
}

// ─── Main Queue Loop ─────────────────────────────────────────────────────────

async function runDynamicQueue() {
    let iteration = 0;
    
    while (true) {
        if (stopRequested) {
            broadcastStatus('⏹ Processing stopped by user.', 'warn');
            break;
        }

        iteration++;
        const jobTag = `[Job ${iteration}]`;

        // 1. Fetch the next available "New" job from the dashboard
        broadcastStatus(`🔍 ${jobTag} Fetching next available job…`, 'info');
        let nextJobData;
        try {
            nextJobData = await getNextJobFromDashboard();
        } catch (err) {
            log('ERROR', 'Failed to fetch next job', err);
            broadcastStatus(`❌ ${jobTag} Error fetching job: ${err.message}`, 'error');
            break; 
        }

        if (!nextJobData || !nextJobData.job) {
            broadcastStatus(`🏁 Phase 1 complete. No more jobs found on the current page.`, 'success');
            break;
        }

        const rawJob = nextJobData.job;
        const jobId = generateJobId(rawJob.title || `job_${iteration}`, rawJob.company || 'unknown');

        broadcastStatus(`⚙️ ${jobTag} Processing: "${rawJob.title}" (at index ${rawJob.index})`, 'info');

        try {
            // Strict Sequential Lock
            await processSingleJob(rawJob, jobId, rawJob.index, jobTag);
            processedCount++;
            broadcastStatus(`✅ ${jobTag} Done: "${rawJob.title}"`, 'success');
        } catch (err) {
            failedCount++;
            await updateStats('failed');
            log('ERROR', `${jobTag} Job failed`, { title: rawJob.title, error: err.message });
            broadcastStatus(`❌ ${jobTag} Failed: "${rawJob.title}" — ${err.message}`, 'error');
        }

        // Wait for DOM to update and provide spacing between runs
        broadcastStatus(`⏱ Waiting 3s for UI stability…`, 'info');
        await sleep(3000); 
    }

    // --- Phase 2: Resume Builder ---
    broadcastStatus('🚀 Starting Phase 2: JD Resume Builder…', 'info');
    await sleep(2000);
    await runResumeBuilderQueue();

    finishQueue();
}

// ─── Wrapper for Sequential Control ───────────────────────────────────────────

async function processSingleJob(rawJob, jobId, rowIndex, jobNum) {
    if (isProcessing) return;
    isProcessing = true;

    try {
        await processJobFlow(rawJob, jobId, rowIndex, jobNum);
        await markAsProcessed(jobId);
        await updateStats('success');
    } finally {
        isProcessing = false;
    }
}

// ─── Single Job Flow Logic ───────────────────────────────────────────────────

async function processJobFlow(rawJob, jobId, rowIndex, jobNum) {
    // Step 1: Simulate Real User Click (Main World)
    // We do this to trigger the "Reviewed" status update which requires complex events.
    broadcastStatus(`🖱️ ${jobNum} Simulating interaction…`, 'info');
    try {
        await chrome.scripting.executeScript({
            target: { tabId: dashboardTabId },
            world: 'MAIN',
            func: simulateClickInMainWorld,
            args: [rowIndex]
        });
    } catch (err) {
        throw new Error(`Failed to simulate click: ${err.message}`);
    }

    // Step 2: Extract Popup Data (Content Script)
    let popupData;
    try {
        popupData = await getPopupData(rowIndex);
    } catch (err) {
        throw new Error(`Popup extraction failed: ${err.message}`);
    }

    log('INFO', `${jobNum} Popup data extracted`, popupData);

    const viewUrl = popupData.viewFullPostUrl;
    if (!viewUrl) {
        throw new Error('No "View Full Post" URL found in popup');
    }

    // Step 2: Open external tab (hidden)
    const source = getDomainGroup(viewUrl);
    broadcastStatus(`🌐 ${jobNum} Opening ${source} page…`, 'info');

    let tab;
    try {
        tab = await chrome.tabs.create({ url: viewUrl, active: false });
        await waitForTabComplete(tab.id);
    } catch (err) {
        throw new Error(`Failed to open/load external tab: ${err.message}`);
    }

    // Step 3: Inject and Execute scraper
    let externalData = { fullDescription: '', applyEmail: '', location: '', experience: '' };
    try {
        // Enforce a small extra delay for SPA rendering if needed
        await sleep(1000);

        const results = await chrome.scripting.executeScript({
            target: { tabId: tab.id },
            func: scrapeExternalJobPage
        });

        if (!results || !results[0] || !results[0].result) {
            log('WARN', `${jobNum} External scraping returned empty result`);
        } else {
            externalData = results[0].result;
            log('INFO', `${jobNum} External data extracted`, externalData);
        }
    } catch (err) {
        log('WARN', `${jobNum} External extractor failed`, err.message);
    } finally {
        // Step 4: ONLY close tab after extraction is finished
        try {
            await chrome.tabs.remove(tab.id);
            log('INFO', `${jobNum} Tab closed`);
        } catch (_) { }
    }

    // Step 4.5: Extract Email Template (back in dashboard popup)
    broadcastStatus(`📧 ${jobNum} Extracting email template…`, 'info');
    let emailData = { emailSubject: '', emailBody: '' };
    try {
        emailData = await getEmailData();
        log('INFO', `${jobNum} Email data extracted`, emailData);
    } catch (err) {
        log('WARN', `${jobNum} Email extraction failed`, err.message);
    }

    // Step 4.6: Extract Outreach Data (Initial Message + Follow-up)
    broadcastStatus(`💬 ${jobNum} Extracting outreach messages…`, 'info');
    let outreachData = { initialMessage: '', followUpMessage1: '' };
    try {
        outreachData = await getOutreachData();
        log('INFO', `${jobNum} Outreach data extracted`, outreachData);
    } catch (err) {
        log('WARN', `${jobNum} Outreach extraction failed`, err.message);
    }

    // Step 5: Merge and send to API
    const jobPayload = {
        jobId,
        title: popupData.title || rawJob.title || '',
        company: popupData.company || rawJob.company || '',
        hiringManager: popupData.hiringManager || { name: '', profileUrl: '' },
        shortDescription: popupData.shortDescription || '',
        viewFullPostUrl: viewUrl,
        jobPostUrl: viewUrl,
        fullDescription: externalData.fullDescription || '',
        applyEmail: externalData.applyEmail || 'not-provided',
        location: externalData.location || '',
        experience: externalData.experience || '',
        emailSubject: emailData.emailSubject || '',
        emailBody: emailData.emailBody || '',
        outreach: {
            initialMessage: outreachData.initialMessage || '',
            followUpMessage1: outreachData.followUpMessage1 || '',
        },
        source,
        processedAt: new Date().toISOString(),
        jdResumeBuilt: false,
    };

    // --- Save to Local Storage ---
    try {
        const stored = await chrome.storage.local.get(['scrapedJobs']);
        const jobs = stored.scrapedJobs || [];
        // Prevent duplicate payload entries for the same jobId in storage
        const filtered = jobs.filter(j => j.jobId !== jobId);
        filtered.push(jobPayload);
        await chrome.storage.local.set({ scrapedJobs: filtered });
        log('INFO', `${jobNum} Saved to local storage`);
    } catch (err) {
        log('WARN', `${jobNum} Failed to save to local storage`, err);
    }

    // --- Send to API (Optional) ---
    try {
        const apiResult = await sendJobToAPI(jobPayload);
        if (!apiResult.success) {
            log('WARN', `API submission failed but continuing: ${apiResult.error}`);
        }
    } catch (err) {
        log('WARN', `API call failed: ${err.message}`);
    }
}

// ─── Phase 2: Resume Builder Queue ───────────────────────────────────────────

async function runResumeBuilderQueue() {
    log('INFO', 'Starting Resume Builder Queue...');

    // 1. Fetch pending jobs from API
    let jobs = [];
    try {
        // Fetch Telegram configuration from storage
        const storage = await chrome.storage.local.get(['telegram_config']);
        const telegramConfig = storage.telegram_config || null;

        const response = await fetch(`${API_BASE_URL}/api/jobs`, {
            method: 'GET',
            headers: {
                'Content-Type': 'application/json',
                'X-Telegram-Config': telegramConfig ? JSON.stringify(telegramConfig) : ''
            }
        });
        if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
        jobs = await response.json();
    } catch (err) {
        broadcastStatus(`❌ Failed to fetch jobs for Phase 2: ${err.message}`, 'error');
        return;
    }

    const pendingJobs = jobs.filter(job => job.jdResumeBuilt !== true);
    if (pendingJobs.length === 0) {
        broadcastStatus('✅ No pending jobs for Resume Builder. Phase 2 skipped.', 'success');
        return;
    }

    broadcastStatus(`📝 Phase 2: Building resumes for ${pendingJobs.length} job(s)…`, 'info');

    // 2. Navigate Dashboard Tab to Resume Builder
    try {
        await chrome.tabs.update(dashboardTabId, { url: 'https://landbetterjobs.com/jd-resume-builder' });
        await waitForTabComplete(dashboardTabId);
        await sleep(2000); // Give page time to initialize
    } catch (err) {
        broadcastStatus(`❌ Failed to navigate to Resume Builder: ${err.message}`, 'error');
        return;
    }

    for (let i = 0; i < pendingJobs.length; i++) {

        const job = pendingJobs[i];
        const jobNum = `[Resume ${i + 1}/${pendingJobs.length}]`;
        broadcastStatus(`📝 ${jobNum} Building resume for: "${job.title}"`, 'info');

        try {
            // Send command to resumeBuilder.js
            const result = await new Promise((resolve, reject) => {
                chrome.tabs.sendMessage(dashboardTabId, { type: MSG.BUILD_RESUME, job }, response => {
                    if (chrome.runtime.lastError) return reject(new Error(chrome.runtime.lastError.message));
                    if (response && response.error) return reject(new Error(response.error));
                    resolve(response);
                });
            });

            if (result && result.success) {
                // Update job status in API
                job.jdResumeBuilt = true;
                job.jdResumeBuiltAt = new Date().toISOString();
                await sendJobToAPI(job);
                broadcastStatus(`✅ ${jobNum} Resume built for: "${job.title}"`, 'success');
            }
        } catch (err) {
            log('ERROR', `${jobNum} Resume build failed`, err.message);
            broadcastStatus(`❌ ${jobNum} Failed: "${job.title}" — ${err.message}`, 'error');
        }

        if (i < pendingJobs.length - 1) {
            broadcastStatus(`⏱ Waiting 5s before next resume and reloading for fresh state…`, 'info');
            await sleep(5000);
            await chrome.tabs.reload(dashboardTabId);
            await waitForTabComplete(dashboardTabId);
            await sleep(3000); // Give the fresh React page some extra time
        }
    }

    broadcastStatus('🏁 Phase 2 (Resume Builder) complete.', 'complete');
}


// ─── Popup Data via Dashboard Content Script ──────────────────────────────────

function getNextJobFromDashboard() {
    return new Promise((resolve, reject) => {
        chrome.tabs.sendMessage(dashboardTabId, { type: MSG.GET_NEXT_JOB }, response => {
            if (chrome.runtime.lastError) {
                return reject(new Error(chrome.runtime.lastError.message));
            }
            if (response && response.error) {
                return reject(new Error(response.error));
            }
            resolve(response);
        });
    });
}

function getPopupData(rowIndex) {
    return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
            _pendingResolvers.popupData = null;
            reject(new Error('Timeout waiting for popup data from dashboard content script'));
        }, 20000);

        _pendingResolvers.popupData = (data) => {
            clearTimeout(timeout);
            if (data && data.error) return reject(new Error(data.error));
            resolve(data);
        };

        chrome.tabs.sendMessage(
            dashboardTabId,
            { type: MSG.CLICK_JOB_ACTION, rowIndex },
            response => {
                if (chrome.runtime.lastError) {
                    clearTimeout(timeout);
                    _pendingResolvers.popupData = null;
                    reject(new Error(chrome.runtime.lastError.message));
                }
            }
        );
    });
}

function getEmailData() {
    return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
            _pendingResolvers.emailData = null;
            reject(new Error('Timeout waiting for email data from dashboard content script'));
        }, 15000);

        _pendingResolvers.emailData = (data) => {
            clearTimeout(timeout);
            if (data && data.error) return reject(new Error(data.error));
            resolve(data);
        };

        chrome.tabs.sendMessage(
            dashboardTabId,
            { type: MSG.EXTRACT_EMAIL_DATA },
            response => {
                if (chrome.runtime.lastError) {
                    clearTimeout(timeout);
                    _pendingResolvers.emailData = null;
                    reject(new Error(chrome.runtime.lastError.message));
                }
            }
        );
    });
}

function getOutreachData() {
    return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
            _pendingResolvers.outreachData = null;
            reject(new Error('Timeout waiting for outreach data from dashboard content script'));
        }, 20000);

        _pendingResolvers.outreachData = (data) => {
            clearTimeout(timeout);
            if (data && data.error) return reject(new Error(data.error));
            resolve(data);
        };

        chrome.tabs.sendMessage(
            dashboardTabId,
            { type: MSG.EXTRACT_OUTREACH_DATA },
            response => {
                if (chrome.runtime.lastError) {
                    clearTimeout(timeout);
                    _pendingResolvers.outreachData = null;
                    reject(new Error(chrome.runtime.lastError.message));
                }
            }
        );
    });
}

// ─── Tab Management ───────────────────────────────────────────────────────────

/**
 * Enforce waiting for tab "complete" status.
 */
function waitForTabComplete(tabId) {
    return new Promise((resolve, reject) => {
        const timeout = setTimeout(() => {
            chrome.tabs.onUpdated.removeListener(listener);
            reject(new Error(`Timeout waiting for tab ${tabId} to complete`));
        }, TAB_LOAD_TIMEOUT_MS);

        function listener(id, info) {
            if (id === tabId && info.status === "complete") {
                chrome.tabs.onUpdated.removeListener(listener);
                clearTimeout(timeout);
                resolve();
            }
        }
        chrome.tabs.onUpdated.addListener(listener);

        // Fallback: check current status immediately in case it completed before listener added
        chrome.tabs.get(tabId, tab => {
            if (tab && tab.status === 'complete') {
                chrome.tabs.onUpdated.removeListener(listener);
                clearTimeout(timeout);
                resolve();
            }
        });
    });
}

/**
 * Generic Scraper Function (Injected into pages)
 * Strictly extracts the main post body for LinkedIn updates and Job pages.
 */
function scrapeExternalJobPage() {
    const sleep = (ms) => new Promise(r => setTimeout(r, ms));
    const extractEmails = (text) => {
        const matches = text.match(/[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g) || [];
        return [...new Set(matches)];
    };

    // --- 1. Identify Target Container ---
    // For Feed Posts/Updates: .feed-shared-update-v2
    // For Job Pages: .jobs-description__content or #job-details
    const postContainer = document.querySelector('div.feed-shared-update-v2')
        || document.querySelector('.jobs-description__content')
        || document.querySelector('#job-details')
        || document.querySelector('article');

    if (postContainer) {
        // --- 2. Expand "See more" if needed ---
        const seeMoreBtn = postContainer.querySelector('button[aria-label*="more"], button[class*="see-more"]');
        if (seeMoreBtn) {
            seeMoreBtn.click();
            // No await here in direct injection usually, but we can try to wait briefly if we were in an async context.
            // Since this function is the raw func string for executeScript, it runs once.
        }
    }

    let fullDescription = '';

    // --- 3. Extract Post Content (Ignore Comments/Social) ---
    if (postContainer) {
        // Specific LinkedIn Update text block
        const postTextElement = postContainer.querySelector('div.update-components-text span[dir="ltr"]')
            || postContainer.querySelector('div.update-components-text')
            || postContainer.querySelector('.jobs-description__content')
            || postContainer.querySelector('.jobs-box__html-content');

        if (postTextElement) {
            fullDescription = postTextElement.innerText.trim();
        } else {
            // Fallback: take container text but cut off at "Like" or "Social" sections
            let rawText = postContainer.innerText || '';
            const markers = ['Like', 'Comment', 'Repost', 'Send', 'Reactions'];
            for (const marker of markers) {
                if (rawText.includes(marker)) {
                    rawText = rawText.split(marker)[0];
                }
            }
            fullDescription = rawText.trim();
        }
    }

    // --- 4. Global Fallback if Still Empty ---
    if (!fullDescription || fullDescription.length < 20) {
        fullDescription = document.body.innerText.slice(0, 3000);
    }

    // --- 5. Extract Metadata (Location/Experience) ---
    const locSelectors = ['.jobs-unified-top-card__bullet', '.top-card-layout__first-subline', '.feed-shared-actor__description'];
    let location = '';
    for (const sel of locSelectors) {
        const el = document.querySelector(sel);
        if (el) { location = el.innerText.trim(); break; }
    }

    let experience = '';
    const insights = document.querySelectorAll('.jobs-unified-top-card__job-insight, .jobs-description__job-criteria-item');
    for (const item of insights) {
        const text = item.innerText.toLowerCase();
        if (text.includes('experience') || text.includes('level') || text.includes('seniority')) {
            experience = item.innerText.trim();
            break;
        }
    }

    return {
        fullDescription,
        applyEmail: extractEmails(document.body.innerText)[0] || '',
        location,
        experience
    };
}

// ─── Cleanup ──────────────────────────────────────────────────────────────────

function finishQueue() {
    if (dashboardTabId) {
        log('INFO', 'Redirecting dashboard tab back to Job Search AI Agent...');
        chrome.tabs.update(dashboardTabId, { url: 'https://landbetterjobs.com/job-search-ai-agent' });
    }
    isRunning = false;
    stopRequested = false;
    dashboardTabId = null;
    queue = [];
    chrome.storage.local.set({ [STORAGE.QUEUE_ACTIVE]: false });
    log('INFO', '■ Queue processor shut down');
}

// ─── Interception Prep (MAIN world) ──────────────────────────────────────────

async function handlePrepareInterception(tabId) {
    log('INFO', `Preparing MAIN world interception for tab ${tabId}`);
    try {
        await chrome.scripting.executeScript({
            target: { tabId },
            world: 'MAIN',
            func: () => {
                // This code runs in the page's actual JS context (Main World)
                document.body.setAttribute('data-rp-url', '');

                // Backup originals if not already done
                window._rp_origOpen = window._rp_origOpen || window.open;
                window._rp_origAssign = window._rp_origAssign || (window.location && window.location.assign);
                window._rp_origReplace = window._rp_origReplace || (window.location && window.location.replace);

                function capture(url) {
                    if (!url || typeof url !== 'string') return;
                    if (url.startsWith('http')) {
                        document.body.setAttribute('data-rp-url', url);
                    }
                }

                // Intercept window.open
                window.open = function (url) {
                    capture(url);
                    return { close: () => { }, focus: () => { } };
                };

                // Intercept location methods
                if (window.location) {
                    window.location.assign = function (url) { capture(url); };
                    window.location.replace = function (url) { capture(url); };
                }

                // Intercept location.href setter
                try {
                    const oldLoc = window.location;
                    if (oldLoc) {
                        // This is tricky for window.location directly, but we've caught assign/replace
                    }
                } catch (e) { }

                console.log('[RecruitPulse] MAIN world interceptor installed');
            }
        });
    } catch (err) {
        log('ERROR', 'Failed to inject MAIN world interceptor', err);
    }
}

async function handleCleanupInterception(tabId) {
    log('INFO', `Cleaning up MAIN world interception for tab ${tabId}`);
    try {
        await chrome.scripting.executeScript({
            target: { tabId },
            world: 'MAIN',
            func: () => {
                if (window._rp_origOpen) window.open = window._rp_origOpen;
                if (window._rp_origAssign && window.location) window.location.assign = window._rp_origAssign;
                if (window._rp_origReplace && window.location) window.location.replace = window._rp_origReplace;
                document.body.removeAttribute('data-rp-url');
                console.log('[RecruitPulse] MAIN world interceptor removed');
            }
        });
    } catch (err) {
        log('ERROR', 'Failed to cleanup MAIN world interceptor', err);
    }
}

/**
 * Injected into MAIN world to simulate a real user click.
 * Updates "Reviewed" status.
 */
function simulateClickInMainWorld(rowIndex) {
    // Helper to find the row (must match dashboard.js logic)
    function getRow() {
        const rows = Array.from(document.querySelectorAll('tbody tr'));
        if (rows[rowIndex]) return rows[rowIndex];

        // Fallback
        const allRows = Array.from(document.querySelectorAll('tr'));
        return allRows.filter(r => r.querySelector('td'))[rowIndex];
    }

    const row = getRow();
    if (!row) throw new Error(`Row ${rowIndex} not found in Main World`);

    // Target the "Job & Company" cell (usually index 1) or first cell
    const cells = Array.from(row.querySelectorAll('td'));
    const target = cells[1] || cells[0] || row;

    console.log('[RecruitPulse] Simulating real click on:', target);

    // 1. Scroll
    target.scrollIntoView({ behavior: "instant", block: "center" });

    // 2. Async Click Sequence
    return new Promise(async (resolve) => {
        const delay = ms => new Promise(r => setTimeout(r, ms));

        await delay(300);

        const rect = target.getBoundingClientRect();
        const x = rect.left + rect.width / 2;
        const y = rect.top + rect.height / 2;

        ["mouseover", "mousedown", "mouseup", "click"].forEach(type => {
            target.dispatchEvent(
                new MouseEvent(type, {
                    view: window,
                    bubbles: true,
                    cancelable: true,
                    clientX: x,
                    clientY: y
                })
            );
        });

        await delay(800);
        resolve();
    });
}

// ─── Popup Status Broadcast ────────────────────────────────────────────────────

/**
 * Send a status update to the popup (if open) and log it.
 * @param {string} message
 * @param {'info'|'success'|'warn'|'error'|'complete'} level
 */
function broadcastStatus(message, level = 'info') {
    log('INFO', message);
    chrome.runtime.sendMessage({
        type: MSG.STATUS_UPDATE,
        message,
        level,
        timestamp: new Date().toISOString(),
        stats: { processedCount, failedCount, queueLength: queue.length },
    }).catch(() => {
        // Popup may be closed – silently ignore
    });
}

/**
 * Check if job exists in the persistent 'scrapedJobs' list.
 */
async function isJobAlreadyScraped(jobId) {
    return new Promise(resolve => {
        chrome.storage.local.get(['scrapedJobs'], result => {
            const jobs = result.scrapedJobs || [];
            // Check if any job in storage has this ID
            const exists = jobs.some(j => j.jobId === jobId);
            resolve(exists);
        });
    });
}

// ─── PDF Export Download Tracking ───────────────────────────────────────────

chrome.downloads.onCreated.addListener((downloadItem) => {
    if (currentDownloadJob && _pdfExportResolver) {
        log('INFO', 'Download started, tracking for rename:', downloadItem.id);
        _downloadResolvers.set(downloadItem.id, _pdfExportResolver);
        _pdfExportResolver = null; // Handled by map now
    }
});

chrome.downloads.onDeterminingFilename.addListener((item, suggest) => {
    if (currentDownloadJob) {
        const sanitizedTitle = currentDownloadJob.title.replace(/[^a-z0-9]/gi, '_').toLowerCase();
        const timestamp = new Date().getTime();
        const newFilename = `RecruitPulse_${sanitizedTitle}_${timestamp}.pdf`;

        log('INFO', `Renaming download ${item.id} to: ${newFilename}`);
        suggest({ filename: newFilename, conflictAction: 'overwrite' });

        // Optional: clear job so we don't intercept other unrelated downloads
        // But better wait until completion to be safe
    }
});

chrome.downloads.onChanged.addListener((delta) => {
    const resolver = _downloadResolvers.get(delta.id);
    if (!resolver) return;

    if (delta.state && delta.state.current === 'complete') {
        log('INFO', `Download ${delta.id} complete`);
        resolver.resolve({ success: true, downloadId: delta.id });
        _downloadResolvers.delete(delta.id);
        currentDownloadJob = null;
    } else if (delta.error) {
        log('ERROR', `Download ${delta.id} failed:`, delta.error.current);
        resolver.reject(new Error(`Download failed: ${delta.error.current}`));
        _downloadResolvers.delete(delta.id);
        currentDownloadJob = null;
    }
});

// ─── Pending Actions Polling (Telegram → Extension Draft Update) ─────────────
// Reuses existing resume HTML extraction approach:
//   1. Open resume page in background tab
//   2. Content script extracts HTML from div#resume-preview
//   3. Send HTML to backend → WeasyPrint generates PDF → updates Gmail draft

let _pendingActionInProgress = false;
const PENDING_ACTION_POLL_INTERVAL_MS = 120000; // 2 minutes

async function pollPendingActions() {
    if (_pendingActionInProgress) return;

    try {
        const resp = await fetch(`${API_BASE_URL}/api/pending-actions`);
        const data = await resp.json();

        if (!data.success || !data.action) return;

        const action = data.action;
        log('INFO', `[PendingAction] Received: ${action.action} for job ${action.job_id}`);

        _pendingActionInProgress = true;
        await handlePendingAction(action);
    } catch (err) {
        if (!err.message.includes('Failed to fetch')) {
            log('WARN', `[PendingAction] Poll error: ${err.message}`);
        }
    } finally {
        _pendingActionInProgress = false;
    }
}

async function handlePendingAction(action) {
    const { job_id, resumeEditUrl } = action;
    let tab = null;

    try {
        log('INFO', `[PendingAction] Opening resume page: ${resumeEditUrl}`);

        // 1. Open the resume page in a background tab
        tab = await chrome.tabs.create({ url: resumeEditUrl, active: false });
        await waitForTabComplete(tab.id);

        // 2. Wait for SPA rendering + content script initialization
        await sleep(5000);

        // 3. Ask content script to extract resume HTML from div#resume-preview
        log('INFO', `[PendingAction] Extracting resume HTML from tab ${tab.id}`);
        let resumeHtml = null;

        try {
            const response = await chrome.tabs.sendMessage(tab.id, { type: 'EXTRACT_RESUME_HTML' });
            if (response && response.html) {
                resumeHtml = response.html;
                log('INFO', `[PendingAction] Got resume HTML (${resumeHtml.length} chars)`);
            } else {
                throw new Error(response?.error || 'No HTML returned from content script');
            }
        } catch (err) {
            throw new Error(`Failed to extract resume HTML: ${err.message}. Content script may not be loaded.`);
        }

        // 4. Send HTML to backend for PDF generation + draft update
        log('INFO', `[PendingAction] Sending HTML to /api/update-draft for job ${job_id}`);
        const updateResp = await fetch(`${API_BASE_URL}/api/update-draft`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                job_id: job_id,
                resume_html: resumeHtml,
            })
        });

        const result = await updateResp.json();
        if (result.success) {
            log('INFO', `[PendingAction] Draft updated! New Draft ID: ${result.newDraftId}`);
        } else {
            throw new Error(`Backend error: ${result.error}`);
        }

    } catch (err) {
        log('ERROR', `[PendingAction] Failed: ${err.message}`);

        // Notify backend of failure
        try {
            await fetch(`${API_BASE_URL}/api/complete-action`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    job_id: job_id,
                    status: 'failed',
                    error: err.message,
                })
            });
        } catch (e) {
            log('ERROR', `[PendingAction] Failed to report error to backend: ${e.message}`);
        }
    } finally {
        // 5. Close the tab
        if (tab) {
            try {
                await chrome.tabs.remove(tab.id);
                log('INFO', `[PendingAction] Tab closed.`);
            } catch (_) { }
        }
    }
}

// Start polling for pending actions using chrome.alarms (survives service worker restarts)
chrome.alarms.create('pendingActionPoll', { periodInMinutes: 2 });
log('INFO', '[PendingAction] Alarm-based polling started (every 2 minutes)');

// Also poll immediately on service worker startup
pollPendingActions();
