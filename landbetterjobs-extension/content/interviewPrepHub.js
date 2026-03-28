/**
 * content/interviewPrepHub.js
 * Automates the Interview Preparation Hub form filling.
 */

// Flag to skip creation flow (user is on Free Plan, creation is blocked)
const SKIP_CREATION = false;

const MSG = {
    FILL_INTERVIEW_PREP: 'FILL_INTERVIEW_PREP',
};

const INTERVIEW_PREP_SELECTORS = {
    PREPARE_BTN: 'button',
    MODAL_FORM: '[role="dialog"], [class*="modal"], [class*="dialog"]',
    RESUME_DROPDOWN: 'button[role="combobox"]',
    DROPDOWN_OPTIONS: '[role="listbox"] [role="option"], [class*="select-content"] [role="option"]',
    POSITION_INPUT: 'input[name="position"], input#position, input[placeholder*="position" i]',
    COMPANY_INPUT: 'input[name="company"], input#company, input[placeholder*="company" i]',
    COMPANY_URL_INPUT: 'input[name="companyUrl"], input#companyUrl, input[placeholder*="url" i]',
    JD_TEXTAREA: 'textarea[name="jobDescription"], textarea#jobDescription, textarea[placeholder*="description" i]',
    COMPANY_DETAILS_TEXTAREA: 'textarea[name="companyDetails"], textarea#companyDetails, textarea[placeholder*="company details" i]',
    SUBMIT_BTN: 'button[type="submit"]',
};

const DEBUG = true;

function log(level, message, data = null) {
    const timestamp = new Date().toISOString();
    const prefix = `[RecruitPulse][InterviewPrepHub][${level}][${timestamp}]`;
    if (data) {
        console.log(prefix, message, data);
    } else {
        console.log(prefix, message);
    }
}

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

async function waitForCondition(fn, timeout = 0) {
    const start = Date.now();
    let iteration = 0;
    while (true) {
        if (fn()) return true;
        
        // If timeout is 0, wait indefinitely
        if (timeout > 0 && (Date.now() - start >= timeout)) {
            throw new Error("Condition timeout");
        }
        
        await sleep(200);
        iteration++;
        
        // Log progress every 10 seconds when waiting indefinitely
        if (timeout === 0 && iteration % 50 === 0) {
            log('INFO', `Still waiting... (${Math.floor((Date.now() - start) / 1000)}s elapsed)`);
        }
    }
}

function waitForElement(selector, root = document, timeout = 0) {
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
        
        // Only set timeout if timeout > 0, otherwise wait indefinitely
        if (timeout > 0) {
            setTimeout(() => {
                observer.disconnect();
                resolve(null);
            }, timeout);
        }
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

// ─── Questions Tab Scraper ──────────────────────────────────────────────────

async function scrapeQuestionsTab() {
    log('INFO', 'Starting Questions tab scraping...');
    
    // Helper functions with MutationObserver for reliability
    function waitForAttribute(el, attr, value, timeout = 4000) {
        return new Promise(resolve => {
            if (el.getAttribute(attr) === value) return resolve(true);
            const observer = new MutationObserver(() => {
                if (el.getAttribute(attr) === value) {
                    observer.disconnect();
                    resolve(true);
                }
            });
            observer.observe(el, { attributes: true, attributeFilter: [attr] });
            setTimeout(() => {
                observer.disconnect();
                resolve(false); // timeout but DO NOT stop the loop
            }, timeout);
        });
    }
    
    function waitForHiddenRemoved(el, timeout = 4000) {
        return new Promise(resolve => {
            if (!el.hasAttribute('hidden')) return resolve(true);
            const observer = new MutationObserver(() => {
                if (!el.hasAttribute('hidden')) {
                    observer.disconnect();
                    resolve(true);
                }
            });
            observer.observe(el, { attributes: true, attributeFilter: ['hidden'] });
            setTimeout(() => {
                observer.disconnect();
                resolve(false);
            }, timeout);
        });
    }
    
    function sleepLocal(ms) {
        return new Promise(resolve => setTimeout(resolve, ms));
    }
    
    try {
        // STEP 1: Click the Questions tab
        log('INFO', 'Looking for Questions tab...');
        const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
        const questionsTab = tabs.find(tab => tab.textContent.trim().toLowerCase().includes('questions'));
        
        if (!questionsTab) {
            throw new Error('Questions tab not found');
        }
        
        log('INFO', 'Clicking Questions tab...');
        simulateRealClick(questionsTab);
        await sleep(500);
        
        // STEP 2: Wait for Questions panel to be active
        log('INFO', 'Waiting for Questions panel to load...');
        await waitForCondition(() => {
            const panel = document.querySelector('[data-state="active"][role="tabpanel"]');
            if (!panel) return false;
            const hasCards = panel.querySelectorAll('div[data-orientation="vertical"]').length > 0;
            return hasCards;
        }, 0);
        
        log('INFO', 'Questions panel loaded successfully');
        await sleep(1000);
        
        // STEP 3: Extract Fit Analysis (FIX 5)
        log('INFO', 'Extracting Fit Analysis...');
        const allHeadings = Array.from(document.querySelectorAll('h2, h3, h4'));
        const fitHeading = allHeadings.find(h => 
            h.textContent.trim().includes('Fit Analysis'));
        let fitAnalysis = '';
        if (fitHeading) {
            const container = fitHeading.closest('div[class*="card"], div[class*="rounded"], section') 
                              || fitHeading.parentElement;
            fitAnalysis = container.innerText
                .replace('Fit Analysis', '')
                .trim();
        }
        log('INFO', `Fit Analysis length: ${fitAnalysis.length} chars`);
        
        // STEP 4: Get the active questions panel (FIX 2)
        const panel = document.querySelector('[data-state="active"][role="tabpanel"]');
        if (!panel) {
            throw new Error('Questions panel not found');
        }
        
        // Get ALL accordion wrapper divs — these are the direct question cards
        const allCards = Array.from(
            panel.querySelectorAll('div[data-orientation="vertical"][data-state]')
        ).filter(div => div.querySelector('button[aria-expanded]'));
        
        log('INFO', `Found ${allCards.length} total question cards`);
        
        // STEP 5: Build section map BEFORE the card loop (FIX 4)
        const cardSectionMap = new Map();
        let currentSection = 'General';
        
        function walkForSections(node) {
            for (const child of node.children) {
                // Is it a section heading? (h2/h3 with no button inside)
                if ((child.tagName === 'H2' || child.tagName === 'H3') && 
                    !child.querySelector('button[aria-expanded]')) {
                    const text = child.textContent.trim();
                    if (text.length > 0 && text.length < 100) {
                        currentSection = text;
                        log('INFO', `Section found: "${currentSection}"`);
                    }
                }
                // Is it a question card?
                else if (child.hasAttribute('data-orientation') && 
                         child.querySelector('button[aria-expanded]')) {
                    cardSectionMap.set(child, currentSection);
                }
                // Otherwise recurse into it
                else {
                    walkForSections(child);
                }
            }
        }
        walkForSections(panel);
        log('INFO', `Section map built for ${cardSectionMap.size} cards`);
        
        // STEP 6: Extract data from each card (FIX 3 - NEVER stop on errors)
        const questions = [];
        
        for (let i = 0; i < allCards.length; i++) {
            const card = allCards[i];
            log('INFO', `[Card ${i+1}/${allCards.length}] Processing...`);
            
            try {
                // Get the button
                const button = card.querySelector('button[aria-expanded]');
                if (!button) {
                    log('INFO', `[Card ${i+1}] No button found, skipping`);
                    continue;
                }
                
                // Extract question text from button (remove tag spans)
                const btnClone = button.cloneNode(true);
                btnClone.querySelectorAll('span, svg, [aria-hidden]').forEach(el => el.remove());
                const rawQuestion = btnClone.textContent.trim();
                const question = rawQuestion.replace(/^\d+[\.\)]\s*/, '').trim();
                
                if (!question) {
                    log('INFO', `[Card ${i+1}] Empty question, skipping`);
                    continue;
                }
                log('INFO', `[Card ${i+1}] Question: "${question.substring(0, 60)}..."`);
                
                // Expand if not already open
                const isExpanded = button.getAttribute('aria-expanded') === 'true';
                if (!isExpanded) {
                    log('INFO', `[Card ${i+1}] Clicking to expand...`);
                    button.click();
                    
                    // Wait for the region div to lose its hidden attribute
                    const region = card.querySelector('[role="region"]');
                    if (region) {
                        await waitForHiddenRemoved(region, 4000);
                    } else {
                        // fallback wait
                        await sleepLocal(600);
                    }
                }
                
                // Extra wait for content to render
                await sleepLocal(300);
                
                // Extract answer
                const region = card.querySelector('[role="region"]');
                let answer = '';
                if (region && !region.hidden) {
                    answer = region.innerText.trim();
                } else if (region) {
                    // Try getting text even if still hidden
                    answer = region.textContent.trim();
                }
                log('INFO', `[Card ${i+1}] Answer length: ${answer.length} chars`);
                
                // Extract tags — try multiple selectors
                let tags = [];
                
                // Method 1: spans with specific text values
                const allSpans = Array.from(card.querySelectorAll('span'));
                const knownTags = ['behavioral','technical','hard','medium','easy',
                                   'situational','cultural','analytical','leadership',
                                   'strategic','operational'];
                tags = allSpans
                    .map(s => s.textContent.trim().toLowerCase())
                    .filter(t => knownTags.includes(t));
                
                // Method 2: fallback — any small span that looks like a badge
                if (tags.length === 0) {
                    tags = allSpans
                        .filter(s => s.className && 
                                     (s.className.includes('badge') || 
                                      s.className.includes('tag') ||
                                      s.className.includes('pill')))
                        .map(s => s.textContent.trim().toLowerCase());
                }
                
                const difficulty = tags.find(t => 
                    ['hard','medium','easy'].includes(t)) || null;
                
                log('INFO', `[Card ${i+1}] Tags: ${JSON.stringify(tags)}`);
                
                questions.push({
                    section: cardSectionMap.get(card) || 'General',
                    question,
                    answer,
                    tags,
                    difficulty
                });
                
                log('INFO', `[Card ${i+1}] ✓ Successfully extracted`);
                
            } catch (err) {
                // CRITICAL: never stop the loop on error
                log('ERROR', `[Card ${i+1}] Error: ${err.message}`);
                continue;
            }
            
            // Delay between cards to not overwhelm Radix UI animations
            await sleepLocal(500);
        }
        
        log('INFO', `=== Extraction complete. Total questions: ${questions.length} ===`);
        
        const allQuestions = questions;
        
        // STEP 6: Build final result
        const result = {
            fitAnalysis: fitAnalysis,
            questions: allQuestions,
            scrapedAt: new Date().toISOString(),
            sourceUrl: window.location.href
        };
        
        log('INFO', `✓ Scraping complete! Extracted ${allQuestions.length} questions`);
        log('INFO', 'Full scraped data:', result);
        
        // STEP 7: Store in chrome.storage.local
        await chrome.storage.local.set({ interviewPrepData: result });
        log('INFO', 'Data saved to chrome.storage.local');
        
        return result;
        
    } catch (err) {
        log('ERROR', `Questions tab scraping failed: ${err.message}`);
        throw err;
    }
}

// ─── Insights Tab Scraper ───────────────────────────────────────────────────

async function scrapeInsightsTab() {
    log('INFO', 'Starting Insights tab scraping...');
    
    try {
        // STEP 1: Click the Insights tab
        log('INFO', 'Looking for Insights tab...');
        const tabs = Array.from(document.querySelectorAll('[role="tab"]'));
        const insightsTab = tabs.find(tab => tab.textContent.trim().toLowerCase().includes('insights'));
        
        if (!insightsTab) {
            throw new Error('Insights tab not found');
        }
        
        log('INFO', 'Clicking Insights tab...');
        simulateRealClick(insightsTab);
        await sleep(500);
        
        // STEP 2: Wait for Insights panel to be active
        log('INFO', 'Waiting for Insights panel to load...');
        await waitForCondition(() => {
            const panel = document.querySelector('[data-state="active"][role="tabpanel"]');
            if (!panel) return false;
            const hasContent = panel.textContent.length > 100;
            return hasContent;
        }, 0);
        
        log('INFO', 'Insights panel loaded successfully');
        await sleep(1000);
        
        // STEP 3: Get the active insights panel
        const panel = document.querySelector('[data-state="active"][role="tabpanel"]');
        if (!panel) {
            throw new Error('Insights panel not found');
        }
        
        // STEP 4: Extract Company Insights sections
        const insights = {
            companyInsights: {},
            culture: '',
            values: [],
            interviewProcess: '',
            companySpecificTips: [],
            salaryInsights: {
                estimatedRange: '',
                negotiationTips: []
            },
            redFlags: []
        };
        
        // Find all headings and their content
        const allHeadings = Array.from(panel.querySelectorAll('h2, h3, h4'));
        
        for (const heading of allHeadings) {
            const headingText = heading.textContent.trim();
            log('INFO', `Found heading: "${headingText}"`);
            
            // Get content after this heading
            let content = '';
            let nextElement = heading.nextElementSibling;
            
            // Collect content until we hit another heading
            while (nextElement && !['H2', 'H3', 'H4'].includes(nextElement.tagName)) {
                const text = nextElement.innerText?.trim() || nextElement.textContent?.trim() || '';
                if (text) {
                    content += text + '\n\n';
                }
                nextElement = nextElement.nextElementSibling;
            }
            
            content = content.trim();
            
            // Categorize based on heading text
            if (headingText.toLowerCase().includes('culture')) {
                insights.culture = content;
                log('INFO', `Culture content length: ${content.length} chars`);
            } else if (headingText.toLowerCase().includes('values')) {
                // Extract individual values (they're often in red text or list items)
                const valueElements = [];
                let elem = heading.nextElementSibling;
                while (elem && !['H2', 'H3', 'H4'].includes(elem.tagName)) {
                    // Look for text in red or list items
                    const redTexts = elem.querySelectorAll('[style*="red"], [class*="red"], li, p');
                    redTexts.forEach(el => {
                        const text = el.textContent.trim();
                        if (text && text.length < 200) { // Values are usually short
                            valueElements.push(text);
                        }
                    });
                    elem = elem.nextElementSibling;
                }
                insights.values = valueElements;
                log('INFO', `Found ${valueElements.length} values`);
            } else if (headingText.toLowerCase().includes('interview process')) {
                insights.interviewProcess = content;
                log('INFO', `Interview Process content length: ${content.length} chars`);
            } else if (headingText.toLowerCase().includes('company insights')) {
                // This is the main section, store the content
                insights.companyInsights.overview = content;
            } else if (headingText.toLowerCase().includes('company-specific tips')) {
                // Extract tips as list items
                const tips = [];
                let elem = heading.nextElementSibling;
                while (elem && !['H2', 'H3', 'H4'].includes(elem.tagName)) {
                    const listItems = elem.querySelectorAll('li, p');
                    listItems.forEach(item => {
                        const text = item.textContent.trim();
                        if (text && text.startsWith('**')) {
                            tips.push(text);
                        }
                    });
                    elem = elem.nextElementSibling;
                }
                insights.companySpecificTips = tips.length > 0 ? tips : [content];
                log('INFO', `Found ${insights.companySpecificTips.length} company-specific tips`);
            } else if (headingText.toLowerCase().includes('salary insights')) {
                // This is the main salary section
                insights.salaryInsights.overview = content;
                log('INFO', `Salary Insights overview length: ${content.length} chars`);
            } else if (headingText.toLowerCase().includes('estimated range')) {
                insights.salaryInsights.estimatedRange = content;
                log('INFO', `Estimated Range length: ${content.length} chars`);
            } else if (headingText.toLowerCase().includes('negotiation tips')) {
                // Extract negotiation tips as list items
                const tips = [];
                let elem = heading.nextElementSibling;
                while (elem && !['H2', 'H3', 'H4'].includes(elem.tagName)) {
                    const listItems = elem.querySelectorAll('li, p');
                    listItems.forEach(item => {
                        const text = item.textContent.trim();
                        if (text && text.startsWith('**')) {
                            tips.push(text);
                        }
                    });
                    elem = elem.nextElementSibling;
                }
                insights.salaryInsights.negotiationTips = tips.length > 0 ? tips : [content];
                log('INFO', `Found ${insights.salaryInsights.negotiationTips.length} negotiation tips`);
            } else if (headingText.toLowerCase().includes('red flags')) {
                // Extract red flags as list items
                const flags = [];
                let elem = heading.nextElementSibling;
                while (elem && !['H2', 'H3', 'H4'].includes(elem.tagName)) {
                    const listItems = elem.querySelectorAll('li, p');
                    listItems.forEach(item => {
                        const text = item.textContent.trim();
                        if (text) {
                            flags.push(text);
                        }
                    });
                    elem = elem.nextElementSibling;
                }
                insights.redFlags = flags.length > 0 ? flags : [content];
                log('INFO', `Found ${insights.redFlags.length} red flags`);
            }
        }
        
        // STEP 5: Build final result
        const result = {
            insights: insights,
            scrapedAt: new Date().toISOString(),
            sourceUrl: window.location.href
        };
        
        log('INFO', `✓ Insights scraping complete!`);
        log('INFO', 'Full insights data:', result);
        
        return result;
        
    } catch (err) {
        log('ERROR', `Insights tab scraping failed: ${err.message}`);
        throw err;
    }
}

// ─── Main Logic ─────────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
    if (msg.type === MSG.FILL_INTERVIEW_PREP) {
        handleFillInterviewPrep(msg.job)
            .then(result => sendResponse(result))
            .catch(err => sendResponse({ error: err.message }));
        return true;
    }
});

let isInterviewPrepProcessing = false;

async function handleFillInterviewPrep(job) {
    if (isInterviewPrepProcessing) {
        log('WARN', 'Interview prep form fill already in progress. Ignoring new request.');
        return { success: false, error: 'Form fill in progress' };
    }
    isInterviewPrepProcessing = true;
    log('INFO', `Starting interview prep processing for: ${job.title} @ ${job.company}`);

    try {
        // Store position and company for later use
        const position = job.title;
        const company = job.company;
        
        // STEP 1: Skip creation flow if flag is enabled
        if (!SKIP_CREATION) {
            // ═══════════════════════════════════════════════════════════════════════
            // CREATION FLOW (PRESERVED BUT DISABLED)
            // ═══════════════════════════════════════════════════════════════════════
            
            // 0. Wait for correct URL (no timeout - wait indefinitely)
            log('INFO', 'Waiting for /interview-preparation-hub URL...');
            log('INFO', `Current URL: ${window.location.href}`);
            await waitForCondition(() => window.location.href.includes('/interview-preparation-hub'), 0);
            log('INFO', 'URL confirmed. Waiting for page to render...');
            await sleep(3000); // Give page more time to fully render

            // 1. Find and click the "+ Prepare" button (no timeout - wait indefinitely)
            log('INFO', 'Looking for "+ Prepare" button (will wait indefinitely)...');
            let prepareBtn = null;
            let attemptCount = 0;
            
            await waitForCondition(() => {
                attemptCount++;
                const buttons = Array.from(document.querySelectorAll(INTERVIEW_PREP_SELECTORS.PREPARE_BTN));
                
                // Debug: Log all button texts every 10 seconds
                if (attemptCount % 50 === 0) {
                    log('INFO', `Attempt ${attemptCount}: Found ${buttons.length} buttons on page`);
                    const buttonTexts = buttons.slice(0, 10).map(btn => btn.innerText.trim());
                    log('INFO', `Button texts (first 10): ${JSON.stringify(buttonTexts)}`);
                }
                
                prepareBtn = buttons.find(btn => {
                    const text = btn.innerText.trim();
                    return text.includes('Prepare') || text.includes('+ Prepare') || text.toLowerCase().includes('prepare');
                });
                return prepareBtn !== null;
            }, 0); // 0 = wait indefinitely

            if (!prepareBtn) {
                throw new Error('Could not find "+ Prepare" button');
            }

            log('INFO', 'Clicking "+ Prepare" button...');
            simulateRealClick(prepareBtn);
            await sleep(1500);

            // 2. Wait for modal form to appear (no timeout - wait indefinitely)
            log('INFO', 'Waiting for modal form (will wait indefinitely)...');
            const modal = await waitForElement(INTERVIEW_PREP_SELECTORS.MODAL_FORM, document, 0);
            if (!modal) {
                throw new Error('Modal form did not appear after clicking Prepare button');
            }
            log('INFO', 'Modal form appeared!');

            await sleep(1000);

            // 3. Select resume from dropdown
            log('INFO', 'Opening resume dropdown...');
            const resumeDropdown = modal.querySelector(INTERVIEW_PREP_SELECTORS.RESUME_DROPDOWN);
            if (!resumeDropdown) {
                throw new Error('Resume dropdown not found in modal');
            }

            simulateRealClick(resumeDropdown);
            await sleep(1500);

            // 4. Find the resume option matching "{company} - {title}" pattern
            const targetResumeName = `${job.company} - ${job.title}`;
            log('INFO', `Looking for resume option: "${targetResumeName}"`);

            const options = document.querySelectorAll(INTERVIEW_PREP_SELECTORS.DROPDOWN_OPTIONS);
            let targetOption = null;

            for (const option of options) {
                const optionText = option.textContent.trim();
                if (optionText === targetResumeName) {
                    targetOption = option;
                    break;
                }
            }

            if (!targetOption) {
                log('WARN', `Resume option "${targetResumeName}" not found in dropdown. Aborting form submission.`);
                throw new Error(`Resume option "${targetResumeName}" not found. Cannot proceed with form submission.`);
            }

            log('INFO', `Selecting resume: "${targetOption.textContent.trim()}"`);
            simulateRealClick(targetOption);
            await sleep(2000);

            // 5. Fill form fields
            log('INFO', 'Filling form fields...');

            const typeHumanly = async (input, value) => {
                if (!input) return;
                input.value = value;
                input.dispatchEvent(new Event('input', { bubbles: true }));
                input.dispatchEvent(new Event('change', { bubbles: true }));
                await sleep(500 + Math.random() * 300);
            };

            // Position
            const positionInput = modal.querySelector(INTERVIEW_PREP_SELECTORS.POSITION_INPUT);
            if (positionInput) {
                await typeHumanly(positionInput, job.title);
                log('INFO', `Filled Position: ${job.title}`);
            } else {
                log('WARN', 'Position input not found');
            }

            // Company
            const companyInput = modal.querySelector(INTERVIEW_PREP_SELECTORS.COMPANY_INPUT);
            if (companyInput) {
                await typeHumanly(companyInput, job.company);
                log('INFO', `Filled Company: ${job.company}`);
            } else {
                log('WARN', 'Company input not found');
            }

            // Company URL - leave blank (not in jobs object)
            log('INFO', 'Company URL field left blank (not in job data)');

            // Job Description
            const jdTextarea = modal.querySelector(INTERVIEW_PREP_SELECTORS.JD_TEXTAREA);
            if (jdTextarea && job.fullDescription) {
                await typeHumanly(jdTextarea, job.fullDescription);
                log('INFO', `Filled Job Description (${job.fullDescription.length} chars)`);
            } else {
                log('WARN', 'Job Description textarea not found or no description available');
            }

            // Company Details - leave blank
            log('INFO', 'Company Details field left blank');

            await sleep(1000);

            // 6. Submit the form
            log('INFO', 'Looking for submit button...');
            const submitBtn = modal.querySelector(INTERVIEW_PREP_SELECTORS.SUBMIT_BTN);
            if (!submitBtn) {
                throw new Error('Submit button not found in modal');
            }

            log('INFO', 'Clicking submit button...');
            const currentUrl = window.location.href;
            log('INFO', `Current URL before submit: ${currentUrl}`);
            simulateRealClick(submitBtn);
            await sleep(2000);

            // 7. Wait for URL to change (page navigates to /interview-preparation-hub/{UUID})
            log('INFO', 'Waiting for page navigation after form submission (will wait indefinitely)...');
            await waitForCondition(() => {
                const newUrl = window.location.href;
                // Check if URL has changed and contains a UUID pattern
                const hasNavigated = newUrl !== currentUrl && newUrl.includes('/interview-preparation-hub/');
                if (hasNavigated) {
                    log('INFO', `Page navigated to: ${newUrl}`);
                }
                return hasNavigated;
            }, 0); // 0 = wait indefinitely

            log('INFO', 'Form submitted and page navigated. Waiting for AI content generation...');
            
            // 8. Wait for AI-generated content to load (Skills, Questions, Insights, etc.) - no timeout
            await sleep(3000); // Initial wait for navigation/page load
            
            log('INFO', 'Waiting for interview prep content to generate (will wait indefinitely)...');
            let attempts = 0;
            
            await waitForCondition(() => {
                attempts++;
                
                // Check for content indicators: Skills section, Questions tab, or any generated content
                const skillsSection = document.querySelector('[class*="skills"], h2, h3');
                const questionsTab = Array.from(document.querySelectorAll('button, [role="tab"]'))
                    .find(el => el.textContent.includes('Questions'));
                const insightsTab = Array.from(document.querySelectorAll('button, [role="tab"]'))
                    .find(el => el.textContent.includes('Insights'));
                
                // Log progress every 10 seconds
                if (attempts % 50 === 0) {
                    log('INFO', `Still waiting for content generation... (${Math.floor(attempts * 0.2)}s elapsed)`);
                }
                
                // Check if content has loaded (presence of tabs or content sections)
                const contentLoaded = (skillsSection && skillsSection.textContent.includes('Required Skills')) || 
                                      (questionsTab && insightsTab);
                
                if (contentLoaded) {
                    log('INFO', 'Interview prep content detected!');
                }
                
                return contentLoaded;
            }, 0); // 0 = wait indefinitely
            
            // Additional wait to ensure all content is fully rendered
            log('INFO', 'Waiting additional 5s for complete content rendering...');
            await sleep(5000);

            log('INFO', 'Interview prep form submitted and content generated successfully!');
            
            // Now scrape the generated content
            log('INFO', 'Starting to scrape the generated interview prep content...');
        }
        
        // ═══════════════════════════════════════════════════════════════════════
        // SCRAPING FLOW (RUNS AFTER CREATION OR DIRECTLY IF SKIP_CREATION = true)
        // ═══════════════════════════════════════════════════════════════════════
        
        if (SKIP_CREATION) {
            log('INFO', '🔧 SKIP_CREATION enabled - navigating to interview prep hub to find existing content');
            
            // STEP 2: Navigate to Interview Preparation Hub
            log('INFO', 'Navigating to /interview-preparation-hub...');
            if (!window.location.href.includes('/interview-preparation-hub')) {
                window.location.href = 'https://landbetterjobs.com/interview-preparation-hub';
                await waitForCondition(() => window.location.href.includes('/interview-preparation-hub'), 0);
            }
            
            log('INFO', 'Waiting for table to load...');
            await sleep(3000);
            
            // Wait for table rows to appear
            await waitForCondition(() => {
                const rows = document.querySelectorAll('table tbody tr, [role="table"] [role="row"]');
                return rows.length > 0;
            }, 0);
            
            log('INFO', 'Table loaded successfully');
            await sleep(1000);
            
            // STEP 3: Find matching row
            log('INFO', `Searching for row matching: Position="${position}", Company="${company}"`);
            
            const rows = Array.from(document.querySelectorAll('table tbody tr, [role="table"] [role="row"]'));
            log('INFO', `Found ${rows.length} total rows in table`);
            
            let matchedRow = null;
            
            // Helper function for flexible text matching
            const flexibleMatch = (cellText, searchText) => {
                const cell = cellText.toLowerCase().trim();
                const search = searchText.toLowerCase().trim();
                
                // Remove ellipsis for comparison
                const cellClean = cell.replace(/\.{3,}$/, '').trim();
                const searchClean = search.replace(/\.{3,}$/, '').trim();
                
                // Check if either contains the other (handles truncation)
                if (cellClean.includes(searchClean) || searchClean.includes(cellClean)) {
                    return true;
                }
                
                // Check if cell starts with search (for truncated text like "Product Data Scientist Manager...")
                if (cellClean.length > 0 && searchClean.startsWith(cellClean)) {
                    return true;
                }
                
                // Check if search starts with cell (reverse case)
                if (searchClean.length > 0 && cellClean.startsWith(searchClean)) {
                    return true;
                }
                
                // For longer matches, check if first significant words match
                const cellWords = cellClean.split(/\s+/).filter(w => w.length > 2);
                const searchWords = searchClean.split(/\s+/).filter(w => w.length > 2);
                
                if (cellWords.length >= 3 && searchWords.length >= 3) {
                    // Check if first 3 significant words match
                    const cellStart = cellWords.slice(0, 3).join(' ');
                    const searchStart = searchWords.slice(0, 3).join(' ');
                    if (cellStart === searchStart) {
                        return true;
                    }
                }
                
                return false;
            };
            
            for (const row of rows) {
                const cells = Array.from(row.querySelectorAll('td, [role="cell"]'));
                if (cells.length === 0) continue;
                
                // Extract text from all cells
                const cellTexts = cells.map(cell => cell.textContent.trim());
                
                // Look for position and company matches using flexible matching
                const positionMatch = cellTexts.some(text => flexibleMatch(text, position));
                const companyMatch = cellTexts.some(text => flexibleMatch(text, company));
                
                if (positionMatch && companyMatch) {
                    log('INFO', `✓ Found matching row: ${cellTexts.join(' | ')}`);
                    matchedRow = row;
                    break;
                }
            }
            
            if (!matchedRow) {
                const errorMsg = `No matching row found for "${position}" at "${company}"`;
                log('ERROR', errorMsg);
                throw new Error(errorMsg);
            }
            
            // STEP 4: Click View button
            log('INFO', 'Looking for View button in matched row...');
            
            // Find the View button (anchor tag with external link icon or "View" text)
            const viewButton = matchedRow.querySelector('a[href*="/interview-preparation-hub/"], button:has(svg), a:has(svg)');
            
            if (!viewButton) {
                throw new Error('View button not found in matched row');
            }
            
            log('INFO', 'Clicking View button...');
            const currentUrl = window.location.href;
            simulateRealClick(viewButton);
            
            // Wait for navigation to detail page
            log('INFO', 'Waiting for navigation to interview prep detail page...');
            await waitForCondition(() => {
                const newUrl = window.location.href;
                const hasNavigated = newUrl !== currentUrl && newUrl.includes('/interview-preparation-hub/');
                if (hasNavigated) {
                    log('INFO', `Navigated to: ${newUrl}`);
                }
                return hasNavigated;
            }, 0);
            
            // Wait for page to load
            log('INFO', 'Waiting for interview prep content to load...');
            await sleep(3000);
            
            // Wait for content to be present
            await waitForCondition(() => {
                const content = document.body.textContent;
                return content.length > 1000; // Ensure substantial content is loaded
            }, 0);
        }
        
        // ═══════════════════════════════════════════════════════════════════════
        // SCRAPING SECTION (RUNS FOR BOTH CREATION AND SKIP_CREATION MODES)
        // ═══════════════════════════════════════════════════════════════════════
        
        log('INFO', 'Content loaded. Starting Questions tab scraping...');
        
        // Scrape the Questions tab
        const questionsData = await scrapeQuestionsTab();
        
        log('INFO', `✓ Successfully scraped ${questionsData.questions.length} questions from Questions tab`);
        
        // Scrape the Insights tab
        log('INFO', 'Starting Insights tab scraping...');
        let insightsData = null;
        try {
            insightsData = await scrapeInsightsTab();
            log('INFO', `✓ Successfully scraped Insights tab`);
        } catch (err) {
            log('WARN', `Insights tab scraping failed: ${err.message}`);
            // Continue even if insights fails
        }
        
        // Combine all scraped data
        // Combined all scraped data for background script processing
        const combinedData = {
            questions: questionsData,
            insights: insightsData,
            scrapedAt: new Date().toISOString()
        };
        
        log('INFO', 'Interview prep content extraction completed successfully!');
        
        // Return the scraped data
        return { 
            success: true, 
            scrapedData: combinedData,
            questionsCount: questionsData.questions.length 
        };

    } catch (err) {
        log('ERROR', `Interview prep form fill failed: ${err.message}`);
        throw err;
    } finally {
        isInterviewPrepProcessing = false;
        log('INFO', 'Interview prep execution lock released.');
    }
}
