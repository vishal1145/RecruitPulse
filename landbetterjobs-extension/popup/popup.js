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

const automationGapInput = document.getElementById('automationGap');
const btnSaveAutomation = document.getElementById('btnSaveAutomation');
const btnDisableAutomation = document.getElementById('btnDisableAutomation');
const btnStart = document.getElementById('btnStart');
const btnStop = document.getElementById('btnStop');
const btnCleanTest = document.getElementById('btnCleanTest');
const btnClear = document.getElementById('btnClear');

const statusDot = document.getElementById('statusDot');
const footerStatus = document.getElementById('footerStatus');
const statTotal = document.getElementById('statTotal');
const statSuccess = document.getElementById('statSuccess');
const statFailed = document.getElementById('statFailed');
const btnToggleSettings = document.getElementById('btnToggleSettings');
const settingsSection = document.querySelector('.settings-section');
const btnSaveSettings = document.getElementById('btnSaveSettings');
// Download button removed as server now handles local file saving
const tgChatIdsInput = document.getElementById('tgChatIds');
const btnRevealToken = document.getElementById('btnRevealToken');
const botTokenDisplay = document.getElementById('botTokenDisplay');
const logFeed = document.getElementById('logFeed');
const btnClearLog = document.getElementById('btnClearLog');

let isRunning = false;

// ── Helpers ───────────────────────────────────────────────────────────────────

function formatTime(isoOrNow) {
    const d = isoOrNow ? new Date(isoOrNow) : new Date();
    return d.toLocaleTimeString('en-US', { hour12: false });
}

function appendLog(message, level = 'info', timestamp = null) {
    if (!logFeed) return;
    const timeStr = formatTime(timestamp);
    const div = document.createElement('div');
    div.className = `log-entry log-${level}`;
    div.innerHTML = `
      <span class="log-time">[${timeStr}]</span>
      <span class="log-msg">${message}</span>
    `;
    logFeed.appendChild(div);
    if (logFeed.children.length > 50) logFeed.firstChild.remove();
    logFeed.scrollTop = logFeed.scrollHeight;
    console.log(`[${level}] ${message}`);
}

function setRunningState(running, scheduled = false) {
    isRunning = running;
    btnStart.disabled = running;
    btnStop.disabled = !running;

    if (scheduled && !running) {
        statusDot.className = 'status-indicator running';
        statusDot.title = 'Agent Scheduled';
        footerStatus.textContent = 'Scheduled';
    } else {
        statusDot.className = 'status-indicator ' + (running ? 'running' : '');
        statusDot.title = running ? 'Agent running…' : 'Agent idle';
        footerStatus.textContent = running ? 'Running…' : 'Idle';
    }
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
    chrome.runtime.sendMessage({ type: MSG.START_QUEUE }, (response) => {
        if (chrome.runtime.lastError) {
            console.error('Could not reach background:', chrome.runtime.lastError.message);
            setRunningState(false);
        }
    });
});

btnStop.addEventListener('click', () => {
    chrome.runtime.sendMessage({ type: MSG.STOP_QUEUE }, () => {
        setRunningState(false);
        footerStatus.textContent = 'Stopped';
        statusDot.className = 'status-indicator error';
    });
});

btnSaveAutomation.addEventListener('click', () => {
    const gap = parseInt(automationGapInput.value, 10);
    if (!gap || gap < 1) {
        alert('Please enter a valid automation gap in minutes (minimum 1).');
        return;
    }

    chrome.storage.local.set({
        automationGapMinutes: gap,
        automationEnabled: true
    }, () => {
        btnDisableAutomation.disabled = false;
        setRunningState(false, true); // Visual indicator that it's scheduled

        chrome.runtime.sendMessage({ type: 'UPDATE_AUTOMATION' }, () => {
            if (chrome.runtime.lastError) {
                console.error("Failed to update automation:", chrome.runtime.lastError);
            }
        });
    });
});

btnDisableAutomation.addEventListener('click', () => {
    chrome.storage.local.set({ automationEnabled: false }, () => {
        btnDisableAutomation.disabled = true;
        setRunningState(false);

        chrome.runtime.sendMessage({ type: 'UPDATE_AUTOMATION' });
    });
});

if (btnClearLog) {
    btnClearLog.addEventListener('click', () => {
        if (logFeed) logFeed.innerHTML = '';
    });
}

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

btnSaveSettings.addEventListener('click', () => {
    const botToken = '8653643537:AAH4kaIH-mEQIB_hZ-FWPuM3B-eyUWrtYsc';
    const chatIds = tgChatIdsInput.value.trim();

    if (!chatIds) {
        appendLog('⚠️ Please provide Chat IDs.', 'warn');
        return;
    }

    chrome.storage.local.set({
        telegram_config: {
            botToken,
            chatIds
        }
    }, () => {
        appendLog('✅ Telegram configuration saved locally.', 'success');
        // Optionally send to backend immediately if needed, 
        // but it will be sent with the next action anyway.
    });
});

btnToggleSettings.addEventListener('click', () => {
    settingsSection.classList.toggle('open');
});

if (btnRevealToken && botTokenDisplay) {
    btnRevealToken.addEventListener('click', () => {
        botTokenDisplay.style.display = botTokenDisplay.style.display === 'none' ? 'block' : 'none';
    });
}

// ── On popup open: sync with background state ─────────────────────────────────

chrome.runtime.sendMessage({ type: MSG.GET_STATUS }, (response) => {
    if (chrome.runtime.lastError || !response) return;
    setRunningState(response.isRunning);
    updateStats(response);
    if (response.isRunning) {
        appendLog('⚙️ Agent is currently running…', 'info');
    }
});

chrome.storage.local.get(['recruitpulse_stats', 'telegram_config', 'automationGapMinutes', 'automationEnabled'], (result) => {
    const stats = result['recruitpulse_stats'];
    if (stats) {
        statSuccess.textContent = stats.success || 0;
        statFailed.textContent = stats.failed || 0;
        if (stats.lastRun) {
            footerStatus.textContent = `Last run: ${formatTime(stats.lastRun)}`;
        }
    }

    const tgConfig = result['telegram_config'];
    if (tgConfig) {
        tgChatIdsInput.value = tgConfig.chatIds || '';
    }

    if (result.automationGapMinutes) {
        automationGapInput.value = result.automationGapMinutes;
    }

    if (result.automationEnabled) {
        btnDisableAutomation.disabled = false;
        setRunningState(false, true);
    } else {
        btnDisableAutomation.disabled = true;
    }
});
