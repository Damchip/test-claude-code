/* Espace client — comportements d'interface (aucune dépendance). */
(function () {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const T = window.FS_T || { vide: 'Aucune prestation sélectionnée.', eco: 'Tarif pack appliqué : {n} crédits économisés',
                            indispo: 'Tarif indisponible, réessayez.', cr: 'cr.', pj: 'Pièce jointe : ' };
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

  // Pièce jointe d'un message : affiche le nom du fichier choisi
  $$('[data-pj]').forEach(inp => inp.addEventListener('change', () => {
    const out = $('[data-pj-name]');
    if (!out) return;
    out.hidden = !inp.files.length;
    out.textContent = inp.files.length ? T.pj + inp.files[0].name : '';
  }));

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
    const esc = s => String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
    const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
    let seq = 0;

    // N'affiche que les prestations du type de véhicule choisi (les services restent visibles)
    function showCategory() {
      const cat = ($('input[name="categorie"]:checked', form) || {}).value;
      $$('.option[data-cat]', form).forEach(o => {
        const on = o.dataset.cat === cat;
        o.hidden = !on;
        if (!on) $('input', o).checked = false;
      });
    }

    // Le prix est calculé par le serveur (/tarif) : une seule source de vérité
    async function update() {
      const cat = ($('input[name="categorie"]:checked', form) || {}).value;
      const codes = $$('input[name="prestas"]:checked', form).map(i => i.value);
      const body = {
        categorie: cat, prestations: codes,
        siege: $('#siege').checked,
        garantie: ($('input[name="garantie"]:checked', form) || {}).value || null,
      };
      const mine = ++seq;
      let d;
      try {
        const r = await fetch(summary.dataset.url, {
          method: 'POST', body: JSON.stringify(body),
          headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
        });
        d = await r.json();
      } catch (e) {
        d = { lignes: [], total: 0, economie: 0, siege_possible: false, erreur: T.indispo };
      }
      if (mine !== seq) return;   // une réponse plus récente est déjà arrivée

      $('#siegeBox').hidden = !d.siege_possible;
      $('#retourBox').hidden = !(d.siege_possible && $('#siege').checked);
      $('#sumLines').innerHTML = d.lignes.length
        ? d.lignes.map(l => `<div class="summary-line"><span>${esc(l.nom)}</span><span class="num">${l.credits} ${T.cr}</span></div>`).join('')
        : `<p class="summary-empty">${esc(T.vide)}</p>`;
      const saving = $('#sumSaving');
      saving.hidden = !(d.economie > 0);
      saving.textContent = T.eco.replace('{n}', d.economie);
      const err = $('#sumError');
      err.hidden = !d.erreur;
      err.textContent = d.erreur || '';
      $('#sumTotal').textContent = d.total + ' ' + T.cr;
      const after = balance - d.total;
      const afterEl = $('#sumAfter');
      afterEl.textContent = after + ' ' + T.cr;
      afterEl.style.color = after < 0 ? 'var(--danger)' : '';
      $('#submitBtn').disabled = !codes.length || after < 0 || !!d.erreur || !input.files.length;
    }
    form.addEventListener('change', e => {
      if (e.target.name === 'categorie') showCategory();
      update();
    });
    showCategory();
    update();
  }
})();

/* Confirmation avant envoi d'un formulaire sensible (texte traduit dans data-confirm) */
document.addEventListener("submit", e => {
  const msg = e.target.dataset && e.target.dataset.confirm;
  if (msg && !window.confirm(msg)) e.preventDefault();
});
