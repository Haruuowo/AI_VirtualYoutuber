// app.js — Luna Studio WebSocket client + Web Audio playback + DOM
"use strict";

// ── Globals ──────────────────────────────────────────
const WS_URL = (location.protocol === "https:" ? "wss://" : "ws://") + location.host + "/ws";
let ws = null;
let audioCtx = null;
let analyser = null;
let currentLunaLatency = null;

// ── DOM helpers ──────────────────────────────────────
const $ = (id) => document.getElementById(id);

function setWsStatus(state) {
    const el = $("ws-status");
    el.classList.remove("ws-connected", "ws-disconnected", "ws-connecting");
    el.classList.add("ws-" + state);
}

function appendChat({ who, text, ja, cls, platform }) {
    const scrollback = $("chat-scrollback");
    const div = document.createElement("div");
    div.className = "msg " + cls;
    const meta = platform && platform !== "Web" ? `<span class="meta">[${platform}]</span>` : "";
    const jaLine = ja ? `<span class="ja">${escapeHtml(ja)}</span>` : "";
    div.innerHTML = `<span class="who">${escapeHtml(who)}:</span>${meta} ${escapeHtml(text)}${jaLine}`;
    scrollback.appendChild(div);
    scrollback.scrollTop = scrollback.scrollHeight;
    while (scrollback.children.length > 500) scrollback.removeChild(scrollback.firstChild);
}

function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
}

// ── WebSocket ────────────────────────────────────────
function connect() {
    setWsStatus("connecting");
    ws = new WebSocket(WS_URL);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
        setWsStatus("connected");
        appendChat({ who: "system", text: "Connected to Luna", cls: "system", platform: "" });
        // request initial snapshot
        send({ type: "hello" });
    };

    ws.onclose = () => {
        setWsStatus("disconnected");
        appendChat({ who: "system", text: "Disconnected — retrying in 3s…", cls: "system", platform: "" });
        setTimeout(connect, 3000);
    };

    ws.onerror = () => setWsStatus("disconnected");

    ws.onmessage = (ev) => {
        if (ev.data instanceof ArrayBuffer) {
            handleBinary(ev.data);
            return;
        }
        let msg;
        try { msg = JSON.parse(ev.data); } catch { return; }
        handleJson(msg);
    };
}

function send(obj) {
    if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

// ── JSON message handlers ────────────────────────────
function handleJson(msg) {
    switch (msg.type) {
        case "chat_msg":
            appendChat({
                who: msg.username,
                text: msg.message,
                cls: msg.username === "Luna" ? "luna" : "user",
                platform: msg.platform,
            });
            break;

        case "luna_response":
            currentLunaLatency = msg.latency_ms;
            appendChat({
                who: "Luna",
                text: msg.en,
                ja: msg.ja || null,
                cls: msg.banter ? "banter" : "luna",
                platform: msg.platform || "Luna",
            });
            break;

        case "stats":
            updateStats(msg);
            break;

        case "memory":
            renderMemory(msg);
            break;

        case "config":
            applyConfigToForm(msg);
            break;

        case "models":
            populateModelSelect(msg.list);
            break;

        case "system":
            appendChat({ who: "system", text: msg.text, cls: "system", platform: "" });
            break;

        case "settings_saved":
            appendChat({ who: "system", text: "Settings saved — handlers reloaded", cls: "system", platform: "" });
            $("settings-modal").close();
            send({ type: "hello" });
            break;

        case "error":
            appendChat({ who: "system", text: "Error: " + msg.text, cls: "system", platform: "" });
            break;
    }
}

// ── Binary WAV frames ────────────────────────────────
// 4-byte ASCII engine_id prefix, then WAV bytes
async function handleBinary(buffer) {
    const view = new DataView(buffer);
    const engineId = String.fromCharCode(view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3));
    const wavBytes = new Uint8Array(buffer, 4);

    if (!audioCtx) audioCtx = new (window.AudioContext || window.webkitAudioContext)();
    if (audioCtx.state === "suspended") await audioCtx.resume();

    try {
        const audioBuf = await audioCtx.decodeAudioData(wavBytes.slice().buffer);
        const src = audioCtx.createBufferSource();
        src.buffer = audioBuf;

        // analyser for avatar mouth-flap
        if (!analyser) {
            analyser = audioCtx.createAnalyser();
            analyser.fftSize = 256;
        }
        src.connect(analyser);
        analyser.connect(audioCtx.destination);

        // notify avatar.js that speech is starting
        if (window.avatarStartSpeech) window.avatarStartSpeech(analyser);

        src.onended = () => {
            if (window.avatarStopSpeech) window.avatarStopSpeech();
        };
        src.start();
    } catch (e) {
        console.error("Audio decode failed:", e, "engine:", engineId);
    }
}

// ── Stats bar ────────────────────────────────────────
function updateStats(s) {
    $("stat-provider").textContent = s.provider || "—";
    $("stat-model").textContent = s.model || "—";
    $("stat-tts").textContent = s.tts || "—";
    $("stat-speaking").textContent = s.is_speaking ? "true" : "false";
    $("stat-speaking").classList.toggle("value-true", !!s.is_speaking);
    $("stat-queue").textContent = s.queue ?? "0";
    $("stat-latency").textContent = s.last_latency_ms != null ? s.last_latency_ms + "ms" : "—";
    $("stat-total").textContent = s.total_responded ?? "0";
    $("stat-idle").textContent = s.idle_countdown_s != null ? s.idle_countdown_s + "s" : "—";
    $("stat-vram").textContent = s.vram_used_mb ? s.vram_used_mb + "MB" : "—";
}

// ── Memory viewer ────────────────────────────────────
function renderMemory(m) {
    const shortEl = $("mem-short");
    const longEl = $("mem-long");
    shortEl.innerHTML = "";
    longEl.innerHTML = "";
    (m.short_term || []).forEach((msg) => {
        const li = document.createElement("li");
        li.textContent = `[${msg.role}] ${msg.content}`;
        shortEl.appendChild(li);
    });
    (m.long_term || []).forEach((f) => {
        const li = document.createElement("li");
        li.textContent = `(${f.about}) ${f.fact}`;
        longEl.appendChild(li);
    });
}

// ── Settings modal ───────────────────────────────────
function applyConfigToForm(c) {
    if (!c) return;
    const form = $("settings-form");
    form.llm_provider.value = c.llm_provider || "openrouter";
    $("llm_model_input").value = c.llm_model || "";
    form.tts_engine.value = c.tts_engine || "edge";
    form.qwen_tts_voice.value = c.qwen_tts_voice || "Ryan";
    form.japanese_mode.checked = !!c.japanese_mode;
    form.qwen_tts_ref_text.value = c.qwen_tts_ref_text || "";

    // Mask keys (server returns existing values; show count of chars as a hint)
    form.openrouter_key.value = c.openrouter_key_set ? "•".repeat(8) : "";
    form.deepseek_key.value = c.deepseek_key_set ? "•".repeat(8) : "";
    form.groq_key.value = c.groq_key_set ? "•".repeat(8) : "";
    // Mark keys as untouched — only submit if the user edits them
    form.openrouter_key.dataset.untouched = "1";
    form.deepseek_key.dataset.untouched = "1";
    form.groq_key.dataset.untouched = "1";

    renderMemory(c.memory || {});
}

function populateModelSelect(list) {
    const sel = $("llm_model_select");
    sel.disabled = !list || list.length === 0;
    sel.innerHTML = "";
    const placeholder = document.createElement("option");
    placeholder.value = "";
    placeholder.textContent = list && list.length ? "— pick a model —" : "— no models fetched —";
    sel.appendChild(placeholder);
    (list || []).forEach((m) => {
        const opt = document.createElement("option");
        opt.value = m;
        opt.textContent = m;
        sel.appendChild(opt);
    });
    sel.onchange = () => { if (sel.value) $("llm_model_input").value = sel.value; };
}

function collectSettings() {
    const form = $("settings-form");
    const fd = new FormData(form);
    const out = {
        type: "settings_save",
        llm_provider: fd.get("llm_provider"),
        llm_model: $("llm_model_input").value.trim(),
        tts_engine: fd.get("tts_engine"),
        qwen_tts_voice: fd.get("qwen_tts_voice"),
        japanese_mode: form.japanese_mode.checked,
        qwen_tts_ref_text: fd.get("qwen_tts_ref_text") || "",
    };
    // Only include API keys the user touched (don't overwrite with mask)
    ["openrouter", "deepseek", "groq"].forEach((p) => {
        const field = form[p + "_key"];
        if (field && field.dataset.untouched !== "1" && field.value.trim()) {
            out[p + "_key"] = field.value.trim();
        }
    });
    // Voice-clone file is read by the upload endpoint separately (Phase 3 feature)
    return out;
}

// ── Event wiring ─────────────────────────────────────
function wireEvents() {
    // Send chat
    $("chat-send").onclick = sendChat;
    $("chat-input").addEventListener("keydown", (e) => { if (e.key === "Enter") sendChat(); });

    // Settings open/close
    $("btn-settings").onclick = () => {
        send({ type: "hello" }); // refresh config + memory
        $("settings-modal").showModal();
    };
    $("settings-close").onclick = () => $("settings-modal").close();
    $("settings-cancel").onclick = () => $("settings-modal").close();

    // Settings save
    $("settings-form").addEventListener("submit", (e) => {
        e.preventDefault();
        send(collectSettings());
    });

    // Key show/hide + dirty marking
    document.querySelectorAll(".toggle-key").forEach((btn) => {
        btn.onclick = () => {
            const input = btn.previousElementSibling;
            if (input.type === "password") { input.type = "text"; btn.textContent = "hide"; }
            else { input.type = "password"; btn.textContent = "show"; }
        };
    });
    document.querySelectorAll('input[name$="_key"]').forEach((input) => {
        input.addEventListener("input", () => { delete input.dataset.untouched; });
    });

    // Fetch models button
    $("btn-fetch-models").onclick = async () => {
        const btn = $("btn-fetch-models");
        btn.textContent = "Fetching…";
        btn.disabled = true;
        try {
            const r = await fetch("/api/models");
            const data = await r.json();
            populateModelSelect(data.list || []);
        } catch (e) { console.error(e); }
        btn.textContent = "Fetch models";
        btn.disabled = false;
    };

    // Memory clear buttons
    $("btn-clear-short").onclick = () => send({ type: "clear_memory", scope: "short" });
    $("btn-clear-long").onclick = () => send({ type: "clear_memory", scope: "long" });

    // Reload page button
    $("btn-reload").onclick = () => location.reload();

    // Ref audio file picker — stash filename for settings save
    $("ref_audio_file").addEventListener("change", (e) => {
        const f = e.target.files[0];
        if (!f) return;
        // Upload immediately; server stores it and remembers the path
        const fd = new FormData();
        fd.append("file", f);
        fetch("/api/upload_ref", { method: "POST", body: fd })
            .then((r) => r.json())
            .then((d) => {
                if (d.ok) appendChat({ who: "system", text: `Ref audio saved: ${d.path}`, cls: "system", platform: "" });
                else appendChat({ who: "system", text: `Ref audio upload failed: ${d.error}`, cls: "system", platform: "" });
            })
            .catch((e) => appendChat({ who: "system", text: `Upload error: ${e}`, cls: "system", platform: "" }));
    });
}

function sendChat() {
    const username = $("chat-username").value.trim() || "You";
    const text = $("chat-input").value.trim();
    if (!text) return;
    send({ type: "chat", username, message: text });
    $("chat-input").value = "";
    appendChat({ who: username, text, cls: "user", platform: "Web" });
}

// ── Boot ─────────────────────────────────────────────
window.addEventListener("DOMContentLoaded", () => {
    wireEvents();
    connect();
});