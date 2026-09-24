/* Espace client — comportements d'interface (aucune dépendance). */
(function () {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

  // Thème clair / sombre, mémorisé sur l'appareil
  $$('[data-theme-toggle]').forEach(btn => btn.addEventListener('click', () => {
    const root = document.documentElement;
    const next = root.dataset.theme === 'light' ? 'dark' : 'light';
    root.dataset.theme = next;
    try { localStorage.setItem('fs-theme', next); } catch (e) { /* stockage indisponible */ }
  }));

  // Menu latéral sur mobile
  $$('[data-open-nav]').forEach(b => b.addEventListener('click', () => document.body.classList.add('nav-open')));
  $$('[data-close-nav]').forEach(b => b.addEventListener('click', () => document.body.classList.remove('nav-open')));
  document.addEventListener('keydown', e => { if (e.key === 'Escape') document.body.classList.remove('nav-open'); });

  // Lignes de tableau cliquables
  $$('tr[data-href]').forEach(tr => tr.addEventListener('click', e => {
    if (e.target.closest('a, button, input')) return;
    window.location = tr.dataset.href;
  }));

  // Filtres + recherche de la liste des fichiers
  const root = $('[data-filter-root]');
  if (root) {
    let filter = 'all';
    const search = $('[data-search]', root);
    const rows = $$('tbody tr', root);
    const empty = $('[data-empty]', root);
    const apply = () => {
      const q = (search.value || '').trim().toLowerCase();
      let shown = 0;
      rows.forEach(tr => {
        const okStatus = filter === 'all' || filter.split(' ').includes(tr.dataset.status);
        const okText = !q || tr.dataset.text.includes(q);
        tr.hidden = !(okStatus && okText);
        if (!tr.hidden) shown++;
      });
      empty.hidden = shown > 0;
    };
    $$('[data-filter]', root).forEach(b => b.addEventListener('click', () => {
      $$('[data-filter]', root).forEach(x => x.classList.toggle('active', x === b));
      filter = b.dataset.filter;
      apply();
    }));
    search.addEventListener('input', apply);
  }

  // Formulaire d'envoi : zone de dépôt + récapitulatif des crédits
  const form = $('#newForm');
  if (form) {
    const drop = $('#drop');
    const input = $('#fileInput');
    const setFile = f => {
      if (!f) return;
      $('#fileName').textContent = f.name;
      $('#fileSize').textContent = (f.size / 1024 / 1024).toFixed(2).replace('.', ',') + ' Mo';
      drop.classList.add('has-file');
      update();
    };
    input.addEventListener('change', () => setFile(input.files[0]));
    ['dragenter', 'dragover'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
    ['dragleave', 'drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
    drop.addEventListener('drop', e => {
      const f = e.dataTransfer.files[0];
      if (!f) return;
      const dt = new DataTransfer();
      dt.items.add(f);
      input.files = dt.files;
      setFile(f);
    });

    const summary = $('#summary');
    const balance = parseInt(summary.dataset.balance, 10) || 0;
    const urgentCost = parseInt(summary.dataset.urgent, 10) || 0;
    const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);

    function update() {
      const picked = $$('input[name="prestas"]:checked', form);
      const urgent = $('#urgent').checked;
      let total = 0;
      let html = '';
      picked.forEach(p => {
        const c = parseInt(p.dataset.credits, 10) || 0;
        total += c;
        html += `<div class="summary-line"><span>${esc(p.dataset.name)}</span><span class="num">${c} cr.</span></div>`;
      });
      if (urgent && picked.length) {
        total += urgentCost;
        html += `<div class="summary-line"><span>Prioritaire</span><span class="num">${urgentCost} cr.</span></div>`;
      }
      $('#sumLines').innerHTML = html || '<p class="summary-empty">Aucune prestation sélectionnée.</p>';
      $('#sumTotal').textContent = total + ' cr.';
      const after = balance - total;
      const afterEl = $('#sumAfter');
      afterEl.textContent = after + ' cr.';
      afterEl.style.color = after < 0 ? 'var(--danger)' : '';
      $('#submitBtn').disabled = !picked.length || after < 0 || !input.files.length;
    }
    form.addEventListener('change', update);
    update();
  }
})();
