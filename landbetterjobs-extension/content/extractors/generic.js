/**
 * content/extractors/generic.js
 *
 * Fallback extractor for any job site other than LinkedIn/Indeed.
 * Uses heuristic keyword matching and regex on page text.
 * Reports result via chrome.runtime.sendMessage → background.js
 */

(async function genericExtractor() {
    'use strict';

    const LOG = '[RecruitPulse][Generic]';
    const sleep = ms => new Promise(r => setTimeout(r, ms));
    const safeText = el => el ? el.textContent.trim() : '';

    function log(msg, data) {
        console.log(`${LOG} ${msg}`, data !== undefined ? data : '');
    }

    // ── Email extraction ────────────────────────────────────────────────────

    function extractEmails(text) {
        const matches = text.match(/[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}/g) || [];
        return [...new Set(matches)];
    }

    // ── Experience heuristic ─────────────────────────────────────────────────

    function parseExperience(text) {
        const patterns = [
            /(\d+[\+\-]?\s*(?:to\s*\d+)?\s*years?\s*(?:of\s*)?(?:experience|exp)?)/i,
            /(entry[- ]level|junior|mid[- ]level|senior|lead|principal|fresher|graduate)/i,
            /(no\s*experience\s*required|experienced\s*professional|new\s*grad)/i,
        ];
        for (const re of patterns) {
            const m = text.match(re);
            if (m) return m[0].trim();
        }
        return '';
    }

    // ── Location heuristic ───────────────────────────────────────────────────

    /**
     * Attempt to find a location in page text.
     * Looks for labelled elements first, then raw patterns.
     */
    function parseLocation(text) {
        // Pattern: "Location: New York, NY" or "📍 Remote"
        const labelled = text.match(/(?:location|where|place|city|office)\s*[:\–\-]\s*([^\n\r]{2,60})/i);
        if (labelled) return labelled[1].trim();

        // Remote keyword
        if (/\bremote\b/i.test(text)) return 'Remote';

        return '';
    }

    // ── Best description block ───────────────────────────────────────────────

    /**
     * Find the largest visible text block that likely contains the job description.
     * Prefers elements with JD-related class names, then falls back to the
     * longest paragraph/div.
     */
    function findBestDescriptionBlock() {
        // Priority selectors
        const prioritySelectors = [
            '[class*="job-description"]', '[class*="jobDescription"]', '[class*="jd-content"]',
            '[class*="description"]', '[id*="description"]', '[class*="detail"]',
            '[class*="content"]', 'article', '[role="main"]', 'main',
        ];

        for (const s of prioritySelectors) {
            const el = document.querySelector(s);
            if (el && el.textContent.trim().length > 200) return el;
        }

        // Fallback: find the visible element with the most text (heuristic)
        let best = null;
        let bestLen = 0;
        const candidates = document.querySelectorAll('div, section, article, p');
        for (const el of candidates) {
            // Skip navigation, headers, footers
            if (el.closest('nav, header, footer, script, style')) continue;
            const len = el.textContent.trim().length;
            if (len > bestLen) { bestLen = len; best = el; }
        }
        return best;
    }

    // ── Main extraction ─────────────────────────────────────────────────────

    try {
        log('Page detected (generic). Waiting for content…');
        await sleep(2000);

        const descEl = findBestDescriptionBlock();
        const fullDescription = safeText(descEl);

        const pageText = document.body.innerText;
        const emails = extractEmails(pageText);
        const applyEmail = emails[0] || '';
        const location = parseLocation(pageText);
        const experience = parseExperience(fullDescription || pageText);

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
