// raystream — browser side. Plain DOM, no build step: the server hands these files
// straight out of `std/embed`.
const grid = document.getElementById('grid');
const stage = document.getElementById('stage');
const statsEl = document.getElementById('stats');
const search = document.getElementById('q');
const roomName = document.getElementById('room-name');
const roomToggle = document.getElementById('room-toggle');
const roomDot = document.getElementById('room-state');

let kind = '';
let query = '';
let items = [];
let current = null;
let socket = null;
let applying = false;   // guard: state we apply must not echo back to the room

const icons = { video: '▶', audio: '♪', image: '▦' };

function human(bytes) {
  if (bytes < 1024) return bytes + ' B';
  const units = ['KB', 'MB', 'GB', 'TB'];
  let v = bytes / 1024, i = 0;
  while (v >= 1024 && i < units.length - 1) { v /= 1024; i++; }
  return v.toFixed(v < 10 ? 1 : 0) + ' ' + units[i];
}

function duration(ms) {
  if (!ms) return '';
  const total = Math.round(ms / 1000);
  return Math.floor(total / 60) + ':' + String(total % 60).padStart(2, '0');
}

async function loadStats() {
  const s = await fetch('/api/stats').then(r => r.json());
  statsEl.textContent =
    `${s.items} items · ${s.videos} video · ${s.audios} audio · ${s.images} images · ${human(s.bytes)}`;
  statsEl.title = s.root;
}

async function loadLibrary() {
  const params = new URLSearchParams();
  if (kind) params.set('kind', kind);
  if (query) params.set('q', query);
  items = await fetch('/api/library?' + params).then(r => r.json());
  render();
}

// Todo lo que viene de la biblioteca (nombres de fichero, etiquetas ID3, rutas) es texto que
// otro ha escrito: se inserta SIEMPRE con textContent o como valor de atributo, nunca como
// HTML. Un MP3 titulado `<img src=x onerror=…>` se ve tal cual, no se ejecuta.
function el(tag, props, children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props || {})) {
    if (key === 'text') node.textContent = value;
    else if (key === 'className') node.className = value;
    else node.setAttribute(key, value);
  }
  for (const child of children || []) node.append(child);
  return node;
}

function render() {
  if (!items.length) {
    grid.replaceChildren(el('li', { className: 'empty', text: 'Nothing here yet — drop files into the library directory.' }));
    return;
  }
  const cards = items.map(it => {
    const dims = it.width ? `${it.width}×${it.height}` : '';
    const cc = (it.subtitles || []).length ? 'CC' : '';
    const sub = [it.kind, human(it.size), duration(it.duration_ms) || dims, cc].filter(Boolean).join(' · ');
    const thumb = el('div', { className: 'thumb' });
    if (it.kind === 'image' || it.has_cover) {
      // encodeURIComponent: el id es hex, pero nada que acabe en una URL se da por bueno.
      thumb.style.backgroundImage = `url("/thumb/${encodeURIComponent(it.id)}")`;
    } else {
      thumb.textContent = icons[it.kind] || '•';
    }
    const li = el('li', { className: 'card' }, [
      thumb,
      el('div', { className: 'body' }, [
        el('div', { className: 'name', text: it.title || it.name }),
        el('div', { className: 'sub', text: sub }),
      ]),
    ]);
    li.addEventListener('click', () => open(it, true));
    return li;
  });
  grid.replaceChildren(...cards);
}

function open(it, broadcast) {
  current = it;
  stage.hidden = false;
  const src = '/media/' + encodeURIComponent(it.id);
  let media;
  if (it.kind === 'video' || it.kind === 'audio') {
    media = el(it.kind, { src, controls: '', autoplay: '' });
    if (it.kind === 'video') media.setAttribute('playsinline', '');
    (it.subtitles || []).forEach((t, i) => {
      const track = el('track', { kind: 'subtitles', src: t.url, srclang: t.lang || 'und', label: t.label });
      if (i === 0) track.default = true;
      media.append(track);
    });
  } else {
    media = el('img', { src, alt: it.name });
  }
  const subs = (it.subtitles || []).length ? ` · CC ${it.subtitles.map(t => t.label).join(', ')}` : '';
  const line = [it.artist, it.album].filter(Boolean).join(' — ');
  stage.replaceChildren(
    media,
    el('div', { className: 'meta' }, [
      el('div', { className: 'title', text: it.title || it.name }),
      (line || it.rel) + subs,
    ]),
  );
  wirePlayer();
  if (broadcast) send({ type: 'load', item_id: it.id, position_ms: 0 });
  stage.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function player() {
  return stage.querySelector('video, audio');
}

function wirePlayer() {
  const p = player();
  if (!p) return;
  p.addEventListener('play', () => send({ type: 'play', position_ms: Math.round(p.currentTime * 1000) }));
  p.addEventListener('pause', () => send({ type: 'pause', position_ms: Math.round(p.currentTime * 1000) }));
  p.addEventListener('seeked', () => send({ type: 'seek', position_ms: Math.round(p.currentTime * 1000) }));
}

// --- The room: shared playback over WebSocket ---

function send(message) {
  if (!socket || socket.readyState !== WebSocket.OPEN || applying) return;
  socket.send(JSON.stringify(message));
}

function applyState(state) {
  applying = true;
  try {
    if (state.item_id && (!current || current.id !== state.item_id)) {
      const it = items.find(x => x.id === state.item_id);
      if (it) open(it, false);
    }
    const p = player();
    if (p) {
      const target = state.position_ms / 1000;
      if (Math.abs(p.currentTime - target) > 1.5) p.currentTime = target;
      if (state.playing && p.paused) p.play().catch(() => {});
      if (!state.playing && !p.paused) p.pause();
    }
  } finally {
    setTimeout(() => { applying = false; }, 50);
  }
}

roomToggle.addEventListener('click', () => {
  if (socket) { socket.close(); return; }
  const name = roomName.value.trim() || 'salon';
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  socket = new WebSocket(`${proto}://${location.host}/ws/room/${encodeURIComponent(name)}`);
  socket.addEventListener('open', () => {
    roomToggle.textContent = 'Leave room';
    roomDot.classList.add('on');
    roomDot.title = 'in room ' + name;
  });
  socket.addEventListener('message', e => {
    const state = JSON.parse(e.data);
    if (state.type === 'state') applyState(state);
  });
  socket.addEventListener('close', () => {
    socket = null;
    roomToggle.textContent = 'Join room';
    roomDot.classList.remove('on');
    roomDot.title = 'not connected';
  });
});

// --- Live library over Server-Sent Events ---

const stream = new EventSource('/events');
stream.addEventListener('message', e => {
  const event = JSON.parse(e.data);
  if (event.type === 'library_changed') { loadStats(); loadLibrary(); }
});

// --- Filters ---

for (const b of document.querySelectorAll('.filters button')) {
  b.addEventListener('click', () => {
    document.querySelectorAll('.filters button').forEach(x => x.classList.remove('on'));
    b.classList.add('on');
    kind = b.dataset.kind;
    loadLibrary();
  });
}

let timer = null;
search.addEventListener('input', () => {
  clearTimeout(timer);
  timer = setTimeout(() => { query = search.value.trim(); loadLibrary(); }, 150);
});

loadStats();
loadLibrary();
