import { API_TEST_RESET_URL } from '../utils/constants.js';

/**
 * popup/popup.js  –  RecruitPulse Popup Controller
 *
 * Communicates with background.js to:
 *   - Start / Stop the processing queue
 *   - Receive live STATUS_UPDATE messages and render them in the activity log
 *   - Display stats (queued / sent / failed)
 */

const MSG = {
    START_QUEUE: 'START_QUEUE',
    STOP_QUEUE: 'STOP_QUEUE',
    GET_STATUS: 'GET_STATUS',
    STATUS_UPDATE: 'STATUS_UPDATE',
};

// ── DOM refs ─────────────────────────────────────────────────────────────────

const btnStart = document.getElementById('btnStart');
const btnStop = document.getElementById('btnStop');
const btnCleanTest = document.getElementById('btnCleanTest');
const btnClear = document.getElementById('btnClear');
const btnClearLog = document.getElementById('btnClearLog');
const logFeed = document.getElementById('logFeed');
const statusDot = document.getElementById('statusDot');
const footerStatus = document.getElementById('footerStatus');
const statTotal = document.getElementById('statTotal');
const statSuccess = document.getElementById('statSuccess');
const statFailed = document.getElementById('statFailed');
// Download button removed as server now handles local file saving

// ── State ─────────────────────────────────────────────────────────────────────

let isRunning = false;

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTime(isoOrNow) {
    const d = isoOrNow ? new Date(isoOrNow) : new Date();
    return d.toLocaleTimeString('en-US', { hour12: false });
}

function appendLog(message, level = 'info', timestamp = null) {
    const entry = document.createElement('div');
    entry.className = `log-entry ${level}`;

    const time = document.createElement('span');
    time.className = 'log-time';
    time.textContent = formatTime(timestamp);

    const msg = document.createElement('span');
    msg.className = 'log-msg';
    msg.textContent = message;

    entry.appendChild(time);
    entry.appendChild(msg);
    logFeed.appendChild(entry);

    // Auto-scroll to bottom
    logFeed.scrollTop = logFeed.scrollHeight;

    // Cap log at 200 entries to avoid memory bloat
    while (logFeed.children.length > 200) {
        logFeed.removeChild(logFeed.firstChild);
    }
}

function setRunningState(running) {
    isRunning = running;
    btnStart.disabled = running;
    btnStop.disabled = !running;

    statusDot.className = 'status-indicator ' + (running ? 'running' : '');
    statusDot.title = running ? 'Agent running…' : 'Agent idle';
    footerStatus.textContent = running ? 'Running…' : 'Idle';
}

function updateStats({ processedCount = 0, failedCount = 0, queueLength = 0 } = {}) {
    statTotal.textContent = queueLength;
    statSuccess.textContent = processedCount;
    statFailed.textContent = failedCount;
}

// ── Live message listener ─────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg) => {
    if (msg.type !== MSG.STATUS_UPDATE) return;

    appendLog(msg.message, msg.level || 'info', msg.timestamp);

    if (msg.stats) updateStats(msg.stats);

    // Detect terminal states
    if (msg.level === 'complete' || (msg.level === 'error' && msg.message.includes('ERROR:'))) {
        setRunningState(false);
    }
});

// ── Buttons ───────────────────────────────────────────────────────────────────

btnStart.addEventListener('click', () => {
    setRunningState(true);
    appendLog('▶ Starting RecruitPulse agent…', 'info');

    chrome.runtime.sendMessage({ type: MSG.START_QUEUE, options: {} }, (response) => {
        if (chrome.runtime.lastError) {
            appendLog('❌ Could not reach background: ' + chrome.runtime.lastError.message, 'error');
            setRunningState(false);
        }
    });
});

btnStop.addEventListener('click', () => {
    appendLog('⏹ Stop requested…', 'warn');
    chrome.runtime.sendMessage({ type: MSG.STOP_QUEUE }, () => {
        setRunningState(false);
        footerStatus.textContent = 'Stopped';
        statusDot.className = 'status-indicator error';
    });
});

btnClear.addEventListener('click', () => {
    if (!confirm('Clear all processing history AND saved jobs? This cannot be undone.')) return;
    chrome.storage.local.remove([
        'recruitpulse_processed_ids',
        'recruitpulse_stats',
        'scrapedJobs'
    ], () => {
        appendLog('🗑 History and saved jobs cleared. Starting fresh.', 'warn');
        updateStats({});
    });
});

// Download button removed - server saves to jobs.json automatically
btnCleanTest.addEventListener('click', async () => {
    if (!confirm('This will clean all current jobs and replace them with the test record. Continue?')) return;

    appendLog('🧹 Cleaning jobs and resetting for testing...', 'info');

    try {
        const response = await fetch(API_TEST_RESET_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' }
        });

        const result = await response.json();
        if (result.success) {
            appendLog('✅ ' + result.message, 'success');
            // Refresh stats since jobs changed
            updateStats({ processedCount: 0, failedCount: 0, queueLength: 0 });
        } else {
            appendLog('❌ Reset failed: ' + (result.error || 'Unknown error'), 'error');
        }
    } catch (err) {
        appendLog('❌ Request failed: ' + err.message, 'error');
    }
});

btnClearLog.addEventListener('click', () => {
    logFeed.innerHTML = '';
    appendLog('Log cleared.', 'info');
});

// ── On popup open: sync with background state ─────────────────────────────────

chrome.runtime.sendMessage({ type: MSG.GET_STATUS }, (response) => {
    if (chrome.runtime.lastError || !response) return;
    setRunningState(response.isRunning);
    updateStats(response);
    if (response.isRunning) {
        appendLog('⚙️ Agent is currently running…', 'info');
    }
});

// Load cumulative stats from storage
chrome.storage.local.get(['recruitpulse_stats'], (result) => {
    const stats = result['recruitpulse_stats'];
    if (stats) {
        statSuccess.textContent = stats.success || 0;
        statFailed.textContent = stats.failed || 0;
        if (stats.lastRun) {
            footerStatus.textContent = `Last run: ${formatTime(stats.lastRun)}`;
        }
    }
});
