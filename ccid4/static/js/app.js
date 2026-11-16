// CCID4 client behaviour. Alpine's CSP build only allows components registered here (no inline JS).
document.addEventListener('alpine:init', () => {
  const csrf = () => document.querySelector('meta[name="csrf-token"]').content;

  Alpine.data('shell', () => ({
    collapsed: false,
    init() {
      try { this.collapsed = localStorage.getItem('ccid4.nav') === 'collapsed'; } catch (e) { /* storage blocked */ }
    },
    toggleNav() {
      this.collapsed = !this.collapsed;
      try { localStorage.setItem('ccid4.nav', this.collapsed ? 'collapsed' : 'open'); } catch (e) { /* ignore */ }
    },
    get navLabel() { return this.collapsed ? 'ขยายเมนู' : 'ย่อเมนู'; },
    get expanded() { return !this.collapsed; },
    get navClass() { return this.collapsed ? 'w-[72px]' : 'w-64'; },
    toggleTheme() {
      const root = document.documentElement;
      const next = root.dataset.theme === 'light' ? 'dark' : 'light';
      root.dataset.theme = next;
      const body = new URLSearchParams({ theme: next });
      fetch('/accounts/theme/', { method: 'POST', body, headers: { 'X-CSRFToken': csrf(), 'HX-Request': 'true' } });
    },
  }));

  // Numbers that count up when they appear: <b x-data="countUp" data-value="42"></b>
  Alpine.data('countUp', () => ({
    shown: '0',
    init() {
      const target = parseFloat(this.$el.dataset.value || '0');
      const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      if (reduce || !target) { this.shown = target.toLocaleString('th-TH'); this.$el.textContent = this.shown; return; }
      const t0 = performance.now(), dur = 900;
      const step = (t) => {
        const k = Math.min(1, (t - t0) / dur), eased = 1 - Math.pow(1 - k, 3);
        this.$el.textContent = Math.round(target * eased).toLocaleString('th-TH');
        if (k < 1) requestAnimationFrame(step);
      };
      requestAnimationFrame(step);
    },
  }));

  // Toast messages slide in and leave on their own (errors stay until closed).
  Alpine.data('toast', () => ({
    open: true,
    init() { if (!this.$el.classList.contains('toast-error')) setTimeout(() => { this.open = false; }, 5000); },
    close() { this.open = false; },
  }));

  // Collapsible right-hand panel on the case page; remembers its state per browser.
  Alpine.data('sidePanel', () => ({
    open: true,
    init() {
      try { this.open = localStorage.getItem('ccid4.panel') !== 'closed'; } catch (e) { /* storage blocked */ }
    },
    toggle() {
      this.open = !this.open;
      try { localStorage.setItem('ccid4.panel', this.open ? 'open' : 'closed'); } catch (e) { /* ignore */ }
    },
    get closed() { return !this.open; },
    get toggleLabel() { return this.open ? 'ซ่อนแผงงาน' : 'แสดงแผงงาน'; },
    get panelClass() { return this.open ? 'lg:w-80' : 'lg:w-16'; },
  }));

  // Live countdown for hour-based deadlines: <span x-data="countdown" data-until="ISO">เหลือ …</span>
  Alpine.data('countdown', () => ({
    init() {
      const until = new Date(this.$el.dataset.until).getTime();
      const tick = () => {
        const secs = Math.round((until - Date.now()) / 1000);
        if (secs < 0) {
          const h = Math.ceil(-secs / 3600);
          this.$el.textContent = h < 48 ? `เลยกำหนด ${h} ชม.` : `เลยกำหนด ${Math.floor(h / 24)} วัน`;
          return;
        }
        const h = Math.floor(secs / 3600), m = Math.floor((secs % 3600) / 60);
        this.$el.textContent = h < 48 ? `เหลือ ${h} ชม. ${m} นาที` : `เหลือ ${Math.floor(h / 24)} วัน ${h % 24} ชม.`;
      };
      tick();
      this.timer = setInterval(tick, 30000);
    },
    destroy() { clearInterval(this.timer); },
  }));

  // Unsaved-changes guard used by forms with x-data="dirtyForm".
  Alpine.data('dirtyForm', () => ({
    dirty: false,
    init() {
      // Inputs that belong to another form (form="...") do not make this one dirty.
      const mark = (e) => { if (!e.target.form || e.target.form === this.$el) this.dirty = true; };
      this.$el.addEventListener('input', mark);
      this.$el.addEventListener('change', mark);
      this.$el.addEventListener('submit', () => { this.dirty = false; });
      window.addEventListener('beforeunload', (e) => { if (this.dirty) { e.preventDefault(); e.returnValue = ''; } });
    },
    get clean() { return !this.dirty; },
    get dirtyClass() { return this.dirty ? 'is-dirty' : ''; },
    get saveLabel() { return this.dirty ? 'บันทึก (ยังไม่บันทึก)' : 'บันทึก'; },
  }));
});

// ---------- Tabs: <div data-tabs="name"> with [role=tab][data-tab] buttons and [role=tabpanel][data-panel] panels ----------
function tabRoot(el) { return el.closest('[data-tabs]'); }
function ownTabs(root, sel) { return [...root.querySelectorAll(sel)].filter((el) => tabRoot(el) === root); }

function selectTab(root, name, focus) {
  const tabs = ownTabs(root, '[role="tab"]');
  if (!tabs.some((t) => t.dataset.tab === name)) return;
  tabs.forEach((t) => {
    const on = t.dataset.tab === name;
    t.setAttribute('aria-selected', on ? 'true' : 'false');
    t.tabIndex = on ? 0 : -1;
    if (on && focus) t.focus();
  });
  ownTabs(root, '[role="tabpanel"]').forEach((p) => { p.hidden = p.dataset.panel !== name; });
  const field = root.querySelector(':scope > input[data-tab-field]') || document.querySelector(`input[data-tab-field="${root.dataset.tabs}"]`);
  if (field) field.value = name;
  if (root.dataset.tabs === 'case' && history.replaceState) history.replaceState(null, '', `#${name}`);
}

document.addEventListener('click', (e) => {
  const tab = e.target.closest('[role="tab"][data-tab]');
  if (tab && !tab.disabled) { selectTab(tabRoot(tab), tab.dataset.tab); return; }
  const jump = e.target.closest('[data-goto-tab]');
  if (jump) { const root = document.querySelector('[data-tabs="case"]'); if (root) selectTab(root, jump.dataset.gotoTab, true); }
});

document.addEventListener('keydown', (e) => {
  const tab = e.target.closest && e.target.closest('[role="tab"][data-tab]');
  if (!tab || !['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(e.key)) return;
  const tabs = ownTabs(tabRoot(tab), '[role="tab"]').filter((t) => !t.disabled);
  let i = tabs.indexOf(tab);
  if (e.key === 'ArrowRight') i = (i + 1) % tabs.length;
  if (e.key === 'ArrowLeft') i = (i - 1 + tabs.length) % tabs.length;
  if (e.key === 'Home') i = 0;
  if (e.key === 'End') i = tabs.length - 1;
  e.preventDefault();
  selectTab(tabRoot(tab), tabs[i].dataset.tab, true);
});

function initTabs(scope) {
  scope.querySelectorAll('[data-tabs]').forEach((root) => {
    const wanted = root.dataset.tabs === 'case' ? (root.dataset.initial || location.hash.slice(1)) : '';
    const tabs = ownTabs(root, '[role="tab"]').filter((t) => !t.disabled);
    const pick = tabs.find((t) => t.dataset.tab === wanted) || tabs.find((t) => t.getAttribute('aria-selected') === 'true') || tabs[0];
    if (pick) selectTab(root, pick.dataset.tab);
  });
}

// ---------- Formsets: add rows/people from <template> without a page reload ----------
const thaiDigits = (v) => String(v).replace(/[0-9]/g, (d) => '๐๑๒๓๔๕๖๗๘๙'[d]);

function formsetAdd(prefix) {
  const total = document.querySelector(`input[name="${prefix}-TOTAL_FORMS"]`);
  if (!total) return;
  const n = parseInt(total.value, 10);
  const fill = (tpl) => tpl.innerHTML.replaceAll('__prefix__', String(n)).replaceAll('__num__', thaiDigits(n + 1));
  const rowTpl = document.getElementById(`tpl-${prefix}`);
  const tabTpl = document.getElementById(`tpl-${prefix}-tab`);
  const rows = document.querySelector(`[data-formset-rows="${prefix}"]`);
  const tabs = document.querySelector(`[data-formset-tabs="${prefix}"]`);
  if (rowTpl && rows) rows.insertAdjacentHTML('beforeend', fill(rowTpl));
  if (tabTpl && tabs) tabs.insertAdjacentHTML('beforeend', fill(tabTpl));
  total.value = String(n + 1);
  const count = document.querySelector(`[data-count-for="${prefix}"]`);
  if (count) count.textContent = thaiDigits(n + 1);
  const empty = document.querySelector(`[data-formset-empty="${prefix}"]`);
  if (empty) empty.hidden = true;
  if (tabs) selectTab(tabRoot(tabs), `${prefix}-${n}`);
  const first = rows && rows.lastElementChild && rows.lastElementChild.querySelector('input:not([type=hidden]), textarea');
  if (first) first.focus();
  total.dispatchEvent(new Event('input', { bubbles: true }));
}

document.addEventListener('click', (e) => {
  const add = e.target.closest('[data-formset-add]');
  if (add) { e.preventDefault(); formsetAdd(add.dataset.formsetAdd); }
});

// Native <dialog> modals: [data-dialog-open="id"] opens, [data-dialog-close] closes.
document.addEventListener('click', (e) => {
  const opener = e.target.closest('[data-dialog-open]');
  if (opener) {
    const dlg = document.getElementById(opener.dataset.dialogOpen);
    if (dlg && dlg.showModal) { dlg.showModal(); const first = dlg.querySelector('input, select, textarea'); if (first) first.focus(); }
    return;
  }
  const closer = e.target.closest('[data-dialog-close]');
  if (closer) { const dlg = closer.closest('dialog'); if (dlg) dlg.close(); return; }
  if (e.target.tagName === 'DIALOG') e.target.close(); // click on the backdrop
});

// Deleting a person/row is marked visually until saved.
document.addEventListener('change', (e) => {
  const box = e.target.closest('input[type=checkbox][name$="-DELETE"]');
  if (!box) return;
  const holder = box.closest('[data-panel], tr');
  if (holder) holder.classList.toggle('is-deleted', box.checked);
  if (holder && holder.dataset.panel) {
    const tab = document.querySelector(`[role="tab"][data-tab="${holder.dataset.panel}"]`);
    if (tab) tab.classList.toggle('is-deleted', box.checked);
  }
});

// Sub-tab labels follow the name typed into that person's form.
document.addEventListener('input', (e) => {
  const el = e.target;
  if (!el.name || !/-(title|first_name|last_name)$/.test(el.name)) return;
  const panel = el.closest('[data-panel]');
  if (!panel) return;
  const pre = el.name.replace(/(title|first_name|last_name)$/, '');
  const v = (k) => (panel.querySelector(`[name="${pre}${k}"]`) || {}).value || '';
  const name = `${v('title')}${v('first_name')} ${v('last_name')}`.trim();
  const label = document.querySelector(`[role="tab"][data-tab="${panel.dataset.panel}"] [data-tab-name]`);
  if (label && name) label.textContent = name;
});

// ---------- Documents page (A6.1) ----------
function updateDocSelection() {
  const count = document.querySelector('[data-selected-count]');
  const checked = document.querySelectorAll('[data-doc-check]:checked');
  if (count) count.textContent = thaiDigits(checked.length);
  document.querySelectorAll('[data-doc-check]').forEach((box) => {
    const items = document.querySelector(`[data-items-for="${box.value}"]`);
    if (items) items.hidden = !box.checked;
    box.closest('.doc-card').classList.toggle('is-selected', box.checked);
  });
}
document.addEventListener('change', (e) => { if (e.target.matches('[data-doc-check]')) updateDocSelection(); });
document.addEventListener('input', (e) => {
  if (!e.target.matches('[data-doc-search]')) return;
  const q = e.target.value.trim().toLowerCase();
  document.querySelectorAll('.doc-card').forEach((card) => { card.hidden = q && !card.dataset.title.toLowerCase().includes(q); });
  document.querySelectorAll('[data-doc-group]').forEach((g) => { g.hidden = ![...g.querySelectorAll('.doc-card')].some((c) => !c.hidden); });
});

// AI suggestions: fill the form fields named in data-apply-ai (the officer still reviews and saves).
document.addEventListener('click', (e) => {
  const apply = e.target.closest('[data-apply-ai]');
  if (apply) {
    let values = {};
    try { values = JSON.parse(apply.dataset.applyAi); } catch (err) { return; }
    let first = null;
    Object.entries(values).forEach(([name, value]) => {
      const el = document.querySelector(`[name="${CSS.escape(name)}"]`);
      if (!el) return;
      el.value = value === null ? '' : String(value);
      el.dispatchEvent(new Event('input', { bubbles: true }));
      if (el.type !== 'hidden') { el.classList.add('focus-flash', 'ai-filled'); first = first || el; }
    });
    const panel = apply.closest('.ai-panel');
    if (panel) panel.remove();
    if (first) first.scrollIntoView({ block: 'center', behavior: 'smooth' });
    return;
  }
  const dismiss = e.target.closest('[data-dismiss-ai]');
  if (dismiss) { const panel = dismiss.closest('.ai-panel'); if (panel) panel.remove(); }
});

// Buttons that navigate (so they can be disabled while the form is dirty).
document.addEventListener('click', (e) => {
  const b = e.target.closest('button[data-href]');
  if (b && !b.disabled) window.location.href = b.dataset.href;
});

// ?focus=<field name> opens the right tab and puts the cursor in that field (links from "ยังขาด").
function focusFromQuery() {
  const name = new URLSearchParams(window.location.search).get('focus');
  if (!name) return;
  const el = document.querySelector(`[name="${CSS.escape(name)}"]`);
  if (!el) return;
  let panel = el.closest('[role="tabpanel"]');
  while (panel) {
    const root = tabRoot(panel);
    if (root) selectTab(root, panel.dataset.panel);
    panel = root ? root.parentElement && root.parentElement.closest('[role="tabpanel"]') : null;
  }
  if (el.type === 'hidden') return;
  el.focus();
  el.classList.add('focus-flash');
  el.scrollIntoView({ block: 'center', behavior: 'smooth' });
}

// HTMX sends the CSRF token on every request.
document.addEventListener('htmx:configRequest', (e) => {
  e.detail.headers['X-CSRFToken'] = document.querySelector('meta[name="csrf-token"]').content;
});

// Stagger elements marked .reveal inside the same parent.
document.addEventListener('DOMContentLoaded', () => {
  initTabs(document);
  focusFromQuery();
  updateDocSelection();
  const groups = new Map();
  document.querySelectorAll('.reveal').forEach((el) => {
    const n = groups.get(el.parentElement) || 0;
    el.style.setProperty('--i', String(n));
    groups.set(el.parentElement, n + 1);
  });
});
