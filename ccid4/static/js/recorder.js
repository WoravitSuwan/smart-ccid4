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
  };
  const setState = (text, tone) => { ui.stateChip.textContent = text; ui.stateChip.className = `chip chip-${tone}`; };
  const elapsed = () => (state.running ? (performance.now() - state.startedAt - state.pausedFor - (state.paused ? performance.now() - state.pauseStart : 0)) / 1000 : 0);
  const total = () => state.uploaded + elapsed();

  async function getStream() {
    if (state.stream) return state.stream;
    state.stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: true, autoGainControl: true } });
    const ctx = new AudioContext();
    const analyser = ctx.createAnalyser();
    analyser.fftSize = 512;
    ctx.createMediaStreamSource(state.stream).connect(analyser);
    const data = new Uint8Array(analyser.fftSize);
    const draw = () => {
      analyser.getByteTimeDomainData(data);
      let peak = 0;
      for (const v of data) peak = Math.max(peak, Math.abs(v - 128));
      ui.level.style.width = `${Math.min(100, (peak / 128) * 160)}%`;
      requestAnimationFrame(draw);
    };
    draw();
    return state.stream;
  }

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
    try { await getStream(); } catch (e) { setState('ใช้ไมค์ไม่ได้ (ต้องเปิดผ่าน HTTPS และอนุญาตไมค์)', 'danger'); return; }
    const mime = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : '';
    state.recorder = new MediaRecorder(state.stream, mime ? { mimeType: mime, audioBitsPerSecond: 64000 } : {});
    state.recorder.ondataavailable = (e) => {
      if (!e.data || !e.data.size) return;
      const now = performance.now();
      upload(e.data, (now - state.lastChunkAt) / 1000);
      state.lastChunkAt = now;
    };
    state.recorder.start(5000);
    keepAwake();
    state.running = true; state.paused = false; state.startedAt = state.lastChunkAt = performance.now(); state.pausedFor = 0;
    ui.dot.classList.add('on'); setState('กำลังบันทึก', 'danger');
    ui.start.disabled = true; ui.pause.disabled = false; ui.stop.disabled = false; ui.markerAdd.disabled = false; if (ui.close) ui.close.disabled = true;
  }
  function pause() {
    if (!state.recorder) return;
    if (state.paused) { state.recorder.resume(); state.paused = false; state.pausedFor += performance.now() - state.pauseStart; ui.pause.textContent = 'พัก'; setState('กำลังบันทึก', 'danger'); ui.dot.classList.add('on'); }
    else { state.recorder.pause(); state.paused = true; state.pauseStart = performance.now(); ui.pause.textContent = 'บันทึกต่อ'; setState('พักการบันทึก', 'warning'); ui.dot.classList.remove('on'); }
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
    try { await getStream(); } catch (e) { setState('ใช้ไมค์ไม่ได้', 'danger'); return; }
    const parts = [];
    const rec = new MediaRecorder(state.stream);
    rec.ondataavailable = (e) => parts.push(e.data);
    rec.onstop = () => { ui.testAudio.src = URL.createObjectURL(new Blob(parts)); ui.testAudio.hidden = false; ui.testAudio.play(); setState('ฟังเสียงทดสอบ', 'muted'); };
    rec.start(); setState('กำลังทดสอบไมค์ 5 วินาที…', 'warning');
    setTimeout(() => rec.stop(), 5000);
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
