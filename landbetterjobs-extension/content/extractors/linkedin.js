/**
 * content/extractors/linkedin.js
 *
 * Injected into LinkedIn job posting pages.
 * Extracts: full job description, location, experience level, apply email.
 * Reports result via chrome.runtime.sendMessage → background.js
 */

(async function linkedinExtractor() {
    'use strict';

    const LOG = '[RecruitPulse][LinkedIn]';
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const safeText = el => el ? el.textContent.trim() : '';

    function log(msg, data) {
        console.log(`${LOG} ${msg}`, data !== undefined ? data : '');
    }

    // ── Wait for element ────────────────────────────────────────────────────

    function waitForElement(selector, timeout = 15000) {
        return new Promise((resolve, reject) => {
            const el = document.querySelector(selector);
            if (el) return resolve(el);

            const obs = new MutationObserver(() => {
                const found = document.querySelector(selector);
                if (found) { obs.disconnect(); clearTimeout(t); resolve(found); }
            });
            obs.observe(document.body, { childList: true, subtree: true });
            const t = setTimeout(() => { obs.disconnect(); reject(new Error(`Timeout: ${selector}`)); }, timeout);
        });
    }

    // ── Email extraction ────────────────────────────────────────────────────

    function extractEmails(text) {
        const matches = text.match(/[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g) || [];
        return [...new Set(matches)];
    }

    // ── Main extraction ─────────────────────────────────────────────────────

    try {
        log('Waiting for page to stabilize…');
        await sleep(2000);

        // --- 1. Identify Target Container ---
        const postContainer = document.querySelector('div.feed-shared-update-v2')
            || document.querySelector('.jobs-description__content')
            || document.querySelector('#job-details')
            || document.querySelector('article');

        if (postContainer) {
            // --- 2. Expand "See more" if needed ---
            const seeMoreBtn = postContainer.querySelector('button[aria-label*="more"], button[class*="see-more"]');
            if (seeMoreBtn) { seeMoreBtn.click(); await sleep(500); }
        }

        let fullDescription = '';

        // --- 3. Extract Post Content (Ignore Comments/Social) ---
        if (postContainer) {
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

        const pageText = document.body.innerText;
        const emails = extractEmails(pageText);
        const applyEmail = emails[0] || '';

        const result = { fullDescription, location, experience, applyEmail };
        log('Extraction complete', result);

        chrome.runtime.sendMessage({ type: 'EXTERNAL_DATA', data: result });

    } catch (err) {
        console.error(`${LOG} Extraction failed`, err);
        chrome.runtime.sendMessage({
            type: 'EXTERNAL_DATA',
            data: { fullDescription: '', location: '', experience: '', applyEmail: '', error: err.message },
        });
    }

})();
