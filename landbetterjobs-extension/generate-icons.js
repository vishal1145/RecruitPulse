#!/usr/bin/env node
/**
 * generate-icons.js
 *
 * Generates PNG icons for the RecruitPulse Chrome Extension using
 * the `canvas` npm package. Run once before loading the extension:
 *
 *   cd /home/algofolks/Music/RecruitPulse/landbetterjobs-extension
 *   node generate-icons.js
 *
 * Outputs: icons/icon16.png, icons/icon48.png, icons/icon128.png
 */

const { createCanvas } = require('canvas');
const fs = require('fs');
const path = require('path');

const SIZES = [16, 48, 128];
const OUT_DIR = path.join(__dirname, 'icons');
fs.mkdirSync(OUT_DIR, { recursive: true });

SIZES.forEach(size => {
    const canvas = createCanvas(size, size);
    const ctx = canvas.getContext('2d');

    // ── Background circle with gradient ──────────────────────────────────────
    const grad = ctx.createLinearGradient(0, 0, size, size);
    grad.addColorStop(0, '#6c63ff');
    grad.addColorStop(1, '#a855f7');

    ctx.beginPath();
    ctx.arc(size / 2, size / 2, size / 2, 0, Math.PI * 2);
    ctx.fillStyle = grad;
    ctx.fill();

    const s = size / 128; // scale factor

    // ── Magnifying glass ─────────────────────────────────────────────────────
    ctx.strokeStyle = 'rgba(255,255,255,0.9)';
    ctx.lineWidth = Math.max(2, 8 * s);
    ctx.lineCap = 'round';

    const cx = 52 * s;
    const cy = 52 * s;
    const r = 26 * s;

    // Circle
    ctx.beginPath();
    ctx.arc(cx, cy, r, 0, Math.PI * 2);
    ctx.stroke();

    // Handle
    ctx.beginPath();
    ctx.moveTo((cx + r * 0.7), (cy + r * 0.7));
    ctx.lineTo(size * 0.82, size * 0.82);
    ctx.stroke();

    // ── Pulse line through circle ─────────────────────────────────────────────
    if (size >= 48) {
        ctx.strokeStyle = 'rgba(255,255,255,0.75)';
        ctx.lineWidth = Math.max(1.5, 4 * s);

        const lx = cx - r * 0.85;
        const rx = cx + r * 0.85;
        const my = cy;
        const amp = r * 0.35;

        ctx.beginPath();
        ctx.moveTo(lx, my);
        ctx.lineTo(lx + (rx - lx) * 0.3, my);
        ctx.lineTo(lx + (rx - lx) * 0.4, my - amp);
        ctx.lineTo(lx + (rx - lx) * 0.5, my + amp);
        ctx.lineTo(lx + (rx - lx) * 0.6, my);
        ctx.lineTo(rx, my);
        ctx.stroke();
    }

    const buffer = canvas.toBuffer('image/png');
    const file = path.join(OUT_DIR, `icon${size}.png`);
    fs.writeFileSync(file, buffer);
    console.log(`✓ Generated ${file}`);
});

console.log('\nAll icons generated successfully!');
