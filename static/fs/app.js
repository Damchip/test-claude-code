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
        express: !!($('#express') && $('#express').checked),
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
      const ann = $('#annexes');
      const tropAnnexes = ann && ann.files.length > (parseInt(ann.dataset.annexes, 10) || 4);
      if (tropAnnexes && !d.erreur) { err.hidden = false; err.textContent = T.annexes || 'Trop de fichiers complémentaires.'; }
      $('#submitBtn').disabled = !codes.length || after < 0 || !!d.erreur || !input.files.length || tropAnnexes;
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

/* Application installable (PWA) et notifications push */
(function () {
  'use strict';
  const T = window.FS_T || {};
  const $ = s => document.querySelector(s);
  const csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  const swOk = 'serviceWorker' in navigator && window.isSecureContext;
  const pushOk = swOk && 'PushManager' in window && 'Notification' in window;
  const ios = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const installee = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone === true;

  const inscription = swOk ? navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => null) : Promise.resolve(null);

  // Bouton « Installer l'application » (Chrome, Edge, Android) : le navigateur propose, on déclenche
  let invite = null;
  window.addEventListener('beforeinstallprompt', e => {
    e.preventDefault();
    invite = e;
    const b = $('[data-install]');
    if (b) b.hidden = false;
  });
  window.addEventListener('appinstalled', () => { const b = $('[data-install]'); if (b) b.hidden = true; });
  const bInstall = $('[data-install]');
  if (bInstall) bInstall.addEventListener('click', async () => {
    if (!invite) return;
    invite.prompt();
    await invite.userChoice.catch(() => null);
    invite = null;
    bInstall.hidden = true;
  });

  const racine = $('[data-push-root]');
  if (!racine) return;
  const etat = $('[data-push-etat]');
  const bOn = $('[data-push-on]'), bOff = $('[data-push-off]'), bTest = $('[data-push-test]');
  const dire = txt => { etat.hidden = !txt; etat.textContent = txt || ''; };

  const b64 = s => {
    const p = '='.repeat((4 - s.length % 4) % 4);
    const raw = atob((s + p).replace(/-/g, '+').replace(/_/g, '/'));
    return Uint8Array.from(raw, c => c.charCodeAt(0));
  };
  const post = (url, body) => fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
                                          body: JSON.stringify(body || {}) }).then(r => r.json());

  async function actuel() {
    const reg = await inscription;
    return reg ? reg.pushManager.getSubscription() : null;
  }

  async function afficher() {
    if (!pushOk) {
      dire(ios && !installee ? T.push_ios : T.push_non);
      [bOn, bOff, bTest].forEach(b => { b.hidden = true; });
      return;
    }
    const ab = await actuel();
    const refuse = Notification.permission === 'denied';
    bOn.hidden = !!ab || refuse;
    bOff.hidden = bTest.hidden = !ab;
    dire(refuse ? T.push_refus : (ab ? T.push_on : ''));
  }

  bOn.addEventListener('click', async () => {
    bOn.disabled = true;
    try {
      const cfg = await fetch('/push/cle').then(r => r.json());
      if (!cfg.disponible) { dire(T.push_indispo); return; }
      if (await Notification.requestPermission() !== 'granted') { await afficher(); return; }
      const reg = await inscription;
      const ab = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64(cfg.cle) });
      const r = await post('/push/abonnement', { abonnement: ab.toJSON() });
      if (!r.ok) { await ab.unsubscribe(); dire(r.erreur || T.push_erreur); return; }
      await afficher();
    } catch (e) {
      dire(T.push_erreur);
    } finally {
      bOn.disabled = false;
    }
  });

  bOff.addEventListener('click', async () => {
    const ab = await actuel();
    if (ab) {
      await post('/push/desabonnement', { endpoint: ab.endpoint }).catch(() => null);
      await ab.unsubscribe().catch(() => null);
    }
    await afficher();
    dire(T.push_off);
  });

  bTest.addEventListener('click', async () => {
    const r = await post('/push/test').catch(() => ({}));
    dire(r.ok ? T.push_test : T.push_test_ko);
  });

  if (installee) dire('');
  afficher();
})();
