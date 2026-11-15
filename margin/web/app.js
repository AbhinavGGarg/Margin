/* Margin: single-page interface. No build step, no dependencies. */
(function () {
  "use strict";

  const view = document.getElementById("view");
  const barTitle = document.getElementById("bar-title");
  const barActions = document.getElementById("bar-actions");
  const toastEl = document.getElementById("toast");

  const state = {
    status: null,        // /api/status
    session: null,       // the open session
    selected: null,      // index of the selected line
    mode: "page",        // "page" | "text"
    player: null,
    playerReady: false,
    pendingSeek: null,
    ticker: null,
    pollTimer: null,
    playingLine: null,
    activeCaption: -1,
  };
  // What the user typed on the home screen, kept so an error doesn't wipe it.
  const draft = { url: "", file: null, transcript: "", showTranscript: false, error: null };

  // --- Small helpers ------------------------------------------------------
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const clock = (t) => {
    if (t == null || isNaN(t)) return "";
    t = Math.max(0, Math.floor(t));
    const h = Math.floor(t / 3600), m = Math.floor((t % 3600) / 60), s = t % 60;
    return (h ? h + ":" + String(m).padStart(2, "0") : m) + ":" + String(s).padStart(2, "0");
  };
  const CONFIDENCE = { high: "Strong match", medium: "Likely match", low: "Best guess", pinned: "Set by you", none: "No clear moment" };

  async function api(path, options) {
    let response;
    try {
      response = await fetch(path, options);
    } catch (e) {
      throw { error: "Can't reach Margin. Is it still running in the terminal?", code: "offline" };
    }
    let body = null;
    try { body = await response.json(); } catch (e) { /* not JSON */ }
    if (!response.ok) throw body && body.error ? body : { error: "Something went wrong (" + response.status + ").", code: "error" };
    return body;
  }

  let toastTimer = null;
  function toast(message) {
    toastEl.textContent = message;
    toastEl.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { toastEl.hidden = true; }, 3200);
  }

  // --- Routing ---------------------------------------------------------------
  function route() {
    teardownSession();
    const match = location.hash.match(/^#\/s\/([A-Za-z0-9_-]+)/);
    if (match) openSession(match[1]);
    else renderHome();
  }
  window.addEventListener("hashchange", route);

  // --- Home --------------------------------------------------------------------
  function readerSummary() {
    const s = state.status;
    if (!s) return "";
    const parts = [];
    if (s.reader.active) parts.push("Reading handwriting with " + s.reader.label);
    const model = s.matching.model;
    if (model === "ready") parts.push("matching by meaning and keywords");
    else if (model === "loading" || model === "idle") parts.push("loading the matching model…");
    else parts.push("matching by keywords");
    return parts.join(" · ");
  }

  function setupNotice() {
    const s = state.status;
    if (!s || s.reader.active) return "";
    const hasKey = s.reader.gemini.key;
    const message = hasKey && !s.reader.gemini.installed
      ? "A Gemini key is set, but the Gemini library isn't installed. Run <code>./start.sh</code> again."
      : "No handwriting reader is set up yet. Add <code>GEMINI_API_KEY=…</code> to the <code>.env</code> file in the Margin folder and restart. On a Mac, running <code>./start.sh</code> also installs Apple's built-in reader, which needs no key.";
    return '<div class="notice warn">' + message + "</div>";
  }

  async function renderHome() {
    document.title = "Margin";
    barTitle.innerHTML = "";
    barActions.innerHTML = "";
    view.innerHTML =
      '<div class="home">' +
      "<h1>Tap a line of your notes. <mark>Hear the moment</mark> it was explained.</h1>" +
      '<p class="lead">Paste the video you studied from and add a photo of your handwritten notes. Margin links every line to the second it was said.</p>' +
      '<form class="card form" id="create" novalidate>' +
      '<div id="form-error"></div>' +
      '<div id="setup-notice"></div>' +
      '<div class="field"><label for="url">Video</label>' +
      '<input class="input" id="url" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="https://www.youtube.com/watch?v=…" value="' + esc(draft.url) + '"></div>' +
      '<div class="field"><span class="label">Notes</span>' +
      '<label class="drop" id="drop"><input id="file" type="file" accept="image/*,.heic,.heif" aria-label="Photo of your notes"><span id="drop-inner"></span></label>' +
      '<div class="hint">One page per photo. Shoot from straight above in good light.</div></div>' +
      '<div class="field" id="transcript-field"' + (draft.showTranscript ? "" : " hidden") + ">" +
      '<label for="transcript">Transcript</label>' +
      '<textarea class="textarea" id="transcript" spellcheck="false" placeholder="0:00&#10;so today we\'re going to talk about…&#10;0:07&#10;…">' + esc(draft.transcript) + "</textarea>" +
      '<div class="hint">On YouTube, open the video description, choose “Show transcript”, select everything in the panel and paste it here. SRT and VTT files work too.</div></div>' +
      '<div class="form-foot">' +
      '<button class="button primary" id="go" type="submit">Link my notes</button>' +
      '<span class="status" id="reader-status"></span>' +
      '<button class="link-button" type="button" id="toggle-transcript">' + (draft.showTranscript ? "Use the video’s captions" : "Paste a transcript instead") + "</button>" +
      "</div></form>" +
      '<section class="recent" id="recent" hidden></section>' +
      "</div>";

    const urlInput = document.getElementById("url");
    const fileInput = document.getElementById("file");
    const drop = document.getElementById("drop");
    const transcriptField = document.getElementById("transcript-field");
    const transcriptInput = document.getElementById("transcript");
    const toggle = document.getElementById("toggle-transcript");

    function paintDrop() {
      const inner = document.getElementById("drop-inner");
      if (draft.file) {
        const isHeic = /\.hei[cf]$/i.test(draft.file.name);
        const thumb = isHeic ? '<span class="drop-icon">▤</span>' : '<img class="drop-thumb" alt="" src="' + URL.createObjectURL(draft.file) + '">';
        inner.outerHTML = '<span id="drop-inner" style="display:contents">' + thumb + '<span class="drop-text"><strong>' + esc(draft.file.name) + "</strong><span>Click or drop to change</span></span></span>";
      } else {
        inner.outerHTML = '<span id="drop-inner" style="display:contents"><span class="drop-icon">＋</span><span class="drop-text"><strong>Add a photo of your notes</strong><span>Click to choose, or drop an image here</span></span></span>';
      }
    }
    function paintError() {
      document.getElementById("form-error").innerHTML = draft.error ? '<div class="notice error">' + esc(draft.error) + "</div>" : "";
    }
    function paintStatus() {
      const el = document.getElementById("reader-status");
      const notice = document.getElementById("setup-notice");
      if (el) el.textContent = readerSummary();
      if (notice) notice.innerHTML = setupNotice();
    }
    paintDrop(); paintError(); paintStatus();

    const clearError = () => { if (draft.error) { draft.error = null; paintError(); } };
    urlInput.addEventListener("input", () => { draft.url = urlInput.value; clearError(); });
    transcriptInput.addEventListener("input", () => { draft.transcript = transcriptInput.value; clearError(); });
    fileInput.addEventListener("change", () => { if (fileInput.files[0]) { draft.file = fileInput.files[0]; paintDrop(); clearError(); } });
    ["dragenter", "dragover"].forEach((type) => drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.add("over"); }));
    ["dragleave", "drop"].forEach((type) => drop.addEventListener(type, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
    drop.addEventListener("drop", (e) => {
      const file = e.dataTransfer && e.dataTransfer.files[0];
      if (file) { draft.file = file; paintDrop(); clearError(); }
    });
    toggle.addEventListener("click", () => {
      draft.showTranscript = !draft.showTranscript;
      transcriptField.hidden = !draft.showTranscript;
      toggle.textContent = draft.showTranscript ? "Use the video’s captions" : "Paste a transcript instead";
      if (draft.showTranscript) transcriptInput.focus();
    });

    document.getElementById("create").addEventListener("submit", async (e) => {
      e.preventDefault();
      draft.error = null;
      if (!draft.url.trim()) draft.error = "Paste the link to the video first.";
      else if (!draft.file) draft.error = "Add a photo of your notes.";
      if (draft.error) { paintError(); return; }
      const button = document.getElementById("go");
      button.disabled = true;
      const body = new FormData();
      body.append("video_url", draft.url.trim());
      body.append("notes", draft.file, draft.file.name);
      if (draft.showTranscript && draft.transcript.trim()) body.append("transcript", draft.transcript);
      try {
        const created = await api("/api/sessions", { method: "POST", body });
        location.hash = "#/s/" + created.id;
      } catch (err) {
        draft.error = err.error;
        button.disabled = false;
        paintError();
      }
    });

    // Status (and keep refreshing while the matching model is still loading).
    const refreshStatus = async () => {
      try { state.status = await api("/api/status"); } catch (e) { return; }
      if (!document.getElementById("reader-status")) return;
      paintStatus();
      const model = state.status.matching.model;
      if (model === "loading" || model === "idle") setTimeout(refreshStatus, 1500);
    };
    refreshStatus();

    try {
      const { sessions } = await api("/api/sessions");
      const recent = document.getElementById("recent");
      if (recent && sessions.length) {
        recent.hidden = false;
        recent.innerHTML = "<h2>Recent</h2><div class=\"recent-list\">" + sessions.map((s) =>
          '<a class="recent-item" href="#/s/' + esc(s.id) + '">' +
          '<img alt="" loading="lazy" src="https://i.ytimg.com/vi/' + esc(s.video_id) + '/mqdefault.jpg">' +
          '<span class="meta"><span class="title">' + esc(s.title || "Untitled video") + "</span>" +
          '<span class="sub">' + s.linked + " of " + s.lines + " lines linked" + (s.author ? " · " + esc(s.author) : "") + "</span></span>" +
          '<button class="icon-button" data-delete="' + esc(s.id) + '" aria-label="Delete this session" title="Delete">×</button></a>'
        ).join("") + "</div>";
        recent.addEventListener("click", async (e) => {
          const target = e.target.closest("[data-delete]");
          if (!target) return;
          e.preventDefault();
          await api("/api/sessions/" + target.dataset.delete, { method: "DELETE" });
          target.closest(".recent-item").remove();
          if (!recent.querySelector(".recent-item")) recent.hidden = true;
        });
      }
    } catch (e) { /* the list is optional */ }
  }

  // --- Working -------------------------------------------------------------------
  const STEPS = [["captions", "Getting the video’s captions"], ["handwriting", "Reading your handwriting"], ["matching", "Finding each line’s moment"]];

  function renderWorking(step) {
    document.title = "Linking… · Margin";
    barTitle.innerHTML = "";
    barActions.innerHTML = "";
    const current = Math.max(0, STEPS.findIndex((s) => s[0] === step));
    view.innerHTML = '<div class="working"><h1>Linking your notes</h1><ol class="steps">' +
      STEPS.map((s, i) => '<li class="' + (i < current ? "done" : i === current ? "now" : "") + '"><span class="dot">' + (i < current ? "✓" : "") + "</span>" + s[1] + "</li>").join("") +
      '</ol><p class="hint">This usually takes 10 to 20 seconds. The first run also downloads a small language model.</p></div>';
  }

  async function openSession(id) {
    clearTimeout(state.pollTimer);
    let data;
    try {
      data = await api("/api/sessions/" + id);
    } catch (err) {
      draft.error = err.error;
      location.hash = "#/";
      return;
    }
    if (!location.hash.includes(id)) return; // navigated away meanwhile
    if (data.status === "working") {
      renderWorking(data.step);
      state.pollTimer = setTimeout(() => openSession(id), 700);
    } else if (data.status === "error") {
      draft.error = data.error;
      if (data.code === "captions") draft.showTranscript = true;
      api("/api/sessions/" + id, { method: "DELETE" }).catch(() => {});
      location.hash = "#/";
    } else {
      state.session = data;
      renderSession();
    }
  }

  // --- Session ---------------------------------------------------------------------
  function teardownSession() {
    clearTimeout(state.pollTimer);
    clearInterval(state.ticker);
    if (state.player && state.player.destroy) { try { state.player.destroy(); } catch (e) { /* already gone */ } }
    Object.assign(state, { session: null, selected: null, player: null, playerReady: false, pendingSeek: null, playingLine: null, activeCaption: -1 });
    closeMap();
  }

  function renderSession() {
    const s = state.session;
    const title = s.video.title || "Your notes";
    document.title = title + " · Margin";
    barTitle.innerHTML = "<strong>" + esc(title) + "</strong>" + (s.video.author ? " · " + esc(s.video.author) : "");
    barActions.innerHTML = '<button class="button quiet" id="open-map" title="See how the lines were matched (M)">How it matched</button><a class="button" href="#/">New</a>';
    document.getElementById("open-map").addEventListener("click", openMap);

    const anyBox = s.lines.some((l) => l.box);
    state.mode = anyBox ? "page" : "text";

    view.innerHTML =
      '<div class="session">' +
      '<section class="pane pane-notes">' +
      '<div class="pane-head"><div class="segmented" role="group" aria-label="View">' +
      '<button data-mode="page"' + (anyBox ? "" : " disabled") + ">Page</button><button data-mode=\"text\">Text</button></div>" +
      '<span class="count" id="count"></span></div>' +
      '<div class="page-scroll" id="notes"></div></section>' +
      '<section class="pane pane-video">' +
      '<div class="video"><div id="player"></div></div>' +
      '<div class="timeline" id="timeline" aria-hidden="true"></div>' +
      '<div class="now" id="now"></div>' +
      '<div class="captions" id="captions"></div>' +
      "</section></div>";

    view.querySelector(".segmented").addEventListener("click", (e) => {
      const button = e.target.closest("button[data-mode]");
      if (!button || button.disabled) return;
      state.mode = button.dataset.mode;
      paintNotes();
    });
    document.getElementById("notes").addEventListener("click", (e) => {
      const target = e.target.closest("[data-line]");
      if (target) select(Number(target.dataset.line), true);
    });
    document.getElementById("timeline").addEventListener("click", (e) => {
      const tick = e.target.closest("[data-line]");
      if (tick) { select(Number(tick.dataset.line), true); return; }
      const rect = e.currentTarget.getBoundingClientRect();
      seek(((e.clientX - rect.left) / rect.width) * duration());
    });
    document.getElementById("captions").addEventListener("click", (e) => {
      const row = e.target.closest("[data-t]");
      if (row) seek(Number(row.dataset.t));
    });

    paintNotes(); paintTimeline(); paintNow(); paintCaptions();
    createPlayer();
    clearInterval(state.ticker);
    state.ticker = setInterval(tick, 250);

    const first = s.lines.find((l) => l.t != null);
    if (first) select(first.i, false);
  }

  const duration = () => Math.max(state.session.video.duration || 0, 1);

  function paintNotes() {
    const s = state.session;
    const host = document.getElementById("notes");
    view.querySelectorAll(".segmented button").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.mode === state.mode)));
    const linked = s.lines.filter((l) => l.t != null).length;
    document.getElementById("count").textContent = linked + " of " + s.lines.length + " lines linked";

    if (state.mode === "page") {
      host.innerHTML = '<div class="page"><img alt="Your notes" src="/api/sessions/' + esc(s.id) + '/page.jpg">' +
        s.lines.filter((l) => l.box).map((l) => {
          const [x0, y0, x1, y1] = l.box;
          const pad = 0.004;
          const style = "left:" + ((x0 - pad) * 100).toFixed(2) + "%;top:" + ((y0 - pad) * 100).toFixed(2) + "%;width:" + ((x1 - x0 + 2 * pad) * 100).toFixed(2) + "%;height:" + ((y1 - y0 + 2 * pad) * 100).toFixed(2) + "%";
          return '<button class="mark ' + markClass(l) + '" data-line="' + l.i + '" data-time="' + clock(l.t) + '" style="' + style + '" aria-label="' + esc(l.text) + (l.t != null ? ", plays from " + clock(l.t) : ", no moment found") + '"></button>';
        }).join("") + "</div>";
    } else {
      host.innerHTML = '<ol class="lines">' + s.lines.map((l) =>
        '<li><button class="line-row ' + markClass(l) + '" data-line="' + l.i + '">' +
        '<span class="when">' + (l.t != null ? clock(l.t) : "–") + "</span>" +
        '<span class="words">' + esc(l.text) + "</span>" +
        (l.confidence === "low" ? '<span class="tag">best guess</span>' : l.confidence === "pinned" ? '<span class="tag">set by you</span>' : "") +
        "</button></li>").join("") + "</ol>";
    }
    paintSelection();
  }

  function markClass(l) {
    return l.t == null ? "unlinked" : l.confidence === "low" ? "low" : "";
  }

  function paintSelection() {
    view.querySelectorAll("[data-line]").forEach((el) => {
      const i = Number(el.dataset.line);
      el.classList.toggle("selected", i === state.selected);
      el.classList.toggle("playing", i === state.playingLine && i !== state.selected);
    });
  }

  function paintTimeline() {
    const s = state.session;
    document.getElementById("timeline").innerHTML = s.lines.filter((l) => l.t != null).map((l) =>
      '<button class="tick ' + (l.confidence === "low" ? "low" : "") + '" data-line="' + l.i + '" style="left:' + ((l.t / duration()) * 100).toFixed(3) + '%" title="' + esc(clock(l.t) + "  " + l.text) + '" tabindex="-1"></button>'
    ).join("") + '<div class="playhead" id="playhead" style="left:0%"></div>';
    paintSelection();
  }

  const COMMON = new Set("the and for are was were with that this from have has had not but you your they them then than into out its can will one two all any how what when where why who which there here use used".split(" "));

  function emphasise(caption, lineText) {
    const stems = (lineText.toLowerCase().match(/[a-z0-9]{3,}/g) || [])
      .filter((w) => !COMMON.has(w))
      .map((w) => (w.length > 4 ? w.replace(/(ing|ed|es|s)$/, "") : w));
    if (!stems.length) return esc(caption);
    return esc(caption).replace(/[A-Za-z0-9']+/g, (word) => {
      const lower = word.toLowerCase();
      return stems.some((stem) => lower.startsWith(stem)) ? "<b>" + word + "</b>" : word;
    });
  }

  function paintNow() {
    const host = document.getElementById("now");
    const l = state.selected == null ? null : state.session.lines[state.selected];
    if (!l) { host.innerHTML = '<p class="now-empty">Tap a line of your notes to hear where it was explained.</p>'; return; }
    const linked = l.t != null;
    let html =
      '<div class="now-line"><p class="words" id="now-words">' + esc(l.text) + "</p>" +
      '<button class="icon-button" id="edit-line" title="Fix how this line was read" aria-label="Edit this line’s text">✎</button></div>' +
      '<div class="now-meta">' + (linked ? '<button class="chip" id="replay" title="Play from here">▶ ' + clock(l.t) + "</button>" : "") +
      '<span class="conf ' + esc(l.confidence) + '">' + CONFIDENCE[l.confidence] + "</span></div>";
    if (linked && l.caption) html += '<p class="said"><span class="said-label">What was said</span>' + emphasise(l.caption, l.text) + "</p>";
    if (!linked) html += '<p class="said">Nothing in the video clearly matches this line. If you know where it was said, play the video to that point and set it yourself.</p>';
    html += '<div class="now-actions">';
    if (l.alternatives && l.alternatives.length) {
      html += '<span class="label">' + (linked ? "Also said at" : "Possibly at") + "</span>" +
        l.alternatives.map((a, k) => '<button class="chip" data-alt="' + k + '" title="' + esc(a.caption) + '">' + clock(a.t) + "</button>").join("");
    }
    html += '<button class="button small" id="pin" title="Link this line to where the video is right now">Set to current moment</button>';
    if (l.pinned) html += '<button class="button small quiet" id="unpin">Undo</button>';
    html += "</div>";
    host.innerHTML = html;

    const replay = document.getElementById("replay");
    if (replay) replay.addEventListener("click", () => seek(l.t));
    host.querySelectorAll("[data-alt]").forEach((b) => b.addEventListener("click", () => seek(l.alternatives[Number(b.dataset.alt)].t)));
    document.getElementById("pin").addEventListener("click", () => {
      const t = currentTime();
      if (t == null) { toast("Start the video first, then set the moment."); return; }
      updateLine(l.i, { t: Math.max(0, t - 1) }, "Linked to " + clock(t));
    });
    const unpin = document.getElementById("unpin");
    if (unpin) unpin.addEventListener("click", () => updateLine(l.i, { t: null }, "Back to the automatic match"));
    document.getElementById("edit-line").addEventListener("click", () => editLine(l));
  }

  function editLine(l) {
    const words = document.getElementById("now-words");
    if (!words) return;
    const input = document.createElement("input");
    input.value = l.text;
    input.setAttribute("aria-label", "Line text");
    words.replaceWith(input);
    input.focus(); input.select();
    let done = false;
    const finish = (save) => {
      if (done) return;
      done = true;
      const value = input.value.trim();
      if (save && value && value !== l.text) updateLine(l.i, { text: value }, "Line updated and re-matched");
      else paintNow();
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") finish(true);
      else if (e.key === "Escape") finish(false);
      e.stopPropagation();
    });
    input.addEventListener("blur", () => finish(true));
  }

  async function updateLine(index, change, message) {
    try {
      const updated = await api("/api/sessions/" + state.session.id + "/lines/" + index, {
        method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(change),
      });
      state.session = updated;
      paintNotes(); paintTimeline(); paintNow();
      if (message) toast(message);
    } catch (err) {
      toast(err.error);
      paintNow();
    }
  }

  function paintCaptions() {
    const caps = state.session.captions || [];
    document.getElementById("captions").innerHTML = "<h3>Transcript</h3>" + caps.map((c, i) =>
      '<button class="cap" data-i="' + i + '" data-t="' + c.t + '"><span class="when">' + clock(c.t) + "</span><span>" + esc(c.text) + "</span></button>").join("");
    state.activeCaption = -1;
  }

  function select(index, play) {
    const l = state.session.lines[index];
    if (!l) return;
    state.selected = index;
    paintSelection(); paintNow();
    const el = view.querySelector('#notes [data-line="' + index + '"]');
    if (el && el.scrollIntoView) el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    if (play && l.t != null) seek(l.t);
  }

  function step(direction) {
    const lines = state.session.lines.filter((l) => l.t != null);
    if (!lines.length) return;
    const at = lines.findIndex((l) => l.i === state.selected);
    const next = lines[Math.min(lines.length - 1, Math.max(0, at === -1 ? 0 : at + direction))];
    select(next.i, true);
  }

  // --- Video ------------------------------------------------------------------------
  let apiRequested = false;
  const apiWaiters = [];
  function whenYouTubeReady(callback) {
    if (window.YT && window.YT.Player) { callback(); return; }
    apiWaiters.push(callback);
    if (apiRequested) return;
    apiRequested = true;
    window.onYouTubeIframeAPIReady = () => apiWaiters.splice(0).forEach((fn) => fn());
    const script = document.createElement("script");
    script.src = "https://www.youtube.com/iframe_api";
    script.onerror = () => { apiRequested = false; videoFallback(); };
    document.head.appendChild(script);
    setTimeout(() => { if (!(window.YT && window.YT.Player)) videoFallback(); }, 8000);
  }

  function videoFallback() {
    const host = view.querySelector(".video");
    if (!host || state.playerReady || !state.session) return;
    const l = state.selected == null ? null : state.session.lines[state.selected];
    const t = l && l.t != null ? Math.floor(l.t) : 0;
    host.innerHTML = '<div class="video-fallback"><div>The video player couldn’t load.<br>' +
      '<a target="_blank" rel="noopener" href="https://www.youtube.com/watch?v=' + esc(state.session.video.id) + "&t=" + t + 's">Open on YouTube at ' + clock(t) + "</a></div></div>";
  }

  function createPlayer() {
    const session = state.session;
    whenYouTubeReady(() => {
      if (state.session !== session || !document.getElementById("player")) return;
      state.player = new window.YT.Player("player", {
        videoId: session.video.id,
        playerVars: { rel: 0, playsinline: 1, modestbranding: 1 },
        events: {
          onReady: () => {
            state.playerReady = true;
            if (state.pendingSeek != null) { const t = state.pendingSeek; state.pendingSeek = null; seek(t); }
          },
          onError: () => { state.playerReady = false; videoFallback(); },
        },
      });
    });
  }

  function seek(t) {
    if (t == null) return;
    if (state.playerReady && state.player.seekTo) {
      state.player.seekTo(Math.max(0, t), true);
      state.player.playVideo();
    } else {
      state.pendingSeek = t;
      if (view.querySelector(".video-fallback")) videoFallback();
    }
  }

  function currentTime() {
    if (!state.playerReady || !state.player.getCurrentTime) return null;
    const t = state.player.getCurrentTime();
    return typeof t === "number" && !isNaN(t) ? t : null;
  }

  function tick() {
    if (!state.session) return;
    const t = currentTime();
    if (t == null) return;
    const playhead = document.getElementById("playhead");
    if (playhead) playhead.style.left = Math.min(100, (t / duration()) * 100).toFixed(3) + "%";

    // Which line is being explained right now? The latest link at or before t, within a minute.
    let playing = null, best = -Infinity;
    for (const l of state.session.lines) {
      if (l.t != null && l.t <= t + 0.5 && l.t > best && t - l.t < 60) { best = l.t; playing = l.i; }
    }
    if (playing !== state.playingLine) { state.playingLine = playing; paintSelection(); }

    const caps = state.session.captions || [];
    let lo = 0, hi = caps.length - 1, at = -1;
    while (lo <= hi) { const mid = (lo + hi) >> 1; if (caps[mid].t <= t + 0.05) { at = mid; lo = mid + 1; } else hi = mid - 1; }
    if (at !== state.activeCaption) {
      const host = document.getElementById("captions");
      const before = host && host.querySelector(".cap.active");
      if (before) before.classList.remove("active");
      const now = host && host.querySelector('.cap[data-i="' + at + '"]');
      if (now) {
        now.classList.add("active");
        if (!host.matches(":hover")) host.scrollTo({ top: now.offsetTop - host.clientHeight / 2 + now.clientHeight / 2, behavior: "smooth" });
      }
      state.activeCaption = at;
    }
  }

  // --- Match map -----------------------------------------------------------------------
  const modal = document.getElementById("map-modal");
  const canvas = document.getElementById("map-canvas");
  const tip = document.getElementById("map-tip");
  let mapGeometry = null;

  function openMap() {
    if (!state.session) return;
    modal.hidden = false;
    drawMap();
    document.getElementById("map-foot").textContent = "Matched by " + state.session.matching + ". Handwriting read with " + state.session.reader + ". Click anywhere on the map to play that moment.";
    modal.querySelector("[data-close].icon-button").focus();
  }
  function closeMap() { modal.hidden = true; tip.hidden = true; }
  modal.addEventListener("click", (e) => { if (e.target.closest("[data-close]")) closeMap(); });
  window.addEventListener("resize", () => { if (!modal.hidden) drawMap(); });

  function drawMap() {
    const map = state.session.map, lines = state.session.lines;
    const styles = getComputedStyle(document.documentElement);
    const ink = styles.getPropertyValue("--ink").trim(), muted = styles.getPropertyValue("--muted").trim();
    const rule = styles.getPropertyValue("--rule").trim(), sunk = styles.getPropertyValue("--sunk").trim();
    const dark = window.matchMedia("(prefers-color-scheme: dark)").matches;
    const rows = map.cells.length, cols = map.columns;
    const gutter = 30, axis = 22;
    const width = canvas.parentElement.clientWidth;
    const rowHeight = Math.max(9, Math.min(22, Math.floor(380 / Math.max(rows, 1))));
    const height = rows * rowHeight + axis;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio); canvas.height = Math.round(height * ratio);
    canvas.style.height = height + "px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, width, height);
    const plotWidth = width - gutter, cellWidth = plotWidth / cols;
    ctx.fillStyle = sunk; ctx.fillRect(gutter, 0, plotWidth, rows * rowHeight);
    for (let r = 0; r < rows; r++) {
      for (let c = 0; c < cols; c++) {
        const v = map.cells[r][c] / 255;
        if (v < 0.08) continue;
        ctx.fillStyle = dark ? "rgba(157,184,238," + (v * 0.95).toFixed(3) + ")" : "rgba(39,70,127," + (v * 0.92).toFixed(3) + ")";
        ctx.fillRect(gutter + c * cellWidth, r * rowHeight + 1, Math.ceil(cellWidth), rowHeight - 2);
      }
    }
    ctx.fillStyle = muted; ctx.font = "11px ui-monospace, Menlo, monospace"; ctx.textBaseline = "middle"; ctx.textAlign = "right";
    const labelEvery = rowHeight >= 13 ? 1 : Math.ceil(13 / rowHeight);
    for (let r = 0; r < rows; r += labelEvery) ctx.fillText(String(r + 1), gutter - 8, r * rowHeight + rowHeight / 2);
    ctx.textAlign = "center"; ctx.textBaseline = "top";
    const total = duration(), stepSeconds = [15, 30, 60, 120, 300, 600, 900, 1800].find((s) => total / s <= 10) || 3600;
    for (let t = 0; t <= total; t += stepSeconds) {
      const x = gutter + (t / total) * plotWidth;
      ctx.fillStyle = muted; ctx.fillText(clock(t), Math.min(width - 16, Math.max(gutter + 12, x)), rows * rowHeight + 6);
    }
    // The chosen path, positioned by each line's actual time.
    ctx.strokeStyle = rule; ctx.fillStyle = rule; ctx.lineWidth = 1.5; ctx.lineJoin = "round";
    ctx.beginPath();
    let started = false;
    lines.forEach((l, r) => {
      if (l.t == null) return;
      const x = gutter + (l.t / total) * plotWidth, y = r * rowHeight + rowHeight / 2;
      if (started) ctx.lineTo(x, y); else { ctx.moveTo(x, y); started = true; }
    });
    ctx.stroke();
    lines.forEach((l, r) => {
      if (l.t == null) return;
      ctx.beginPath(); ctx.arc(gutter + (l.t / total) * plotWidth, r * rowHeight + rowHeight / 2, Math.min(4, rowHeight / 2.6), 0, Math.PI * 2); ctx.fill();
    });
    ctx.strokeStyle = ink;
    mapGeometry = { gutter, plotWidth, rowHeight, rows, total };
  }

  function mapPoint(e) {
    if (!mapGeometry) return null;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left, y = e.clientY - rect.top;
    const row = Math.floor(y / mapGeometry.rowHeight);
    if (x < mapGeometry.gutter || row < 0 || row >= mapGeometry.rows) return null;
    return { x, y, row, t: ((x - mapGeometry.gutter) / mapGeometry.plotWidth) * mapGeometry.total };
  }
  canvas.addEventListener("mousemove", (e) => {
    const p = mapPoint(e);
    if (!p) { tip.hidden = true; return; }
    const l = state.session.lines[p.row];
    tip.hidden = false;
    tip.textContent = "Line " + (p.row + 1) + ": " + l.text + "  ·  " + clock(p.t) + (l.t != null ? "  (linked at " + clock(l.t) + ")" : "  (not linked)");
    tip.style.left = Math.min(p.x + 12, canvas.clientWidth - 290) + "px";
    tip.style.top = p.y + 14 + "px";
  });
  canvas.addEventListener("mouseleave", () => { tip.hidden = true; });
  canvas.addEventListener("click", (e) => {
    const p = mapPoint(e);
    if (!p) return;
    closeMap();
    select(p.row, false);
    seek(p.t);
  });

  // --- Keyboard ---------------------------------------------------------------------------
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.hidden) { closeMap(); return; }
    if (!state.session || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.target.closest("input, textarea")) return;
    if (e.key === "ArrowDown" || e.key === "j") { e.preventDefault(); step(1); }
    else if (e.key === "ArrowUp" || e.key === "k") { e.preventDefault(); step(-1); }
    else if (e.key === "m") { if (modal.hidden) openMap(); else closeMap(); }
    else if (e.key === " " && !e.target.closest("button, a") && state.playerReady) {
      e.preventDefault();
      if (state.player.getPlayerState() === 1) state.player.pauseVideo(); else state.player.playVideo();
    }
  });

  route();
})();
