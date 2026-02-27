/**
 * api.js
 * Handles all communication with the RecruitPulse backend API.
 */

import { API_ENDPOINT, MAX_RETRIES, RETRY_BASE_DELAY_MS } from './constants.js';
import { log, retry } from './helpers.js';

/**
 * Sends a structured job data object to the backend API.
 *
 * @param {Object} jobData - The fully merged job record to send
 * @param {string} jobData.jobId            - Unique job identifier
 * @param {string} jobData.title            - Job title
 * @param {string} jobData.company          - Company name
 * @param {string} jobData.hiringManager    - Hiring manager name
 * @param {string} jobData.shortDescription - Short description from the dashboard popup
 * @param {string} jobData.viewFullPostUrl  - URL of the external job posting
 * @param {string} jobData.fullDescription  - Full job description from the external page
 * @param {string} jobData.applyEmail       - Apply email (if found)
 * @param {string} jobData.location         - Job location
 * @param {string} jobData.experience       - Required experience
 * @param {string} jobData.source           - 'linkedin' | 'indeed' | 'generic'
 * @param {string} jobData.processedAt      - ISO timestamp
 *
 * @returns {Promise<{ success: boolean, response?: Object, error?: string }>}
 */
export async function sendJobToAPI(jobData) {
    log('INFO', 'Sending job to API', { jobId: jobData.jobId, title: jobData.title });

    try {
        const result = await retry(
            async () => {
                // Fetch Telegram configuration from storage
                const storage = await chrome.storage.local.get(['telegram_config']);
                const telegramConfig = storage.telegram_config || null;

                const response = await fetch(API_ENDPOINT, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-Source': 'recruitpulse-extension',
                    },
                    body: JSON.stringify({
                        ...jobData,
                        telegram_config: telegramConfig
                    }),
                });

                if (!response.ok) {
                    const body = await response.text().catch(() => '');
                    throw new Error(`API error ${response.status}: ${body}`);
                }

                const json = await response.json().catch(() => ({}));
                return json;
            },
            MAX_RETRIES,
            RETRY_BASE_DELAY_MS
        );

        log('INFO', 'Job sent successfully', { jobId: jobData.jobId, result });
        return { success: true, response: result };

    } catch (err) {
        log('ERROR', 'Failed to send job to API after retries', {
            jobId: jobData.jobId,
            error: err.message,
        });
        return { success: false, error: err.message };
    }
}
