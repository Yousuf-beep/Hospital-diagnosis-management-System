/* MediAI shared UI behaviour. Loaded at the end of <body> by base.html,
   before any page script. Vanilla JS, no dependencies. */
(() => {
  'use strict';
  const root = document.documentElement;
  const reduceMotion = () => window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* storage unavailable */ } },
  };

  // ── CSRF: add the token to every POST form and every same-origin fetch() ──
  const tokenMeta = document.querySelector('meta[name="csrf-token"]');
  const token = tokenMeta ? tokenMeta.content : '';
  document.querySelectorAll('form').forEach(form => {
    if ((form.method || '').toLowerCase() !== 'post' || form.querySelector('input[name="csrf_token"]')) return;
    const input = document.createElement('input');
    input.type = 'hidden'; input.name = 'csrf_token'; input.value = token;
    form.appendChild(input);
  });
  const origFetch = window.fetch;
  window.fetch = (resource, init = {}) => {
    const url = new URL(typeof resource === 'string' ? resource : resource.url, location.href);
    if (url.origin === location.origin) {
      init.headers = new Headers(init.headers || {});
      init.headers.set('X-CSRFToken', token);
    }
    return origFetch(resource, init);
  };

  // ── Theme toggle ──
  const themeButtons = document.querySelectorAll('[data-theme-toggle]');
  const effectiveTheme = () => root.dataset.theme ||
    (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
  const syncThemeButtons = () => {
    const dark = effectiveTheme() === 'dark';
    themeButtons.forEach(b => {
      b.setAttribute('aria-pressed', String(dark));
      b.setAttribute('aria-label', dark ? 'Switch to light theme' : 'Switch to dark theme');
      const icon = b.querySelector('i');
      if (icon) icon.className = dark ? 'fa-solid fa-sun' : 'fa-solid fa-moon';
      const label = b.querySelector('[data-theme-label]');
      if (label) label.textContent = dark ? 'Light theme' : 'Dark theme';
    });
  };
  themeButtons.forEach(b => b.addEventListener('click', () => {
    const next = effectiveTheme() === 'dark' ? 'light' : 'dark';
    root.dataset.theme = next;
    store.set('mediai-theme', next);
    syncThemeButtons();
    document.dispatchEvent(new CustomEvent('mediai:themechange', { detail: { theme: next } }));
  }));
  syncThemeButtons();

  // ── Navigation drawer (top nav on mobile, sidebar on tablet/mobile) ──
  document.querySelectorAll('[data-nav-toggle]').forEach(btn => {
    const container = document.getElementById(btn.getAttribute('data-nav-toggle'));
    if (!container) return;
    const setOpen = open => {
      container.classList.toggle('is-open', open);
      document.querySelectorAll(`[data-nav-toggle="${container.id}"]`).forEach(b => b.setAttribute('aria-expanded', String(open)));
      if (open) {
        const first = container.querySelector('.sidebar a, .topnav__links a');
        if (first && container.classList.contains('shell')) first.focus();
      }
    };
    btn.addEventListener('click', () => setOpen(!container.classList.contains('is-open')));
    container.querySelectorAll('[data-nav-close]').forEach(el => el.addEventListener('click', () => setOpen(false)));
    document.addEventListener('keydown', e => {
      if (e.key === 'Escape' && container.classList.contains('is-open')) { setOpen(false); btn.focus(); }
    });
  });

  // ── Toasts (flash messages and client-side results) ──
  const toastRegion = document.querySelector('.toasts');
  const ICONS = { success: 'circle-check', danger: 'triangle-exclamation', warning: 'circle-exclamation', info: 'circle-info' };
  function dismiss(toast) {
    if (!toast || toast.classList.contains('is-leaving')) return;
    if (reduceMotion()) { toast.remove(); return; }
    toast.classList.add('is-leaving');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
  }
  function wireToast(toast) {
    const close = toast.querySelector('[data-toast-close]');
    if (close) close.addEventListener('click', () => dismiss(toast));
    const kind = toast.dataset.kind || 'info';
    // Errors and warnings stay until dismissed; confirmations fade out.
    if (kind === 'success' || kind === 'info') {
      let timer = setTimeout(() => dismiss(toast), 6000);
      toast.addEventListener('mouseenter', () => clearTimeout(timer));
      toast.addEventListener('focusin', () => clearTimeout(timer));
      toast.addEventListener('mouseleave', () => { timer = setTimeout(() => dismiss(toast), 3000); });
    }
  }
  function toast(message, kind = 'info') {
    if (!toastRegion) return;
    const el = document.createElement('div');
    el.className = `toast toast--${kind}`;
    el.dataset.kind = kind;
    el.setAttribute('role', kind === 'danger' ? 'alert' : 'status');
    el.innerHTML = `<i class="fa-solid fa-${ICONS[kind] || ICONS.info}" aria-hidden="true"></i>
      <div class="toast__msg"></div>
      <button type="button" class="icon-btn" data-toast-close aria-label="Dismiss notification"><i class="fa-solid fa-xmark"></i></button>`;
    el.querySelector('.toast__msg').textContent = message;
    toastRegion.appendChild(el);
    wireToast(el);
    return el;
  }
  document.querySelectorAll('.toasts .toast').forEach(wireToast);

  // ── Real loading feedback on form submit ──
  // Only marks the button; the submit itself proceeds untouched. Buttons are
  // disabled after the entry list is built so name/value pairs still submit.
  document.addEventListener('submit', e => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement) || e.defaultPrevented) return;
    if (!form.hasAttribute('data-loading')) return;
    const btn = e.submitter && e.submitter.matches('button, input[type=submit]') ? e.submitter : form.querySelector('[type=submit]');
    if (btn) {
      btn.classList.add('is-loading');
      btn.setAttribute('aria-busy', 'true');
      if (btn.dataset.loadingText) {
        const span = btn.querySelector('span') || btn;
        if (span !== btn) span.textContent = btn.dataset.loadingText;
      }
      setTimeout(() => form.querySelectorAll('[type=submit]').forEach(b => { b.disabled = true; }), 0);
    }
    form.setAttribute('aria-busy', 'true');
    document.dispatchEvent(new CustomEvent('mediai:submitting', { detail: { form, submitter: e.submitter } }));
  });
  // Restore buttons when the page comes back from the back/forward cache
  window.addEventListener('pageshow', e => {
    if (!e.persisted) return;
    document.querySelectorAll('.is-loading').forEach(b => { b.classList.remove('is-loading'); b.removeAttribute('aria-busy'); });
    document.querySelectorAll('form[aria-busy] [type=submit]').forEach(b => { b.disabled = false; });
    document.querySelectorAll('form[aria-busy]').forEach(f => f.removeAttribute('aria-busy'));
  });

  // ── Dialogs ──
  document.querySelectorAll('[data-dialog-open]').forEach(btn => btn.addEventListener('click', () => {
    const dlg = document.getElementById(btn.getAttribute('data-dialog-open'));
    if (dlg && typeof dlg.showModal === 'function') { dlg.showModal(); dlg._opener = btn; }
  }));
  document.querySelectorAll('dialog.modal').forEach(dlg => {
    dlg.addEventListener('click', e => { if (e.target === dlg) dlg.close('cancel'); });
    dlg.querySelectorAll('[data-dialog-close]').forEach(b => b.addEventListener('click', () => dlg.close('cancel')));
    dlg.addEventListener('close', () => { if (dlg._opener) dlg._opener.focus(); });
  });

  // ── Confirmation for consequential actions: <form data-confirm="..."> ──
  const confirmDlg = document.getElementById('mai-confirm');
  function confirmAction({ title, message, action = 'Confirm', tone = 'danger' }) {
    return new Promise(resolve => {
      if (!confirmDlg || typeof confirmDlg.showModal !== 'function') { resolve(window.confirm(message)); return; }
      confirmDlg.querySelector('[data-confirm-title]').textContent = title || 'Are you sure?';
      confirmDlg.querySelector('[data-confirm-text]').textContent = message || '';
      const ok = confirmDlg.querySelector('[data-confirm-ok]');
      ok.querySelector('span').textContent = action;
      ok.className = `btn btn--${tone === 'danger' ? 'danger' : 'primary'}`;
      const icon = confirmDlg.querySelector('.modal__icon');
      icon.className = `modal__icon tone-${tone}`;
      const onClose = () => { confirmDlg.removeEventListener('close', onClose); resolve(confirmDlg.returnValue === 'ok'); };
      confirmDlg.addEventListener('close', onClose);
      confirmDlg.returnValue = '';
      confirmDlg.showModal();
      ok.focus();
    });
  }
  if (confirmDlg) {
    confirmDlg.querySelector('[data-confirm-ok]').addEventListener('click', () => confirmDlg.close('ok'));
  }
  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', async e => {
      if (form.dataset.confirmed === '1') { form.dataset.confirmed = ''; return; }
      e.preventDefault();
      e.stopImmediatePropagation();
      const submitter = e.submitter;
      const ok = await confirmAction({
        title: form.dataset.confirmTitle, message: form.dataset.confirm,
        action: form.dataset.confirmAction, tone: form.dataset.confirmTone || 'danger',
      });
      if (!ok) return;
      form.dataset.confirmed = '1';
      if (typeof form.requestSubmit === 'function') form.requestSubmit(submitter || undefined);
      else form.submit();
    }, true);
  });

  // ── Tabs: [data-tabs] containing [role=tab][aria-controls]; supports #hash ──
  document.querySelectorAll('[data-tabs]').forEach(list => {
    const tabs = [...list.querySelectorAll('[role=tab]')];
    const select = (tab, focus) => {
      tabs.forEach(t => {
        const on = t === tab;
        t.setAttribute('aria-selected', String(on));
        t.tabIndex = on ? 0 : -1;
        const panel = document.getElementById(t.getAttribute('aria-controls'));
        if (panel) panel.hidden = !on;
      });
      if (focus) tab.focus();
      if (list.hasAttribute('data-tabs-hash')) history.replaceState(null, '', '#' + tab.dataset.key);
      document.dispatchEvent(new CustomEvent('mediai:tab', { detail: { key: tab.dataset.key } }));
    };
    tabs.forEach((t, i) => {
      t.addEventListener('click', () => select(t));
      t.addEventListener('keydown', e => {
        const dir = e.key === 'ArrowRight' ? 1 : e.key === 'ArrowLeft' ? -1 : 0;
        if (dir) { e.preventDefault(); select(tabs[(i + dir + tabs.length) % tabs.length], true); }
        if (e.key === 'Home') { e.preventDefault(); select(tabs[0], true); }
        if (e.key === 'End') { e.preventDefault(); select(tabs[tabs.length - 1], true); }
      });
    });
    const fromHash = () => {
      const key = location.hash.slice(1);
      const tab = key && tabs.find(t => t.dataset.key === key);
      if (tab) select(tab);
    };
    if (list.hasAttribute('data-tabs-hash')) { fromHash(); window.addEventListener('hashchange', fromHash); }
  });

  // ── Client-side filtering of already-rendered rows ──
  // <input data-filter="#target"> filters children marked [data-filter-item]
  function applyFilter(scope) {
    const input = scope.querySelector('[data-filter]');
    const target = document.querySelector(scope.dataset.filterScope || (input && input.dataset.filter));
    if (!target) return;
    const q = input ? input.value.trim().toLowerCase() : '';
    const statusBtn = scope.querySelector('[data-filter-status][aria-pressed="true"]');
    const status = statusBtn ? statusBtn.dataset.filterStatus : 'all';
    let shown = 0;
    target.querySelectorAll('[data-filter-item]').forEach(item => {
      const matchText = !q || item.textContent.toLowerCase().includes(q);
      const matchStatus = status === 'all' || item.dataset.status === status;
      item.hidden = !(matchText && matchStatus);
      if (!item.hidden) shown++;
    });
    target.querySelectorAll('[data-filter-group]').forEach(g => {
      g.hidden = !g.querySelector('[data-filter-item]:not([hidden])');
    });
    const empty = document.querySelector(scope.dataset.filterEmpty || '#__none');
    if (empty) empty.hidden = shown !== 0;
    const live = scope.querySelector('[data-filter-count]');
    if (live) live.textContent = `${shown} shown`;
  }
  document.querySelectorAll('[data-filter-scope]').forEach(scope => {
    const input = scope.querySelector('[data-filter]');
    if (input) input.addEventListener('input', () => applyFilter(scope));
    scope.querySelectorAll('[data-filter-status]').forEach(btn => btn.addEventListener('click', () => {
      scope.querySelectorAll('[data-filter-status]').forEach(b => b.setAttribute('aria-pressed', String(b === btn)));
      applyFilter(scope);
    }));
  });

  // ── Count-up toward the real server-rendered number ──
  function countUp(el) {
    const text = el.textContent.trim();
    const target = parseFloat(text);
    if (!isFinite(target) || reduceMotion()) return;
    const decimals = (text.split('.')[1] || '').replace(/\D.*/, '').length;
    const suffix = text.replace(/^[\d.,-]+/, '');
    const dur = parseInt(el.dataset.countup, 10) || 600;
    const start = performance.now();
    const ease = t => 1 - Math.pow(1 - t, 3);
    const step = now => {
      const t = Math.min(1, (now - start) / dur);
      el.textContent = (target * ease(t)).toFixed(decimals) + suffix;
      if (t < 1) requestAnimationFrame(step); else el.textContent = text;
    };
    el.setAttribute('aria-label', text);
    requestAnimationFrame(step);
  }
  document.querySelectorAll('[data-countup]').forEach(countUp);

  // ── Chart.js theming from CSS tokens (call before creating charts) ──
  const cssVar = name => getComputedStyle(root).getPropertyValue(name).trim();
  function chartTokens() {
    return {
      ink: cssVar('--mai-ink'), muted: cssVar('--mai-muted'), border: cssVar('--mai-border'),
      surface: cssVar('--mai-surface-raised'), primary: cssVar('--mai-primary'), primarySoft: cssVar('--mai-primary-soft'),
      accent: cssVar('--mai-accent'), info: cssVar('--mai-info'), warning: cssVar('--mai-warning'),
      success: cssVar('--mai-success'), danger: cssVar('--mai-danger'),
    };
  }
  function applyChartDefaults(Chart) {
    const t = chartTokens();
    Chart.defaults.font.family = "'DM Sans', 'Segoe UI', system-ui, sans-serif";
    Chart.defaults.font.size = 12;
    Chart.defaults.color = t.muted;
    Chart.defaults.borderColor = t.border;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.responsive = true;
    Chart.defaults.animation = reduceMotion() ? false : { duration: 700, easing: 'easeOutCubic' };
    Object.assign(Chart.defaults.plugins.tooltip, {
      backgroundColor: t.ink, titleColor: t.surface, bodyColor: t.surface,
      padding: 10, cornerRadius: 8, displayColors: true, boxPadding: 4,
      titleFont: { weight: '600' },
    });
    Chart.defaults.plugins.legend.labels.usePointStyle = true;
    Chart.defaults.plugins.legend.labels.boxWidth = 8;
    Chart.defaults.plugins.legend.labels.boxHeight = 8;
    Chart.defaults.plugins.legend.labels.pointStyleWidth = 10;
    Chart.defaults.plugins.legend.labels.padding = 16;
    return t;
  }
  // Re-colour existing charts when the theme changes
  document.addEventListener('mediai:themechange', () => {
    if (!window.Chart) return;
    applyChartDefaults(window.Chart);
    document.dispatchEvent(new CustomEvent('mediai:recolor-charts', { detail: chartTokens() }));
  });

  window.MediAI = { toast, confirm: confirmAction, countUp, chartTokens, applyChartDefaults, reduceMotion };
})();
