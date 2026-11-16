/* Rendu des figures Plotly et des vues 3D d'un document exporté (lot 38). Aucune requête réseau : les données
   sont dans le document (<script type="application/json">), plotly.js et three.js aussi (export HTML) ou injectés
   par le service de rendu (PDF). Contrat avec le service : window.renduDemarrer() → promesse, puis
   window.renduPret = true. */
(function () {
  'use strict';
  var SEQ = __ECHELLE__;
  function lire(id) {
    var el = document.getElementById(id);
    return el ? JSON.parse(el.textContent) : null;
  }
  function hex(c) { return [parseInt(c.slice(1, 3), 16) / 255, parseInt(c.slice(3, 5), 16) / 255, parseInt(c.slice(5, 7), 16) / 255]; }
  function couleur(t) {
    if (!(t >= 0)) t = 0;
    if (t > 1) t = 1;
    for (var i = 1; i < SEQ.length; i++) {
      if (t <= SEQ[i][0]) {
        var a = SEQ[i - 1], b = SEQ[i], u = (t - a[0]) / ((b[0] - a[0]) || 1), ca = hex(a[1]), cb = hex(b[1]);
        return [ca[0] + u * (cb[0] - ca[0]), ca[1] + u * (cb[1] - ca[1]), ca[2] + u * (cb[2] - ca[2])];
      }
    }
    return hex(SEQ[SEQ.length - 1][1]);
  }
  function nombre(v) {
    if (v === null || v === undefined || !isFinite(v)) return '—';
    var a = Math.abs(v), d = a >= 100 ? 0 : a >= 10 ? 1 : 2;
    return v.toLocaleString('fr-FR', { maximumFractionDigits: d });
  }
  var DIRECTIONS = { iso: [1, -1, 0.8], sud: [0, -1, 0.25], nord: [0, 1, 0.25], est: [1, 0, 0.25], ouest: [-1, 0, 0.25], dessus: [0.001, -0.001, 1] };

  function vue3d(el, d) {
    if (!d || !window.THREE) return;
    var T = window.THREE, sc = d.scene, vals = (sc.grandeurs || {})[d.grandeur] || [], types = sc.type || [];
    var V = sc.vertices || [], F = sc.triangles || [];
    var finies = [];
    for (var i = 0; i < F.length; i++) { if (types[i] !== 'hors_volume' && vals[i] !== null && isFinite(vals[i])) finies.push(vals[i]); }
    var vmin = finies.length ? Math.min.apply(null, finies) : 0, vmax = finies.length ? Math.max.apply(null, finies) : 1;
    var pos = new Float32Array(F.length * 9), col = new Float32Array(F.length * 9), posH = [];
    var lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
    for (var k = 0; k < F.length; k++) {
      var c = types[k] === 'hors_volume' || vals[k] === null || !isFinite(vals[k]) ? [0.82, 0.82, 0.82]
        : couleur((vals[k] - vmin) / ((vmax - vmin) || 1));
      for (var j = 0; j < 3; j++) {
        var p = V[F[k][j]] || [0, 0, 0];
        for (var a = 0; a < 3; a++) { lo[a] = Math.min(lo[a], p[a]); hi[a] = Math.max(hi[a], p[a]); }
        if (types[k] === 'hors_volume') { posH.push(p[0], p[1], p[2]); continue; }
        pos.set(p, k * 9 + j * 3); col.set(c, k * 9 + j * 3);
      }
    }
    var renderer = new T.WebGLRenderer({ antialias: true, preserveDrawingBuffer: true });
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
    renderer.setClearColor(0xffffff, 1);
    el.insertBefore(renderer.domElement, el.firstChild);
    var scene = new T.Scene();
    var g = new T.BufferGeometry();
    g.setAttribute('position', new T.BufferAttribute(pos, 3));
    g.setAttribute('color', new T.BufferAttribute(col, 3));
    g.computeVertexNormals();
    scene.add(new T.Mesh(g, new T.MeshLambertMaterial({ vertexColors: true, side: T.DoubleSide })));
    if (posH.length) {
      var gh = new T.BufferGeometry();
      gh.setAttribute('position', new T.BufferAttribute(new Float32Array(posH), 3));
      gh.computeVertexNormals();
      scene.add(new T.Mesh(gh, new T.MeshLambertMaterial({ color: 0xbbbbbb, transparent: true, opacity: 0.35, side: T.DoubleSide })));
    }
    scene.add(new T.AmbientLight(0xffffff, 2.2));
    var sun = new T.DirectionalLight(0xffffff, 1.4); sun.position.set(0.4, -1, 1.2); scene.add(sun);
    var centre = new T.Vector3((lo[0] + hi[0]) / 2, (lo[1] + hi[1]) / 2, (lo[2] + hi[2]) / 2);
    var rayon = Math.max(hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2], 1);
    var cam = new T.PerspectiveCamera(35, 1, rayon / 100, rayon * 50);
    cam.up.set(0, 0, 1);
    var dir = new T.Vector3().fromArray(DIRECTIONS[d.camera] || DIRECTIONS.iso).normalize();
    var az = Math.atan2(dir.y, dir.x), el0 = Math.asin(Math.max(-1, Math.min(1, dir.z))), dist = rayon * 2.4;
    function placer() {
      var ce = Math.cos(el0);
      cam.position.set(centre.x + dist * ce * Math.cos(az), centre.y + dist * ce * Math.sin(az), centre.z + dist * Math.sin(el0));
      cam.lookAt(centre);
      var w = el.clientWidth || 600, h = el.clientHeight || 400;
      renderer.setSize(w, h, false); cam.aspect = w / h; cam.updateProjectionMatrix();
      renderer.render(scene, cam);
    }
    placer();
    var bornes = el.querySelector('[data-min]'), bornesM = el.querySelector('[data-max]');
    if (bornes) bornes.textContent = nombre(vmin);
    if (bornesM) bornesM.textContent = nombre(vmax);
    var tirer = null;
    el.addEventListener('pointerdown', function (e) { tirer = [e.clientX, e.clientY]; el.setPointerCapture(e.pointerId); });
    el.addEventListener('pointerup', function () { tirer = null; });
    el.addEventListener('pointermove', function (e) {
      if (!tirer) return;
      az -= (e.clientX - tirer[0]) * 0.01;
      el0 = Math.min(1.55, Math.max(-0.3, el0 + (e.clientY - tirer[1]) * 0.01));
      tirer = [e.clientX, e.clientY]; placer();
    });
    el.addEventListener('wheel', function (e) { e.preventDefault(); dist = Math.min(rayon * 10, Math.max(rayon * 0.6, dist * (e.deltaY > 0 ? 1.1 : 0.9))); placer(); }, { passive: false });
    window.addEventListener('resize', placer);
  }

  window.renduDemarrer = function () {
    var attentes = [];
    var figs = document.querySelectorAll('[data-rendu-figure]');
    for (var i = 0; i < figs.length; i++) {
      var f = lire(figs[i].getAttribute('data-rendu-figure'));
      if (!f || !window.Plotly) continue;
      attentes.push(window.Plotly.newPlot(figs[i], f.data, f.layout, { displaylogo: false, responsive: true }));
    }
    var vues = document.querySelectorAll('[data-rendu-3d]');
    for (var j = 0; j < vues.length; j++) vue3d(vues[j], lire(vues[j].getAttribute('data-rendu-3d')));
    return Promise.all(attentes).then(function () { window.renduPret = true; });
  };
  if (window.Plotly) window.renduDemarrer();
})();
