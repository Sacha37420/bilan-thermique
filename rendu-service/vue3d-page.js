// Script de page (navigateur) : rendu three.js d'une scène I4 déjà validée par
// le serveur (validation.js → forme compacte). Injecté par addScriptTag({content}),
// après three (window.THREE). Expose window.renduVue3d(p) → data:image/png;base64.
//
// Repère : x est, y nord, z haut (m). Couleur par triangle, échelle séquentielle
// d'impression (bleu très clair → bleu-violet foncé, lisible en niveaux de gris :
// luminance strictement décroissante), valeur absente en gris clair, hors_volume
// en gris translucide. Légende dessinée sur l'image (barre, min/max, unité).
(function () {
  'use strict';

  // Arrêts de l'échelle (sRGB), luminance décroissante.
  var ARRETS = [
    [0.00, [239, 245, 252]],
    [0.25, [189, 215, 238]],
    [0.50, [107, 165, 214]],
    [0.75, [55, 92, 170]],
    [1.00, [42, 23, 94]],
  ];
  var GRIS_ABSENT = [215, 215, 215];
  var GRIS_HORS = [150, 150, 150];

  function couleurEchelle(t) {
    if (!(t >= 0)) t = 0;
    if (t > 1) t = 1;
    for (var i = 1; i < ARRETS.length; i += 1) {
      if (t <= ARRETS[i][0]) {
        var a = ARRETS[i - 1]; var b = ARRETS[i];
        var u = (t - a[0]) / (b[0] - a[0]);
        return [0, 1, 2].map(function (k) { return a[1][k] + u * (b[1][k] - a[1][k]); });
      }
    }
    return ARRETS[ARRETS.length - 1][1].slice();
  }

  var DIRECTIONS = {
    iso: [1, -1, 0.9],      // depuis le sud-est, en hauteur
    sud: [0, -1, 0.0001],
    nord: [0, 1, 0.0001],
    est: [1, 0, 0.0001],
    ouest: [-1, 0, 0.0001],
    dessus: [0, 0, 1],
  };

  function formater(v) {
    var a = Math.abs(v);
    var chiffres = a >= 100 ? 0 : a >= 10 ? 1 : a >= 1 ? 2 : 3;
    try {
      return new Intl.NumberFormat('fr-FR', { maximumFractionDigits: chiffres }).format(v);
    } catch (e) {
      return v.toFixed(chiffres);
    }
  }

  function dessinerLegende(ctx, l, h, min, max, unite, aucune) {
    var s = Math.max(1, Math.min(l, h) / 700);
    var barreL = Math.round(220 * s); var barreH = Math.round(14 * s);
    var pad = Math.round(10 * s);
    var police = Math.round(13 * s);
    var boiteL = barreL + 2 * pad;
    var boiteH = barreH + 2 * pad + police * 2 + Math.round(6 * s);
    var x0 = pad; var y0 = h - boiteH - pad;
    ctx.save();
    ctx.fillStyle = 'rgba(255,255,255,0.92)';
    ctx.strokeStyle = '#9a9a9a';
    ctx.lineWidth = 1;
    ctx.fillRect(x0, y0, boiteL, boiteH);
    ctx.strokeRect(x0 + 0.5, y0 + 0.5, boiteL - 1, boiteH - 1);
    ctx.font = police + 'px sans-serif';
    ctx.fillStyle = '#222';
    ctx.textBaseline = 'top';
    var yTexte = y0 + pad;
    if (unite) ctx.fillText(unite, x0 + pad, yTexte);
    var yBarre = yTexte + police + Math.round(4 * s);
    if (aucune) {
      ctx.fillStyle = 'rgb(' + GRIS_ABSENT.join(',') + ')';
      ctx.fillRect(x0 + pad, yBarre, barreL, barreH);
      ctx.fillStyle = '#222';
      ctx.fillText('aucune valeur', x0 + pad, yBarre + barreH + Math.round(2 * s));
    } else {
      for (var i = 0; i < barreL; i += 1) {
        var c = couleurEchelle(i / (barreL - 1));
        ctx.fillStyle = 'rgb(' + c.map(Math.round).join(',') + ')';
        ctx.fillRect(x0 + pad + i, yBarre, 1, barreH);
      }
      ctx.strokeStyle = '#555';
      ctx.strokeRect(x0 + pad + 0.5, yBarre + 0.5, barreL - 1, barreH - 1);
      ctx.fillStyle = '#222';
      var yv = yBarre + barreH + Math.round(2 * s);
      ctx.textAlign = 'left';
      ctx.fillText(formater(min), x0 + pad, yv);
      ctx.textAlign = 'right';
      ctx.fillText(formater(max), x0 + pad + barreL, yv);
    }
    ctx.restore();
  }

  window.renduVue3d = function (p) {
    var THREE = window.THREE;
    var l = p.largeur; var h = p.hauteur;
    var n = p.triangles.length / 3;

    var min = Infinity; var max = -Infinity;
    for (var i = 0; i < n; i += 1) {
      var v = p.valeurs[i];
      if (p.types[i] !== 'hors_volume' && typeof v === 'number' && isFinite(v)) {
        if (v < min) min = v;
        if (v > max) max = v;
      }
    }
    var aucune = !(min <= max);

    var opaques = { pos: [], col: [] }; var hors = { pos: [] };
    var P = p.positions;
    for (var t = 0; t < n; t += 1) {
      var cible = p.types[t] === 'hors_volume' ? hors : opaques;
      for (var k = 0; k < 3; k += 1) {
        var iv = p.triangles[3 * t + k];
        cible.pos.push(P[3 * iv], P[3 * iv + 1], P[3 * iv + 2]);
      }
      if (cible === opaques) {
        var val = p.valeurs[t];
        var c;
        if (typeof val !== 'number' || !isFinite(val) || aucune) c = GRIS_ABSENT;
        else c = couleurEchelle(max > min ? (val - min) / (max - min) : 0.5);
        for (var kk = 0; kk < 3; kk += 1) opaques.col.push(c[0] / 255, c[1] / 255, c[2] / 255);
      }
    }

    var canvas = document.createElement('canvas');
    canvas.width = l; canvas.height = h;
    document.body.appendChild(canvas);
    var renderer = new THREE.WebGLRenderer({
      canvas: canvas, antialias: true, preserveDrawingBuffer: true, alpha: false,
    });
    renderer.setPixelRatio(1);
    renderer.setSize(l, h, false);
    renderer.setClearColor(0xffffff, 1);
    // Couleurs de sommet données en sRGB : pas de gestion de couleur three.
    THREE.ColorManagement.enabled = false;
    renderer.outputColorSpace = THREE.LinearSRGBColorSpace;

    var scene = new THREE.Scene();
    scene.background = new THREE.Color(0xffffff);
    // three ≥ r155 : la diffusion de Lambert vaut couleur × éclairement / π.
    // Ambiante + directionnelle = π sur une face éclairée de face : la couleur de
    // l'échelle y est rendue exactement ; une face à contre-jour perd au plus 15 %
    // (le relief reste lisible, la correspondance avec la légende aussi).
    scene.add(new THREE.AmbientLight(0xffffff, 0.85 * Math.PI));
    var soleil = new THREE.DirectionalLight(0xffffff, 0.15 * Math.PI);
    scene.add(soleil);

    var boite = new THREE.Box3();
    function maillage(groupe, materiau) {
      var g = new THREE.BufferGeometry();
      g.setAttribute('position', new THREE.Float32BufferAttribute(groupe.pos, 3));
      if (groupe.col) g.setAttribute('color', new THREE.Float32BufferAttribute(groupe.col, 3));
      g.computeVertexNormals();
      g.computeBoundingBox();
      boite.union(g.boundingBox);
      var m = new THREE.Mesh(g, materiau);
      scene.add(m);
      return g;
    }
    if (opaques.pos.length) {
      var gOpaque = maillage(opaques, new THREE.MeshLambertMaterial({
        vertexColors: true, side: THREE.DoubleSide,
        polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1,
      }));
      var aretes = new THREE.LineSegments(
        new THREE.EdgesGeometry(gOpaque, 25),
        new THREE.LineBasicMaterial({ color: 0x3a3a3a, transparent: true, opacity: 0.55 }),
      );
      scene.add(aretes);
    }
    if (hors.pos.length) {
      maillage(hors, new THREE.MeshLambertMaterial({
        color: new THREE.Color('rgb(' + GRIS_HORS.join(',') + ')'),
        transparent: true, opacity: 0.25, depthWrite: false, side: THREE.DoubleSide,
      }));
    }

    var centre = boite.getCenter(new THREE.Vector3());
    var rayon = Math.max(boite.getSize(new THREE.Vector3()).length() / 2, 1e-3);
    var d = DIRECTIONS[p.camera] || DIRECTIONS.iso;
    var dir = new THREE.Vector3(d[0], d[1], d[2]).normalize();
    var aspect = l / h;
    var cam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0.01, rayon * 20);
    cam.up.set(0, 0, 1);
    if (p.camera === 'dessus') cam.up.set(0, 1, 0);   // nord en haut
    cam.position.copy(centre).addScaledVector(dir, rayon * 6);
    cam.lookAt(centre);
    cam.updateMatrixWorld(true);
    // Cadrage serré : étendue réelle de la boîte englobante vue de cette caméra,
    // plus une bande libre en bas pour la légende.
    var wx = 1e-3; var wy = 1e-3; var coin = new THREE.Vector3();
    for (var c8 = 0; c8 < 8; c8 += 1) {
      coin.set(c8 & 1 ? boite.max.x : boite.min.x, c8 & 2 ? boite.max.y : boite.min.y,
        c8 & 4 ? boite.max.z : boite.min.z).applyMatrix4(cam.matrixWorldInverse);
      wx = Math.max(wx, Math.abs(coin.x)); wy = Math.max(wy, Math.abs(coin.y));
    }
    var marge = 1.1;
    var fLegende = Math.min(0.3, (Math.round(74 * Math.max(1, Math.min(l, h) / 700)) + 10) / h);
    var haut = wy * marge;
    var hauteurTot = (2 * wy * marge) / (1 - fLegende);
    var demiL = wx * marge;
    if (2 * demiL / hauteurTot > aspect) {
      var k = (2 * demiL / aspect) / hauteurTot;   // trop large : on agrandit en hauteur
      haut *= k; hauteurTot *= k;
    } else {
      demiL = (hauteurTot * aspect) / 2;
    }
    cam.left = -demiL; cam.right = demiL; cam.top = haut; cam.bottom = haut - hauteurTot;
    cam.updateProjectionMatrix();
    // Lumière venant de l'arrière-gauche de la caméra, en hauteur.
    soleil.position.copy(cam.position).add(new THREE.Vector3(-rayon * 2, rayon, rayon * 4));
    soleil.target.position.copy(centre);
    scene.add(soleil.target);

    renderer.render(scene, cam);

    var sortie = document.createElement('canvas');
    sortie.width = l; sortie.height = h;
    var ctx = sortie.getContext('2d');
    ctx.fillStyle = '#fff';
    ctx.fillRect(0, 0, l, h);
    ctx.drawImage(canvas, 0, 0);
    dessinerLegende(ctx, l, h, min, max, p.unite, aucune);
    var url = sortie.toDataURL('image/png');
    renderer.dispose();
    return url;
  };
}());
