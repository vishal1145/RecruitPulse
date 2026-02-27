/**
 * content/resumeBuilder.js
 * Automates the JD Resume Builder flow.
 */

const MSG = {
    BUILD_RESUME: 'BUILD_RESUME',
    RESUME_BUILT: 'RESUME_BUILT',
    START_PDF_EXPORT: 'START_PDF_EXPORT',
    DOWNLOAD_FILE: 'DOWNLOAD_FILE',
};

const RESUME_BUILDER_SELECTORS = {
    CREATE_NEW_BTN: 'button', // Will filter by text "+ Create New"
    RESUME_COMBOBOX: 'button[role="combobox"]',
    DROPDOWN_OPTIONS: '[role="listbox"] [role="option"], [class*="select-content"] [role="option"]',
    COMPANY_INPUT: 'input#company',
    ROLE_INPUT: 'input#role',
    JD_TEXTAREA: 'textarea[placeholder*="Paste the complete job description"]',
    ANALYZE_JD_BTN: 'button', // Will filter by text "Analyze JD"
    EXPORT_PDF_BTN: 'button', // Will filter by text "Export PDF"
    LOADER: '[class*="spinner"], [class*="loading"], [role="status"]',
    MATCH_SCORE: '[class*="match-score"], [class*="score"]',
};

const DEBUG = true;

function log(level, message, data = null) {
    const timestamp = new Date().toISOString();
    const prefix = `[RecruitPulse][ResumeBuilder][${level}][${timestamp}]`;
    if (data) {
        console.log(prefix, message, data);
    } else {
        console.log(prefix, message);
    }
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function waitForCondition(fn, timeout = 5000) {
    const start = Date.now();
    while (Date.now() - start < timeout) {
        if (fn()) return true;
        await sleep(200);
    }
    throw new Error("Condition timeout");
}

function waitForElement(selector, root = document, timeout = 10000) {
    return new Promise((resolve) => {
        const el = root.querySelector(selector);
        if (el) return resolve(el);

        const observer = new MutationObserver((mutations, obs) => {
            const element = root.querySelector(selector);
            if (element) {
                obs.disconnect();
                resolve(element);
            }
        });
        observer.observe(root, { childList: true, subtree: true });
        setTimeout(() => {
            observer.disconnect();
            resolve(null);
        }, timeout);
    });
}

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

// ─── Main Logic ─────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.type === MSG.BUILD_RESUME) {
        handleBuildResume(msg.job)
            .then(result => sendResponse(result))
            .catch(err => sendResponse({ error: err.message }));
        return true;
    }

    if (msg.type === MSG.START_PDF_EXPORT) {
        exportResumePDF(msg.job)
            .then(result => sendResponse(result))
            .catch(err => sendResponse({ error: err.message }));
        return true;
    }
});

async function handleBuildResume(job) {
    log('INFO', `Starting resume build for: ${job.title} @ ${job.company}`);

    try {
        // 0. Wait for correct URL
        log('INFO', 'Waiting for /jd-resume-builder URL...');
        await waitForCondition(() => window.location.href.includes('/jd-resume-builder'), 30000);

        // 1. Wait for Create New OR Combobox to render (ensures React is done)
        log('INFO', 'Waiting for page elements to render...');
        let createNewBtn = null;
        let combobox = null;

        await waitForCondition(() => {
            createNewBtn = Array.from(document.querySelectorAll(RESUME_BUILDER_SELECTORS.CREATE_NEW_BTN))
                .find(btn => btn.innerText.includes("Create New"));
            combobox = document.querySelector(RESUME_BUILDER_SELECTORS.RESUME_COMBOBOX);
            return createNewBtn || combobox;
        }, 30000);

        // 2. Click "Create New" if needed
        if (createNewBtn && !combobox) {
            log('INFO', 'Clicking "Create New"...');
            simulateRealClick(createNewBtn);
            await sleep(500); // Give it a moment to transition
        }

        // 3. Wait for form/combobox
        log('INFO', 'Waiting for resume combobox...');
        combobox = await waitForElement(RESUME_BUILDER_SELECTORS.RESUME_COMBOBOX, document, 15000);
        if (!combobox) throw new Error('Resume select combobox not found');

        await sleep(1000 + Math.random() * 500);

        // 3. Select Last Resume
        log('INFO', 'Opening resume dropdown...');
        simulateRealClick(combobox);
        await sleep(1500);

        const firstOption = await waitForElement(RESUME_BUILDER_SELECTORS.DROPDOWN_OPTIONS, document, 5000);
        if (!firstOption) throw new Error('No resumes found in dropdown');

        const options = document.querySelectorAll(RESUME_BUILDER_SELECTORS.DROPDOWN_OPTIONS);
        const lastOption = options[options.length - 1];

        log('INFO', 'Selecting last resume...');
        simulateRealClick(lastOption);
        await sleep(2000); // Give time for content to load

        // 4. Fill Job Details
        log('INFO', 'Filling job details...');
        const companyInput = document.querySelector(RESUME_BUILDER_SELECTORS.COMPANY_INPUT);
        const roleInput = document.querySelector(RESUME_BUILDER_SELECTORS.ROLE_INPUT);
        const jdTextarea = document.querySelector(RESUME_BUILDER_SELECTORS.JD_TEXTAREA);

        if (!companyInput || !roleInput || !jdTextarea) {
            throw new Error('Form fields (Company/Role/JD) not found');
        }

        const typeHumanly = async (input, value) => {
            input.value = value;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
            await sleep(800 + Math.random() * 400);
        };

        await typeHumanly(companyInput, job.company);
        await typeHumanly(roleInput, job.title);
        await typeHumanly(jdTextarea, job.fullDescription);

        await sleep(2000); // "Reviewing"

        // 5. Click "Analyze JD"
        const analyzeBtn = Array.from(document.querySelectorAll(RESUME_BUILDER_SELECTORS.ANALYZE_JD_BTN))
            .find(btn => btn.innerText.includes("Analyze JD"));

        if (!analyzeBtn) throw new Error('"Analyze JD" button not found');

        log('INFO', 'Clicking "Analyze JD"...');
        simulateRealClick(analyzeBtn);

        // 6. Wait dynamically for AI generation and specific "Apply All Suggestions" button
        log('INFO', 'Waiting for AI generation to complete and specific "Apply All Suggestions" button...');

        let applyBtn = null;
        let attempts = 0;
        const maxAttempts = 60; // 60 * 500ms = 30 seconds

        const findSpecificApplyBtn = () => {
            // Find the h3 that says "AI Suggestions"
            const headers = Array.from(document.querySelectorAll("h3"));
            const aiHeader = headers.find(h => h.innerText.trim() === "AI Suggestions");

            if (aiHeader && aiHeader.parentElement && aiHeader.parentElement.parentElement) {
                // Look in the direct parent or the parent's parent for the button
                // (Based on the image showing h3 and button in the same flex container)
                const container = aiHeader.parentElement;
                return Array.from(container.querySelectorAll("button"))
                    .find(btn => btn.innerText.includes("Apply All Suggestions"));
            }
            return null;
        };

        while (!applyBtn && attempts < maxAttempts) {
            applyBtn = findSpecificApplyBtn();

            if (!applyBtn) {
                await new Promise(r => setTimeout(r, 500));
                attempts++;
                if (attempts % 10 === 0) { // Log every 5 seconds
                    log('INFO', `Still waiting for 'Apply' button... (${attempts / 2}s elapsed)`);
                }
            }
        }

        // 7. Click if found
        if (applyBtn) {
            log('INFO', 'Found "Apply All Suggestions" button, clicking...');
            applyBtn.scrollIntoView({ behavior: 'smooth', block: "center" });
            await sleep(1000); // Give scroll time to finish seamlessly
            simulateRealClick(applyBtn);
            await sleep(1500);
        } else {
            log('WARN', '"Apply All Suggestions" button not found after 30 seconds. Continuing safely.');
        }

        // 8. Automatic PDF Generation & Download (Backend-driven)
        try {
            log('INFO', 'Starting automatic backend PDF generation...');
            const exportResult = await generateAndDownloadResumePDF(job);
            log('INFO', 'PDF generation result:', exportResult);
        } catch (err) {
            log('ERROR', `Automatic PDF generation failed (non-blocking): ${err.message}`);
        }

        log('INFO', 'Resume build completed successfully!');
        return { success: true };

    } catch (err) {
        log('ERROR', `Resume build failed: ${err.message}`);
        throw err;
    }
}

/**
 * Modern Backend-Driven PDF Generation Pipeline
 * Extracts HTML, sends to backend for PDF generation and automatic emailing.
 */
async function generateAndDownloadResumePDF(job) {
    log('INFO', `Requesting backend PDF & Email pipeline for: ${job.title}`);

    try {
        // 1. Extract Resume HTML
        const resumePreview = document.querySelector('div#resume-preview');
        if (!resumePreview) {
            throw new Error('Resume preview container (#resume-preview) not found');
        }

        const resumeHtml = resumePreview.outerHTML;
        log('INFO', `Extracted resume HTML (${resumeHtml.length} chars)`);

        // 2. Send to Backend
        const API_URL = 'https://recruitpulse.algofolks.com/api/generate-resume-pdf';

        // Fetch Telegram configuration from storage
        const storage = await chrome.storage.local.get(['telegram_config']);
        const telegramConfig = storage.telegram_config || null;

        const response = await fetch(API_URL, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                jobId: job.jobId,
                title: job.title,
                company: job.company,
                applyEmail: job.applyEmail,
                emailSubject: job.emailSubject,
                emailBody: job.emailBody,
                resumeHtml: resumeHtml,
                telegram_config: telegramConfig
            })
        });

        const result = await response.json();
        if (!result.success) {
            throw new Error(`Backend failed: ${result.error}`);
        }

        log('INFO', `Backend success! EmailSent: ${result.emailSent}`, result.filename);

        if (result.emailSent) {
            log('INFO', 'Email was successfully sent with PDF attachment.');
        } else {
            log('WARN', `PDF was generated (${result.filename}) but email failed: ${result.error || 'Unknown error'}`);
        }

        // 3. User convenience download (Simple, no background orchestration needed)
        const filename = `RecruitPulse_${job.jobId}.pdf`;
        log('INFO', `Triggering convenience download: ${filename}`);

        await chrome.runtime.sendMessage({
            type: MSG.DOWNLOAD_FILE,
            url: result.downloadUrl,
            filename: filename
        });

        return { success: true, emailSent: result.emailSent, filename: filename };

    } catch (err) {
        log('ERROR', `generateAndDownloadResumePDF failed: ${err.message}`);
        return { success: false, error: err.message };
    }
}

// Expose for testing
window.generateAndDownloadResumePDF = generateAndDownloadResumePDF;
