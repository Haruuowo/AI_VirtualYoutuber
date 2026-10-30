// Luna demo front-end: WebSocket client, Live2D avatar, lip-sync, chat + brain panels.
(() => {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

  // ── State ────────────────────────────────────────────────────────
  const avatar = { mouth: 0, targetMouth: 0, smile: 0.3, form: 0.3, cheek: 0, brow: 0, talking: false, level: 0 };
  const moodStyles = {
    "chill and witty":        { smile: 0.35, form: 0.4, cheek: 0.1, brow: 0,    color: "#a78bfa", label: "chill & witty" },
    "supportive and curious": { smile: 0.55, form: 0.7, cheek: 0.45, brow: 0.4, color: "#34d399", label: "supportive & curious" },
    "focused gamer energy":   { smile: 0.0,  form: 0.1, cheek: 0,   brow: -0.5, color: "#22d3ee", label: "focused gamer energy" },
    "upbeat and sharp":       { smile: 0.85, form: 1.0, cheek: 0.6, brow: 0.5,  color: "#f472b6", label: "upbeat & sharp" },
  };
  let mood = moodStyles["chill and witty"];
  let muted = false;
  let ws, actx, analyser;
  const speechQueue = [];
  let speaking = false;

  // ── WebSocket ────────────────────────────────────────────────────
  function connect() {
    ws = new WebSocket(`${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/ws`);
    ws.onmessage = (e) => handle(JSON.parse(e.data));
    ws.onclose = () => { $("introStatus").textContent = "reconnecting…"; setTimeout(connect, 1500); };
  }
  const send = (obj) => ws && ws.readyState === 1 && ws.send(JSON.stringify(obj));

  function handle(m) {
    switch (m.type) {
      case "hello": {
        if (m.name) {
          document.querySelector(".intro h1").textContent = m.name;
          document.querySelector(".intro-badge").textContent = `AI VTUBER · BUILT BY ${(m.creator || "KAI").toUpperCase()}`;
          document.querySelector(".brand-name").innerHTML = `${esc(m.name)} <span class="muted">· AI VTuber</span>`;
          document.querySelector(".brand-sub").textContent = `by ${m.creator || "Kai"}`;
          document.querySelector(".nameplate b").textContent = m.name.toUpperCase();
          document.querySelector(".nameplate span").textContent = `AI VTuber · made by ${m.creator || "Kai"}`;
          document.title = `${m.name} · AI VTuber`;
        }
        const offline = m.provider === "offline";
        $("brainBadge").textContent = offline ? "offline" : `${m.provider}`;
        $("brainBadge").title = `${m.provider} · ${m.model} · voice ${m.voice}`;
        $("introStatus").textContent = offline
          ? "⚠ no API key / Ollama found — running scripted fallback brain"
          : `✓ brain online · ${m.provider} / ${m.model}`;
        $("introStatus").className = "intro-status " + (offline ? "warn" : "ok");
        $("viewers").textContent = m.viewers;
        $("simToggle").checked = m.sim;
        setStats(m);
        break;
      }
      case "chat": addChat(m.user, m.text, m.platform); pulse("chat"); lookAtChat(); break;
      case "brain": brain(m); break;
      case "mood": setMood(m.mood); break;
      case "mc_action": mcToast(m.action, m.params); break;
      case "speak": speechQueue.push(m); drainSpeech(); break;
      case "stats": setStats(m); break;
      case "viewers": $("viewers").textContent = m.count; break;
      case "sim": $("simToggle").checked = m.enabled; break;
    }
  }

  function setStats(s) {
    if (s.llm_avg != null) $("llmAvg").textContent = `${s.llm_avg} ms`;
    if (s.tts_avg != null) $("ttsAvg").textContent = `${s.tts_avg} ms`;
    if (s.replies != null) $("replies").textContent = s.replies;
  }

  // ── Chat panel ───────────────────────────────────────────────────
  const userColors = {};
  const palette = ["#93c5fd", "#fca5a5", "#fcd34d", "#86efac", "#c4b5fd", "#f9a8d4", "#67e8f9", "#fdba74"];
  function colorFor(u) {
    if (!userColors[u]) userColors[u] = palette[Object.keys(userColors).length % palette.length];
    return userColors[u];
  }
  const clearEmpty = (log) => log.querySelector(".empty")?.remove();
  function addChat(user, text, platform, isLuna = false) {
    const log = $("chatlog");
    clearEmpty(log);
    const el = document.createElement("div");
    const direct = !isLuna && (platform === "Console" || /\b(luna|loona)\b/i.test(text));
    el.className = "msg" + (isLuna ? " luna" : direct ? " direct" : "");
    const plat = isLuna ? "Luna" : platform;
    const platLabel = { Console: "YOU", Twitch: "TTV", TikTok: "TT", Luna: "AI" }[plat] || plat;
    el.innerHTML = `<span class="plat ${plat}">${platLabel}</span><span class="u" style="color:${isLuna ? "" : colorFor(user)}">${esc(user)}</span>${esc(text)}`;
    log.appendChild(el);
    while (log.children.length > 120) log.firstChild.remove();
    log.scrollTop = log.scrollHeight;
  }

  $("chatform").addEventListener("submit", (e) => {
    e.preventDefault();
    const t = $("chatInput").value.trim();
    if (!t) return;
    send({ type: "chat", text: t, user: "You" });
    $("chatInput").value = "";
  });
  document.querySelectorAll("#chips button").forEach((b) =>
    b.addEventListener("click", () => send({ type: "chat", text: b.textContent, user: "You" })));
  $("simToggle").addEventListener("change", (e) => send({ type: "sim", enabled: e.target.checked }));
  $("resetBtn").addEventListener("click", () => send({ type: "reset" }));
  $("muteBtn").addEventListener("click", () => { muted = !muted; $("muteBtn").textContent = muted ? "🔇" : "🔊"; });
  $("howBtn").addEventListener("click", () => ($("howModal").hidden = false));
  $("howClose").addEventListener("click", () => ($("howModal").hidden = true));
  $("howModal").addEventListener("click", (e) => { if (e.target.id === "howModal") $("howModal").hidden = true; });

  // ── Brain panel ──────────────────────────────────────────────────
  let pendingLLM = null;
  function pulse(node) {
    const el = document.querySelector(`.node[data-n="${node}"]`);
    if (!el) return;
    el.classList.add("on");
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("on"), node === "avatar" ? 1800 : 900);
  }
  function row(icon, html, ms, cls = "") {
    const log = $("brainlog");
    clearEmpty(log);
    const el = document.createElement("div");
    el.className = "row " + cls;
    el.innerHTML = `<span class="ic">${icon}</span><span class="tx">${html}</span>${ms != null ? `<span class="ms ${ms > 1500 ? "slow" : ""}">${ms} ms</span>` : "<span></span>"}`;
    log.appendChild(el);
    while (log.children.length > 140) log.firstChild.remove();
    log.scrollTop = log.scrollHeight;
    return el;
  }
  function brain(m) {
    switch (m.stage) {
      case "route":
        if ($("brainlog").children.length) { const s = row("", "<hr>", null, "sep"); }
        pulse("route");
        row("📥", `<b>${esc(m.user)}</b> <em>(${esc(m.platform)})</em>: “${esc(m.text)}”`);
        row(m.priority.startsWith("P1") ? "🎯" : "📦", `route → <b>${m.priority}</b> <em>· ${esc(m.reason)}</em>`);
        break;
      case "intent":
        pulse("intent");
        if (m.intent === "conversation") row("💬", `intent → conversation`);
        else row("⛏️", `intent → <b>minecraft.${esc(m.intent)}</b> <em>${esc(JSON.stringify(m.params))}</em>`);
        break;
      case "llm_start":
        pulse("llm"); $("thinking").classList.add("on");
        pendingLLM = row("🧠", `<span class="spin"></span> ${esc(m.provider)} / ${esc(m.model)} <em>· mood: ${esc(m.mood || "-")}</em>`);
        break;
      case "llm_done": {
        $("thinking").classList.remove("on");
        const html = `${m.ok ? "LLM reply" : "fallback reply"} <em>· mood: ${esc(m.mood || "-")}</em>`;
        if (pendingLLM) pendingLLM.remove();
        pendingLLM = null;
        row("🧠", html, m.ms, m.ok ? "" : "err");
        break;
      }
      case "llm_error": row("⚠️", `LLM error → failover: <em>${esc(m.error)}</em>`, null, "err"); break;
      case "tts_done": pulse("tts"); row("🔊", `neural TTS <em>· ${esc(m.voice)}</em>${m.ok ? "" : " <em>(failed, text only)</em>"}`, m.ms, m.ok ? "" : "err"); break;
      case "tts_error": row("⚠️", `TTS error: <em>${esc(m.error)}</em>`, null, "err"); break;
      case "reset": row("🧽", "short-term memory cleared"); break;
      case "error": row("⚠️", esc(m.error), null, "err"); break;
    }
  }

  function mcToast(action, params) {
    const icons = { collect: "⛏️", mine: "⛏️", attack: "⚔️", guard: "🛡️", follow: "🏃", follow_player: "🏃", flee: "💨",
      companion_mode: "🧭", explore: "🧭", craft: "🛠️", torch: "🔦", feed: "🍖", eat: "🍖", sleep: "🛏️", farm: "🌾", drop: "🎁", crouch: "🙇", idle: "✋", status: "📋", inventory: "🎒" };
    const detail = params.block ? `${params.block} ×${params.count}` : params.mob || params.item || params.player || "";
    const el = document.createElement("div");
    el.className = "toast";
    el.innerHTML = `<span class="ti">${icons[action] || "🎮"}</span><div><small>Minecraft action sent</small><b>${esc(action)}${detail ? " · " + esc(detail) : ""}</b></div>`;
    $("toasts").appendChild(el);
    setTimeout(() => { el.classList.add("out"); setTimeout(() => el.remove(), 400); }, 4200);
  }

  function setMood(name) {
    mood = moodStyles[name] || mood;
    $("moodText").textContent = mood.label;
    const dot = document.querySelector(".mood-dot");
    dot.style.background = mood.color;
    dot.style.boxShadow = `0 0 12px ${mood.color}`;
  }

  // ── Speech: audio + lip-sync + captions ──────────────────────────
  function ensureAudio() {
    if (actx) return;
    actx = new (window.AudioContext || window.webkitAudioContext)();
    analyser = actx.createAnalyser();
    analyser.fftSize = 1024;
    analyser.connect(actx.destination);
  }

  async function drainSpeech() {
    if (speaking || !speechQueue.length) return;
    speaking = true;
    const m = speechQueue.shift();
    try { await speak(m.text, m.audio); } catch (e) { console.error(e); }
    send({ type: "speech_done" });
    speaking = false;
    drainSpeech();
  }

  async function speak(text, audioB64) {
    addChat("Luna", text, "Luna", true);
    pulse("avatar");
    lookAt(0, 0.05);
    let duration = Math.max(1.8, text.split(/\s+/).length * 0.33);
    let src = null;
    if (audioB64 && !muted && actx) {
      const bytes = Uint8Array.from(atob(audioB64), (c) => c.charCodeAt(0));
      const buf = await actx.decodeAudioData(bytes.buffer);
      duration = buf.duration;
      src = actx.createBufferSource();
      src.buffer = buf;
      src.connect(analyser);
    }
    const cap = $("caption");
    cap.textContent = "";
    cap.classList.add("on");
    avatar.talking = true;
    const words = text.split(" ");
    const start = performance.now();
    const typer = setInterval(() => {
      const p = Math.min(1, (performance.now() - start) / (duration * 1000 * 0.92));
      cap.textContent = words.slice(0, Math.ceil(p * words.length)).join(" ");
    }, 40);
    await new Promise((resolve) => {
      if (src) { src.onended = resolve; src.start(); }
      else if (!muted && "speechSynthesis" in window) {
        // Server TTS unavailable -> browser voice so Luna is never silent.
        const u = new SpeechSynthesisUtterance(text);
        const v = speechSynthesis.getVoices().find((v) => /female|aria|jenny|zira|samantha/i.test(v.name));
        if (v) u.voice = v;
        u.pitch = 1.25; u.rate = 1.05;
        u.onend = u.onerror = resolve;
        speechSynthesis.speak(u);
        setTimeout(resolve, duration * 1000 + 4000);
      } else setTimeout(resolve, duration * 1000);
    });
    clearInterval(typer);
    cap.textContent = text;
    avatar.talking = false;
    setTimeout(() => { if (!avatar.talking) cap.classList.remove("on"); }, 2200);
  }

  const tbuf = new Float32Array(1024);
  function audioLevel() {
    if (!avatar.talking) return 0;
    if (analyser && !muted) {
      analyser.getFloatTimeDomainData(tbuf);
      let sum = 0;
      for (let i = 0; i < tbuf.length; i++) sum += tbuf[i] * tbuf[i];
      const rms = Math.sqrt(sum / tbuf.length);
      if (rms > 0.001) return Math.min(1, rms * 7);
    }
    const t = performance.now() / 1000; // fake lip flap when muted / no audio
    return Math.max(0, Math.sin(t * 17) * 0.5 + Math.sin(t * 7.3) * 0.35);
  }

  // ── Live2D avatar ────────────────────────────────────────────────
  let model = null, focusTarget = { x: 0, y: 0 }, lookUntil = 0;
  function lookAt(x, y, holdMs = 2500) { focusTarget = { x, y }; lookUntil = performance.now() + holdMs; }
  function lookAtChat() { if (!avatar.talking) lookAt(0.75, 0.15, 1600); }

  async function initLive2D() {
    const stage = $("stage");
    try {
      const app = new PIXI.Application({ view: $("live2d"), backgroundAlpha: 0, resizeTo: stage, antialias: true, autoDensity: true, resolution: window.devicePixelRatio || 1 });
      model = await PIXI.live2d.Live2DModel.from("/static/model/luna/konata.model3.json", { autoInteract: false, autoUpdate: true });
      app.stage.addChild(model);
      model.anchor.set(0.5, 0.5);
      const natW = model.width, natH = model.height;
      const fit = () => {
        const w = stage.clientWidth, h = stage.clientHeight;
        const s = Math.min((h * 0.92) / natH, (w * 0.9) / natW);
        model.scale.set(s);
        model.position.set(w / 2, h / 2 + h * 0.06);
      };
      fit();
      new ResizeObserver(fit).observe(stage);

      const im = model.internalModel;
      let blinkT = 0, nextBlink = 2 + Math.random() * 3, eye = 1;
      let t0 = performance.now();
      im.on("beforeModelUpdate", () => {
        const cm = im.coreModel;
        const now = performance.now(), t = now / 1000, dt = Math.min(0.05, (now - t0) / 1000); t0 = now;

        // mouth (smoothed audio level)
        avatar.targetMouth = audioLevel();
        avatar.mouth += (avatar.targetMouth - avatar.mouth) * 0.5;
        // expression easing toward mood
        const k = 0.05;
        avatar.smile += ((avatar.talking ? Math.max(mood.smile, 0.3) : mood.smile) - avatar.smile) * k;
        avatar.form += (mood.form - avatar.form) * k;
        avatar.cheek += (mood.cheek - avatar.cheek) * k;
        avatar.brow += (mood.brow - avatar.brow) * k;
        // blinking
        blinkT += dt;
        if (blinkT > nextBlink) { eye = Math.max(0, eye - dt * 14); if (eye === 0) { blinkT = 0; nextBlink = 2 + Math.random() * 4; } }
        else eye = Math.min(1, eye + dt * 10);

        cm.setParameterValueById("ParamMouthOpenY", Math.min(1, avatar.mouth * 1.25));
        cm.setParameterValueById("ParamMouthForm", avatar.form);
        cm.setParameterValueById("ParamEyeLOpen", eye * (1 - avatar.smile * 0.25));
        cm.setParameterValueById("ParamEyeROpen", eye * (1 - avatar.smile * 0.25));
        cm.setParameterValueById("ParamEyeLSmile", avatar.smile);
        cm.setParameterValueById("ParamEyeRSmile", avatar.smile);
        cm.setParameterValueById("ParamCheek", avatar.cheek);
        cm.setParameterValueById("ParamBrowLY", avatar.brow);
        cm.setParameterValueById("ParamBrowRY", avatar.brow);
        cm.setParameterValueById("ParamBreath", (Math.sin(t * 1.6) + 1) / 2);
        cm.addParameterValueById("ParamAngleZ", Math.sin(t * 0.9) * 4 + (avatar.talking ? avatar.mouth * 4 : 0));
        cm.addParameterValueById("ParamBodyAngleZ", Math.sin(t * 0.7) * 2.5);
        cm.addParameterValueById("ParamAngleY", avatar.talking ? avatar.mouth * 6 : 0);
      });

      // idle look-around
      setInterval(() => {
        if (performance.now() < lookUntil) { im.focusController.focus(focusTarget.x, focusTarget.y); return; }
        if (avatar.talking) { im.focusController.focus(Math.sin(performance.now() / 900) * 0.15, 0.05); return; }
        if (Math.random() < 0.035) focusTarget = { x: (Math.random() - 0.5) * 1.2, y: (Math.random() - 0.4) * 0.6 };
        im.focusController.focus(focusTarget.x, focusTarget.y);
      }, 50);
    } catch (e) {
      console.warn("Live2D failed, using fallback avatar", e);
      $("live2d").style.display = "none";
      $("fallbackAvatar").hidden = false;
      const loop = () => { $("faMouth").style.height = `${6 + audioLevel() * 34}px`; requestAnimationFrame(loop); };
      loop();
    }
  }

  // ── Boot ─────────────────────────────────────────────────────────
  const t0 = Date.now();
  setInterval(() => {
    const s = Math.floor((Date.now() - t0) / 1000);
    $("uptime").textContent = `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  }, 1000);

  $("goLive").addEventListener("click", () => {
    ensureAudio();
    $("intro").classList.add("hide");
    $("chatInput").focus();
    send({ type: "chat", text: "Luna, say hi to everyone watching and introduce yourself!", user: "You" });
  });

  connect();
  initLive2D();
  if (location.hash === "#skip") $("intro").classList.add("hide");
})();
