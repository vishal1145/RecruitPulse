/**
 * content/extractors/indeed.js
 *
 * Injected into Indeed job posting pages.
 * Extracts: full job description, location, experience, apply email.
 * Reports result via chrome.runtime.sendMessage → background.js
 */

(async function indeedExtractor() {
    'use strict';

    const LOG = '[RecruitPulse][Indeed]';
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const safeText = el => el ? el.textContent.trim() : '';

    function log(msg, data) {
        console.log(`${LOG} ${msg}`, data !== undefined ? data : '');
    }

    function extractEmails(text) {
        const matches = text.match(/[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g) || [];
        return [...new Set(matches)];
    }

    // ── Experience heuristic ────────────────────────────────────────────────

    /**
     * Attempt to find an experience requirement in the job description text.
     * Looks for patterns like "3+ years", "5 years of experience", etc.
     * @param {string} text
     * @returns {string}
     */
    function parseExperience(text) {
        const patterns = [
            /(\d+[\+\-]?\s*(?:to\s*\d+)?\s*years?\s*(?:of\s*)?(?:experience|exp)?)/i,
            /(entry[- ]level|junior|mid[- ]level|senior|lead|principal)/i,
            /(no\s*experience\s*required|experienced\s*professional)/i,
        ];
        for (const re of patterns) {
            const m = text.match(re);
            if (m) return m[0].trim();
        }
        return '';
    }

    // ── Main extraction ─────────────────────────────────────────────────────

    try {
        log('Page detected. Waiting for content…');
        await sleep(2000);

        // ── Full Job Description ─────────────────────────────────────────────
        let descEl = null;
        const descSelectors = [
            '#jobDescriptionText',
            '[id*="jobDescription"]',
            '[class*="jobDescription"]',
            '[class*="job-description"]',
            '.jobsearch-jobDescriptionText',
            '[data-testid="jobDescriptionText"]',
            'article',
            'main',
        ];
        for (const s of descSelectors) {
            descEl = document.querySelector(s);
            if (descEl) break;
        }

        const fullDescription = safeText(descEl);

        // ── Location ────────────────────────────────────────────────────────
        const locationEl = document.querySelector(
            '[data-testid="jobsearch-JobInfoHeader-companyLocation"],' +
            '[class*="companyLocation"], [class*="job-location"],' +
            '[id*="jobLocation"], .jobsearch-CompanyInfoContainer [class*="location"]'
        );
        const location = safeText(locationEl);

        // ── Experience ───────────────────────────────────────────────────────
        const experience = parseExperience(fullDescription);

        // ── Apply Email ─────────────────────────────────────────────────────
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
