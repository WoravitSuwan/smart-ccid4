// ห้องสอบปากคำ: บันทึกเสียงในเบราว์เซอร์ ส่งขึ้นเซิร์ฟเวอร์ทีละท่อน 5 วินาที (B2)
(() => {
  const root = document.querySelector('[data-recorder]');
  if (!root) return;
  const $ = (s) => root.querySelector(s);
  // read the cookie first: it is renewed if the officer had to sign in again in another tab
  const csrf = () => (document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/) || [])[1] || document.querySelector('meta[name="csrf-token"]').content;
  const fmt = (t) => `${String(Math.floor(t / 60)).padStart(2, '0')}:${String(Math.floor(t % 60)).padStart(2, '0')}`;
  const state = {
    take: parseInt(root.dataset.take || '0', 10) + 1, // every page load starts a new take (new WebM header)
    seq: 0, recorder: null, stream: null, uploaded: parseFloat(root.dataset.uploaded || '0'),
    running: false, paused: false, startedAt: 0, pausedFor: 0, pauseStart: 0, lastChunkAt: 0, queue: Promise.resolve(), pending: 0,
  };
  const ui = {
    timer: $('[data-timer]'), dot: $('[data-rec-dot]'), stateChip: $('[data-rec-state]'), level: $('[data-level]'),
    uploadedText: $('[data-uploaded-text]'), start: $('[data-rec-start]'), pause: $('[data-rec-pause]'), stop: $('[data-rec-stop]'),
    close: $('[data-rec-close]'), test: $('[data-mic-test]'), testAudio: $('[data-test-audio]'), markerAdd: $('[data-marker-add]'),
    markerNote: $('[data-marker-note]'), markers: $('[data-markers]'),
    micSelect: $('[data-mic-select]'), micName: $('[data-mic-name]'), help: $('[data-mic-help]'), helpReason: $('[data-mic-help-reason]'),
  };
  const setState = (text, tone) => { ui.stateChip.textContent = text; ui.stateChip.className = `chip chip-${tone}`; };
  const elapsed = () => (state.running ? (performance.now() - state.startedAt - state.pausedFor - (state.paused ? performance.now() - state.pauseStart : 0)) / 1000 : 0);
  const total = () => state.uploaded + elapsed();

  // ---------------------------------------------------------------- microphone: choose, listen, diagnose
  const MIC_KEY = 'ccid4.mic';
  const SILENT = 0.01; // peak under 1% of full scale (about -40 dB) = nothing reached the microphone
  const QUIET_WARN = 30; // seconds of silence while recording before we warn
  const audio = { ctx: null, source: null, analyser: null, data: null, peak: 0, maxPeak: 0, lastSoundAt: performance.now() };
  const savedMic = () => { try { return localStorage.getItem(MIC_KEY) || ''; } catch (e) { return ''; } };
  const micLabel = () => { const t = state.stream && state.stream.getAudioTracks()[0]; return (t && t.label) || 'ไมค์หลักของเครื่อง'; };

  function micHelp(reason) {
    if (!ui.help) return;
    ui.help.hidden = !reason;
    if (reason) ui.helpReason.textContent = reason;
  }

  async function refreshDevices() {
    if (!ui.micSelect || !navigator.mediaDevices || !navigator.mediaDevices.enumerateDevices) return;
    const inputs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'audioinput');
    const current = ui.micSelect.value || savedMic();
    const def = inputs.find((d) => d.deviceId === 'default');
    const first = new Option(def && def.label ? `ไมค์หลักของเครื่อง (${def.label.replace(/^Default - /, '')})` : 'ไมค์หลักของเครื่อง', '');
    const others = inputs.filter((d) => d.deviceId && !['default', 'communications'].includes(d.deviceId))
      .map((d, i) => new Option(d.label || `ไมโครโฟน ${i + 1}`, d.deviceId));
    ui.micSelect.replaceChildren(first, ...others);
    ui.micSelect.value = others.some((o) => o.value === current) ? current : '';
  }

  function listen(stream) {
    if (!audio.ctx) {
      audio.ctx = new AudioContext();
      audio.analyser = audio.ctx.createAnalyser();
      audio.analyser.fftSize = 1024;
      audio.data = new Float32Array(audio.analyser.fftSize);
      const draw = () => {
        audio.analyser.getFloatTimeDomainData(audio.data);
        let peak = 0;
        for (const v of audio.data) peak = Math.max(peak, Math.abs(v));
        audio.peak = peak;
        audio.maxPeak = Math.max(audio.maxPeak, peak);
        if (peak >= SILENT) audio.lastSoundAt = performance.now();
        ui.level.style.width = `${Math.min(100, peak * 160)}%`;
        requestAnimationFrame(draw);
      };
      draw();
    }
    if (audio.source) audio.source.disconnect();
    audio.source = audio.ctx.createMediaStreamSource(stream);
    audio.source.connect(audio.analyser);
    if (audio.ctx.state === 'suspended') audio.ctx.resume().catch(() => {});
  }

  async function getStream() {
    const want = ui.micSelect ? ui.micSelect.value : '';
    const live = state.stream && state.stream.getAudioTracks().some((t) => t.readyState === 'live');
    if (live && state.streamDevice === want) return state.stream;
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      const e = new Error('insecure'); e.name = 'InsecureContext'; throw e;
    }
    // raw sound for the speech model: the browser's call-style noise suppression can smear consonants
    const constraints = { echoCancellation: false, noiseSuppression: false, autoGainControl: true };
    if (want) constraints.deviceId = { exact: want };
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: constraints });
    } catch (e) {
      if (want && (e.name === 'OverconstrainedError' || e.name === 'NotFoundError')) { // the chosen mic was unplugged
        ui.micSelect.value = '';
        return getStream();
      }
      throw e;
    }
    if (state.stream) state.stream.getTracks().forEach((t) => t.stop());
    state.stream = stream; state.streamDevice = want;
    const track = stream.getAudioTracks()[0];
    if (track) {
      track.addEventListener('mute', () => { setState('ไมค์ถูกปิดเสียง', 'warning'); micHelp('ระบบปิดเสียงไมค์ตัวนี้อยู่ (ปุ่มปิดไมค์บนคีย์บอร์ด/หูฟัง หรือการตั้งค่าของ Windows)'); });
      track.addEventListener('unmute', () => { micHelp(''); setState(state.running ? 'กำลังบันทึก' : 'พร้อมบันทึก', state.running ? 'danger' : 'muted'); });
      track.addEventListener('ended', () => { setState('ไมค์หลุด', 'danger'); micHelp('ไมค์หลุดหรือถูกถอดออก เสียบใหม่ เลือกไมค์ แล้วกด "หยุด" และ "บันทึกต่อ"'); });
    }
    if (ui.micName) ui.micName.textContent = `ไมค์ที่ใช้: ${micLabel()}`;
    listen(stream);
    refreshDevices().catch(() => {}); // device names are only visible after permission is given
    return stream;
  }

  function micFailed(e) {
    const why = {
      InsecureContext: 'เบราว์เซอร์ใช้ไมค์ได้เฉพาะเมื่อเปิดด้วย http://localhost:8000 หรือ https:// (ไม่ใช่เลข IP)',
      NotAllowedError: 'เบราว์เซอร์ไม่ได้รับอนุญาตให้ใช้ไมค์: กดไอคอนหน้าช่องที่อยู่ (localhost:8000) → Microphone → Allow แล้วโหลดหน้าใหม่ · ถ้าอนุญาตแล้วให้ดูข้อ 3',
      SecurityError: 'เบราว์เซอร์ไม่ได้รับอนุญาตให้ใช้ไมค์: กดไอคอนหน้าช่องที่อยู่ → Microphone → Allow แล้วโหลดหน้าใหม่',
      NotFoundError: 'ไม่พบไมโครโฟนในเครื่องนี้ เสียบไมค์หรือหูฟังที่มีไมค์ แล้วโหลดหน้าใหม่',
      NotReadableError: 'เปิดไมค์ไม่ได้: มีโปรแกรมอื่นใช้ไมค์อยู่ หรือ Windows ไม่อนุญาตให้ใช้ไมค์ (ดูข้อ 3 และ 5)',
    }[e && e.name] || `เปิดไมค์ไม่ได้ (${(e && (e.name || e.message)) || 'ไม่ทราบสาเหตุ'})`;
    setState('ใช้ไมค์ไม่ได้', 'danger');
    micHelp(why);
  }

  ui.micSelect && ui.micSelect.addEventListener('change', async () => {
    try { localStorage.setItem(MIC_KEY, ui.micSelect.value); } catch (e) { /* storage blocked */ }
    micHelp('');
    if (state.stream) { try { await getStream(); setState('เปลี่ยนไมค์แล้ว กดทดสอบไมค์อีกครั้ง', 'muted'); } catch (e) { micFailed(e); } }
  });
  if (ui.micSelect && savedMic()) { // until permission is given the browser hides device names
    ui.micSelect.add(new Option('ไมค์ที่เลือกไว้ครั้งก่อน', savedMic()));
    ui.micSelect.value = savedMic();
  }

  // while recording: warn (do not stop) when nothing reaches the microphone for a while
  document.addEventListener('visibilitychange', () => { audio.lastSoundAt = performance.now(); });
  setInterval(() => {
    if (!state.running || state.paused || document.visibilityState !== 'visible') return;
    const quiet = (performance.now() - Math.max(audio.lastSoundAt, state.resumedAt || 0)) / 1000;
    if (quiet >= QUIET_WARN && !state.quietWarned) {
      state.quietWarned = true;
      setState(`ไม่มีเสียงเข้าไมค์ ${QUIET_WARN} วินาที`, 'warning');
      micHelp(`ระหว่างบันทึกไม่มีเสียงเข้าไมค์ "${micLabel()}" เลย ${QUIET_WARN} วินาที (ยังบันทึกอยู่) · ถ้ามีคนพูดอยู่ให้ตรวจไมค์ตามนี้`);
    } else if (quiet < QUIET_WARN && state.quietWarned) {
      state.quietWarned = false;
      micHelp(''); setState('กำลังบันทึก', 'danger');
    }
  }, 1000);

  function upload(blob, seconds) {
    state.seq += 1;
    const seq = state.seq;
    state.pending += 1;
    const body = new FormData();
    body.append('take', state.take); body.append('seq', seq); body.append('seconds', seconds.toFixed(2)); body.append('blob', blob, `t${state.take}-${seq}.webm`);
    const send = async (attempt) => {
      try {
        const r = await fetch(root.dataset.chunkUrl, { method: 'POST', body, headers: { 'X-CSRFToken': csrf() }, redirect: 'manual' });
        if (r.type === 'opaqueredirect') throw new Error('หมดเวลาเข้าสู่ระบบ — เปิดแท็บใหม่ เข้าสู่ระบบ แล้วระบบจะส่งต่อเอง ห้ามปิดหน้านี้');
        if (!r.ok) {
          const err = new Error(`HTTP ${r.status}`);
          err.permanent = r.status === 400 || r.status === 403 || r.status === 404;
          throw err;
        }
        const s = await r.json();
        ui.uploadedText.textContent = `บันทึกขึ้นเซิร์ฟเวอร์แล้วถึงนาทีที่ ${fmt(s.seconds)}`;
        if (ui.close) ui.close.disabled = false;
      } catch (e) {
        if (e.permanent) { ui.uploadedText.textContent = `เซิร์ฟเวอร์ไม่รับท่อนที่ ${seq}: ${e.message}`; return; }
        // keep trying for as long as it takes (long interviews, server restart): the chunk stays in this tab's memory
        ui.uploadedText.textContent = `ส่งท่อนที่ ${seq} ไม่สำเร็จ (${e.message}) กำลังลองใหม่ครั้งที่ ${attempt + 1}… เสียงยังอยู่ในเครื่อง ห้ามปิดหน้านี้`;
        await new Promise((res) => setTimeout(res, Math.min(30000, 1000 * 2 ** attempt)));
        return send(attempt + 1);
      }
    };
    state.queue = state.queue.then(() => send(0)).finally(() => { state.pending -= 1; });
  }

  // keep the screen (and the recording) awake during long interviews
  let wakeLock = null;
  async function keepAwake() {
    try { if ('wakeLock' in navigator && !wakeLock) { wakeLock = await navigator.wakeLock.request('screen'); wakeLock.addEventListener('release', () => { wakeLock = null; }); } } catch (e) { /* not supported: ask the officer to turn off sleep */ }
  }
  function letSleep() { if (wakeLock) { wakeLock.release(); wakeLock = null; } }
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible' && state.running) keepAwake(); });

  async function start() {
    try { await getStream(); } catch (e) { micFailed(e); return; }
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
    state.recorder = new MediaRecorder(state.stream, mime ? { mimeType: mime, audioBitsPerSecond: 64000 } : {});
    state.recorder.ondataavailable = (e) => {
      if (!e.data || !e.data.size) return;
      const now = performance.now();
      upload(e.data, (now - state.lastChunkAt) / 1000);
      state.lastChunkAt = now;
    };
    state.recorder.start(5000);
    const importBox = document.querySelector('[data-import]');
    if (importBox) importBox.hidden = true; // a room holds either a recording made here or one imported file
    keepAwake();
    state.running = true; state.paused = false; state.startedAt = state.lastChunkAt = state.resumedAt = performance.now(); state.pausedFor = 0;
    if (ui.micSelect) ui.micSelect.disabled = true;
    ui.dot.classList.add('on'); setState('กำลังบันทึก', 'danger');
    ui.start.disabled = true; ui.pause.disabled = false; ui.stop.disabled = false; ui.markerAdd.disabled = false; if (ui.close) ui.close.disabled = true;
  }
  function pause() {
    if (!state.recorder) return;
    if (state.paused) {
      state.recorder.resume(); state.paused = false; state.resumedAt = performance.now();
      const pausedMs = performance.now() - state.pauseStart;
      state.pausedFor += pausedMs;
      state.lastChunkAt += pausedMs; // the next chunk's length must not include the pause (no sound was recorded)
      ui.pause.textContent = 'พัก'; setState('กำลังบันทึก', 'danger'); ui.dot.classList.add('on'); }
    else {
      state.recorder.requestData(); // send what was said up to now, so the live text can finish the sentence
      state.recorder.pause(); state.paused = true; state.pauseStart = performance.now(); ui.pause.textContent = 'บันทึกต่อ'; setState('พักการบันทึก', 'warning'); ui.dot.classList.remove('on');
      setTimeout(() => state.queue.then(liveFlush), 300);
    }
  }
  function stop() {
    return new Promise((resolve) => {
      if (!state.recorder || state.recorder.state === 'inactive') { resolve(); return; }
      state.recorder.addEventListener('stop', () => setTimeout(resolve, 50), { once: true });
      state.recorder.stop();
    }).then(() => state.queue).then(() => {
      state.uploaded = total(); state.running = false; state.paused = false; state.take += 1; state.seq = 0;
      ui.dot.classList.remove('on'); setState('หยุดแล้ว (บันทึกต่อได้)', 'muted'); letSleep();
      ui.start.disabled = false; ui.start.lastChild.textContent = 'บันทึกต่อ'; ui.pause.disabled = true; ui.stop.disabled = true; ui.markerAdd.disabled = true;
      if (ui.micSelect) ui.micSelect.disabled = false;
      state.quietWarned = false; micHelp('');
      liveFlush();
      if (ui.close) ui.close.disabled = false;
    });
  }

  ui.start && ui.start.addEventListener('click', start);
  ui.pause && ui.pause.addEventListener('click', pause);
  ui.stop && ui.stop.addEventListener('click', () => stop());
  ui.markerAdd && ui.markerAdd.addEventListener('click', async () => {
    const body = new URLSearchParams({ at: total().toFixed(1), note: ui.markerNote.value });
    const r = await fetch(root.dataset.markerUrl, { method: 'POST', body, headers: { 'X-CSRFToken': csrf() } });
    if (r.ok) { const m = await r.json(); const li = document.createElement('li'); li.className = 'chip chip-muted'; li.textContent = `${Math.round(m.at)} วิ · ${m.note}`; ui.markers.appendChild(li); ui.markerNote.value = ''; }
  });
  ui.test && ui.test.addEventListener('click', async () => {
    micHelp('');
    try { await getStream(); } catch (e) { micFailed(e); return; }
    ui.test.disabled = true;
    ui.testAudio.hidden = true;
    audio.maxPeak = 0;
    const parts = [];
    const rec = new MediaRecorder(state.stream);
    rec.ondataavailable = (e) => { if (e.data && e.data.size) parts.push(e.data); };
    rec.onstop = () => {
      ui.test.disabled = false;
      const track = state.stream.getAudioTracks()[0];
      if (audio.maxPeak < SILENT) {
        setState('ไม่ได้ยินเสียงจากไมค์', 'danger');
        micHelp(track && track.muted
          ? `ระบบปิดเสียงไมค์ "${micLabel()}" อยู่ (ปุ่มปิดไมค์บนคีย์บอร์ด/หูฟัง หรือการตั้งค่าของ Windows)`
          : `ทดสอบ 5 วินาทีแล้ว ไมค์ "${micLabel()}" ไม่ได้รับเสียงเลย (พูดใส่ไมค์ระหว่างทดสอบ)`);
        return;
      }
      ui.testAudio.src = URL.createObjectURL(new Blob(parts, { type: rec.mimeType || 'audio/webm' }));
      ui.testAudio.hidden = false;
      ui.testAudio.play().catch(() => {});
      setState(`ไมค์ใช้ได้ · ดังสุด ${Math.round(Math.min(1, audio.maxPeak) * 100)}%`, 'success');
      if (ui.micName) ui.micName.textContent = `ไมค์ที่ใช้: ${micLabel()} · กำลังเล่นเสียงที่อัดให้ฟัง ถ้าไม่ได้ยินให้เปิดลำโพงหรือเพิ่มเสียงของเครื่อง (ไม่มีผลต่อการบันทึก)`;
    };
    rec.start(); setState('กำลังทดสอบไมค์ 5 วินาที… พูดใส่ไมค์', 'warning');
    setTimeout(() => rec.stop(), 5000);
  });
  // ---------------------------------------------------------------- text transcribed while recording
  const liveBox = document.querySelector('[data-live]');
  const liveUrl = root.dataset.liveUrl;
  let liveAfter = 0;
  function liveFlush() {
    if (liveUrl) fetch(liveUrl, { method: 'POST', headers: { 'X-CSRFToken': csrf() }, redirect: 'manual' }).catch(() => {});
  }
  async function livePoll() {
    try {
      const r = await fetch(`${liveUrl}?after=${liveAfter}`, { redirect: 'manual' });
      if (!r.ok || r.type === 'opaqueredirect') return;
      const d = await r.json();
      const lines = liveBox.querySelector('[data-live-lines]');
      const status = liveBox.querySelector('[data-live-status]');
      if (!d.enabled) { status.textContent = 'เครื่องนี้ยังไม่ได้เปิดถอดเสียงสด · ระบบจะถอดเสียงทั้งไฟล์หลังปิดการบันทึก'; return; }
      const nearBottom = lines.scrollHeight - lines.scrollTop - lines.clientHeight < 40;
      for (const seg of d.segments) {
        liveAfter = Math.max(liveAfter, seg.id);
        const li = document.createElement('li');
        const time = document.createElement('span');
        time.className = 'live-time';
        time.textContent = (seg.take ? `รอบ ${seg.take} · ` : '') + fmt(seg.start);
        const text = document.createElement('span');
        text.textContent = seg.text; // text, never HTML
        if (seg.p !== null && seg.p < 0.6) text.className = 'live-unsure';
        li.append(time, text);
        lines.appendChild(li);
      }
      if (d.segments.length && nearBottom) lines.scrollTop = lines.scrollHeight;
      const behind = Math.max(0, d.received - d.done);
      if (d.error) status.textContent = `ถอดสดสะดุด (${d.error}) · เสียงยังบันทึกครบ ระบบจะถอดทั้งไฟล์หลังปิดการบันทึก`;
      else if (d.received) {
        status.textContent = `ถอดแล้วถึงนาทีที่ ${fmt(d.done)} จากเสียงที่ได้รับ ${fmt(d.received)}`
          + (behind > 45 ? ` · ตามหลังเสียงจริงประมาณ ${fmt(behind)} ส่วนที่ไม่ทันจะถอดต่อหลังปิดการบันทึก` : ' · ตามทันเสียงพูด');
      }
    } catch (e) { /* offline for a moment: the next poll catches up */ }
  }
  if (liveBox && liveUrl) { livePoll(); setInterval(livePoll, 3000); }

  // importing a recorded file: show that a large upload is on its way, and hide the option once recording starts
  const importForm = document.querySelector('[data-import-form]');
  importForm && importForm.addEventListener('submit', () => {
    const button = importForm.querySelector('[data-import-submit]');
    button.disabled = true;
    button.textContent = 'กำลังนำเข้า… ไฟล์ใหญ่อาจใช้เวลาสักครู่ ห้ามปิดหน้านี้';
  });

  const closeForm = document.querySelector('[data-close-form]');
  closeForm && closeForm.addEventListener('submit', async (e) => {
    if (state.running) { e.preventDefault(); await stop(); closeForm.submit(); }
  });
  window.addEventListener('beforeunload', (e) => { if (state.running || state.pending > 0) { e.preventDefault(); e.returnValue = ''; } });
  setInterval(() => { ui.timer.textContent = fmt(total()); }, 250);
  // a paused interview sends no audio: keep the sign-in alive while this page is open
  setInterval(() => { if (state.running || state.pending) fetch(root.dataset.statusUrl, { redirect: 'manual' }).catch(() => {}); }, 4 * 60 * 1000);
  ui.timer.textContent = fmt(total());
})();
