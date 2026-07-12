// avatar.js — Live2D loader with graceful fallback to placeholder
"use strict";

// State
let pixiApp = null;
let live2dModel = null;
let speechAnalyser = null;
let mouthRafId = null;
let lastMouthValue = 0;

// ── Init ─────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", initAvatar);

async function initAvatar() {
    const canvas = $("avatar-canvas");
    const placeholder = $("avatar-placeholder");

    // Look for a Live2D model in web/assets/
    const modelUrl = await findModelUrl();
    if (!modelUrl) {
        canvas.style.display = "none";
        placeholder.classList.remove("hidden");
        console.log("[avatar] No Live2D model found in web/assets/. Showing placeholder.");
        return;
    }

    placeholder.classList.add("hidden");
    canvas.style.display = "block";

    if (typeof PIXI === "undefined" || typeof PIXI.live2d === "undefined") {
        console.error("[avatar] pixi.js or pixi-live2d-display failed to load from CDN");
        placeholder.classList.remove("hidden");
        placeholder.querySelector("p").textContent = "Live2D libs failed to load from CDN. Check your internet.";
        canvas.style.display = "none";
        return;
    }

    try {
        pixiApp = new PIXI.Application({
            view: canvas,
            autoStart: true,
            resizeTo: canvas.parentElement,
            backgroundAlpha: 0,
            antialias: true,
        });

        // Register Live2D loader extensions
        PIXI.live2d.Live2DModel.registerTicker(PIXI.Ticker);

        live2dModel = await PIXI.live2d.Live2DModel.from(modelUrl);
        pixiApp.stage.addChild(live2dModel);

        // Scale to fit while preserving aspect
        const fitScale = Math.min(
            (canvas.clientWidth * 0.85) / live2dModel.width,
            (canvas.clientHeight * 0.95) / live2dModel.height
        );
        live2dModel.scale.set(fitScale);
        live2dModel.x = pixiApp.renderer.width / (2 * pixiApp.stage.scale.x) - live2dModel.width / 2;
        live2dModel.y = pixiApp.renderer.height / (2 * pixiApp.stage.scale.y) - live2dModel.height / 2;

        // Mouse-focus tracking for eye gaze
        canvas.addEventListener("mousemove", (e) => {
            const rect = canvas.getBoundingClientRect();
            const x = (e.clientX - rect.left) / rect.width;
            const y = (e.clientY - rect.top) / rect.height;
            live2dModel.focus(x, y);
        });

        console.log("[avatar] Live2D model loaded:", modelUrl);
    } catch (e) {
        console.error("[avatar] Live2D load failed:", e);
        canvas.style.display = "none";
        placeholder.classList.remove("hidden");
        placeholder.querySelector("p").textContent = `Failed to load model: ${e.message}`;
    }
}

// Scan web/assets/*.model3.json (pre-resolved by trying common filenames)
async function findModelUrl() {
    const candidates = [
        "/web/assets/index.json",       // optional explicit manifest
        "/web/assets/Luna.model3.json",
        "/web/assets/luna.model3.json",
        "/web/assets/model.model3.json",
    ];
    for (const url of candidates) {
        try {
            const r = await fetch(url, { method: "HEAD" });
            if (r.ok) return url;
        } catch {}
    }
    // Last resort — try the listing endpoint
    try {
        const r = await fetch("/api/assets/list");
        if (r.ok) {
            const data = await r.json();
            if (data.model3_url) return data.model3_url;
        }
    } catch {}
    return null;
}

// ── Speech mouth-flap ────────────────────────────────
window.avatarStartSpeech = function (analyserNode) {
    if (!live2dModel) return;
    speechAnalyser = analyserNode;
    if (mouthRafId) cancelAnimationFrame(mouthRafId);
    mouthRafId = requestAnimationFrame(mouthLoop);
};

window.avatarStopSpeech = function () {
    speechAnalyser = null;
    if (mouthRafId) cancelAnimationFrame(mouthRafId);
    mouthRafId = null;
    if (live2dModel) {
        try { live2dModel.internalModel.coreModel.setParameterValueById("ParamMouthOpenY", 0); }
        catch {}
    }
};

function mouthLoop() {
    if (!speechAnalyser || !live2dModel) {
        mouthRafId = null;
        return;
    }
    const buf = new Uint8Array(speechAnalyser.fftSize);
    speechAnalyser.getByteTimeDomainData(buf);
    // RMS
    let sum = 0;
    for (let i = 0; i < buf.length; i++) {
        const v = (buf[i] - 128) / 128;
        sum += v * v;
    }
    const rms = Math.sqrt(sum / buf.length);
    // Smooth + amplify
    const target = Math.min(1, rms * 4);
    lastMouthValue = lastMouthValue * 0.6 + target * 0.4;
    try {
        const cm = live2dModel.internalModel.coreModel;
        cm.setParameterValueById("ParamMouthOpenY", lastMouthValue);
        // Slight head-tilt with the beat
        cm.setParameterValueById("ParamBodyAngleZ", lastMouthValue * 3);
    } catch {}
    mouthRafId = requestAnimationFrame(mouthLoop);
}

// ── Idle blinks (cosmetic) ────────────────────────────
let blinkTimer = null;
function scheduleBlink() {
    if (!live2dModel) return;
    const delay = 2000 + Math.random() * 4000;
    blinkTimer = setTimeout(() => {
        doBlink();
        scheduleBlink();
    }, delay);
}

function doBlink() {
    if (!live2dModel) return;
    try {
        const cm = live2dModel.internalModel.coreModel;
        let t = 0;
        const blink = () => {
            t += 0.2;
            const v = t < 1 ? 1 - t : t - 1;
            cm.setParameterValueById("ParamEyeLOpen", Math.max(0.05, v));
            cm.setParameterValueById("ParamEyeROpen", Math.max(0.05, v));
            if (t < 2) requestAnimationFrame(blink);
        };
        requestAnimationFrame(blink);
    } catch {}
}