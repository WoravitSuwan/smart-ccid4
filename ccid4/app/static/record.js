// Interview recorder: MediaRecorder chunks are uploaded in order every 5 s.
// Failed uploads stay in the queue and are retried, so a network drop does not lose audio.
(function () {
  const root = document.getElementById('rec');
  if (!root) return;
  const iid = root.dataset.iid;
  const csrf = document.querySelector('meta[name=csrf]').content;
  const $ = (id) => document.getElementById(id);
  const pad = (n) => String(Math.floor(n)).padStart(2, '0');
  const fmt = (s) => `${pad(s / 3600)}:${pad((s / 60) % 60)}:${pad(s % 60)}`;
  const mmss = (s) => `${pad(s / 60)}:${pad(s % 60)}`;

  let rec, stream, seq = 0, queue = [], sending = false, stopping = false;
  let startedAt = 0, pausedTotal = 0, pausedAt = 0, paused = false, marks = [];

  const elapsed = () => {
    if (!startedAt) return 0;
    const t = paused ? pausedAt : performance.now();
    return (t - startedAt - pausedTotal) / 1000;
  };

  // level meter
  const bars = [];
  for (let i = 0; i < 32; i++) { const s = document.createElement('span'); $('meter').appendChild(s); bars.push(s); }
  let analyser, data;
  function draw() {
    if (analyser && !paused) {
      analyser.getByteFrequencyData(data);
      bars.forEach((b, i) => { const v = data[Math.floor(i * data.length / bars.length)] / 255; b.style.height = (6 + v * 50) + 'px'; });
    } else bars.forEach((b) => { b.style.height = '6px'; });
    $('timer').textContent = fmt(elapsed());
    requestAnimationFrame(draw);
  }

  async function pump() {
    if (sending) return;
    sending = true;
    while (queue.length) {
      const item = queue[0];
      try {
        const r = await fetch(`/interviews/${iid}/chunk`, {
          method: 'POST', body: item.blob,
          headers: { 'content-type': item.blob.type || 'audio/webm', 'x-seq': String(item.seq), 'x-elapsed': String(item.t), 'x-csrf-token': csrf },
        });
        if (r.status === 401) { $('queue').textContent = 'หมดเวลาเข้าสู่ระบบ เสียงยังค้างในหน้านี้ เข้าสู่ระบบในแท็บใหม่แล้วกลับมา'; throw new Error('login'); }
        if (!r.ok) throw new Error(await r.text());
        const j = await r.json();
        $('synced').textContent = `บันทึกขึ้นเซิร์ฟเวอร์แล้วถึงนาทีที่ ${mmss(j.uploaded_until)}`;
        queue.shift();
        $('queue').textContent = queue.length ? `รอส่ง ${queue.length} ช่วง` : 'ส่งครบแล้ว';
      } catch (e) {
        $('queue').textContent = `ส่งไม่สำเร็จ จะลองใหม่ (รอส่ง ${queue.length} ช่วง)`;
        await new Promise((ok) => setTimeout(ok, 3000));
      }
    }
    sending = false;
  }

  async function start() {
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: true } });
    } catch (e) {
      $('state').textContent = 'ใช้ไมโครโฟนไม่ได้ ตรวจการอนุญาตของเบราว์เซอร์';
      return;
    }
    const type = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/mp4'].find((t) => window.MediaRecorder && MediaRecorder.isTypeSupported(t));
    rec = new MediaRecorder(stream, type ? { mimeType: type, audioBitsPerSecond: 64000 } : undefined);
    rec.ondataavailable = (ev) => { if (ev.data && ev.data.size) { queue.push({ blob: ev.data, seq: seq++, t: elapsed() }); pump(); } };
    rec.onstop = finish;
    const ctx = new AudioContext();
    analyser = ctx.createAnalyser(); analyser.fftSize = 128; data = new Uint8Array(analyser.frequencyBinCount);
    ctx.createMediaStreamSource(stream).connect(analyser);
    rec.start(5000);
    startedAt = performance.now();
    $('state').textContent = 'กำลังบันทึก';
    ['pause', 'mark', 'stop'].forEach((id) => { $(id).disabled = false; });
    requestAnimationFrame(draw);
  }

  $('pause').onclick = () => {
    if (!rec) return;
    if (!paused) { rec.pause(); paused = true; pausedAt = performance.now(); $('pause').textContent = 'บันทึกต่อ'; $('state').textContent = 'พักการบันทึก'; $('state').className = 'bd due'; }
    else { rec.resume(); pausedTotal += performance.now() - pausedAt; paused = false; $('pause').textContent = 'พัก'; $('state').textContent = 'กำลังบันทึก'; $('state').className = 'bd late'; }
  };

  $('mark').onclick = async () => {
    const t = elapsed();
    const note = prompt('บันทึกสั้น ๆ สำหรับเครื่องหมายนี้', 'เครื่องหมาย') || 'เครื่องหมาย';
    marks.push(`${mmss(t)} ${note}`);
    $('marks').textContent = 'เครื่องหมาย: ' + marks.join(' · ');
    const body = new URLSearchParams({ t: String(t), note, csrf });
    fetch(`/interviews/${iid}/mark`, { method: 'POST', body, headers: { 'x-csrf-token': csrf } }).catch(() => {});
  };

  $('stop').onclick = () => {
    if (!rec || stopping) return;
    if (!confirm('จบการบันทึกเสียง? หลังจบจะเพิ่มเสียงต่อไม่ได้')) return;
    stopping = true;
    ['pause', 'mark', 'stop'].forEach((id) => { $(id).disabled = true; });
    $('state').textContent = 'กำลังส่งเสียงช่วงสุดท้าย';
    rec.stop();
    stream.getTracks().forEach((t) => t.stop());
  };

  async function finish() {
    while (queue.length || sending) { await pump(); await new Promise((ok) => setTimeout(ok, 300)); }
    const r = await fetch(`/interviews/${iid}/finish`, { method: 'POST', headers: { 'x-csrf-token': csrf } });
    if (r.ok) { const j = await r.json(); window.onbeforeunload = null; location.href = j.redirect; }
    else $('state').textContent = 'ปิดไฟล์ไม่สำเร็จ ลองกดอีกครั้ง';
  }

  window.onbeforeunload = (e) => { if (rec && !stopping) { e.preventDefault(); return ''; } };
  start();
})();
