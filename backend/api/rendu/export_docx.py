"""Export Word (python-docx) d'un document résolu : styles de titre Word (sommaire actualisable, champ TOC), vrais
tableaux (ligne d'en-tête répétée), figures et vues 3D en images fournies par le service de rendu (I8), légendes
numérotées (champ SEQ), signets sur les sections et liens internes vers eux, numéros de page en pied.
"""

import io

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_BREAK
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Mm, Pt, RGBColor

from . import export_html, figures, service, texte, theme

TAILLES_MM = {'A4': (210, 297), 'A3': (297, 420)}


def _champ(paragraphe, instruction, provisoire=''):
    run = paragraphe.add_run()
    for kind in ('begin',):
        fc = OxmlElement('w:fldChar')
        fc.set(qn('w:fldCharType'), kind)
        fc.set(qn('w:dirty'), 'true')
        run._r.append(fc)
    it = OxmlElement('w:instrText')
    it.set(qn('xml:space'), 'preserve')
    it.text = instruction
    run._r.append(it)
    sep = OxmlElement('w:fldChar')
    sep.set(qn('w:fldCharType'), 'separate')
    run._r.append(sep)
    t = OxmlElement('w:t')
    t.set(qn('xml:space'), 'preserve')
    t.text = provisoire
    run._r.append(t)
    end = OxmlElement('w:fldChar')
    end.set(qn('w:fldCharType'), 'end')
    run._r.append(end)
    return run


def _signet(paragraphe, nom, ident):
    start = OxmlElement('w:bookmarkStart')
    start.set(qn('w:id'), str(ident))
    start.set(qn('w:name'), nom)
    end = OxmlElement('w:bookmarkEnd')
    end.set(qn('w:id'), str(ident))
    paragraphe._p.insert(1 if paragraphe._p.pPr is not None else 0, start)
    paragraphe._p.append(end)


def _lien_interne(paragraphe, ancre, texte_lien):
    h = OxmlElement('w:hyperlink')
    h.set(qn('w:anchor'), ancre)
    h.set(qn('w:history'), '1')
    r = OxmlElement('w:r')
    rpr = OxmlElement('w:rPr')
    st = OxmlElement('w:rStyle')
    st.set(qn('w:val'), 'Hyperlink')
    rpr.append(st)
    col = OxmlElement('w:color')
    col.set(qn('w:val'), '1C3266')
    rpr.append(col)
    u = OxmlElement('w:u')
    u.set(qn('w:val'), 'single')
    rpr.append(u)
    r.append(rpr)
    t = OxmlElement('w:t')
    t.set(qn('xml:space'), 'preserve')
    t.text = texte_lien
    r.append(t)
    h.append(r)
    paragraphe._p.append(h)


def _entete_repetee(row):
    trpr = row._tr.get_or_add_trPr()
    el = OxmlElement('w:tblHeader')
    el.set(qn('w:val'), 'true')
    trpr.append(el)


def _tableau(doc, entetes, lignes, numeriques=None):
    tab = doc.add_table(rows=1, cols=len(entetes))
    tab.style = 'Table Grid'
    for i, h in enumerate(entetes):
        cell = tab.rows[0].cells[i]
        cell.text = ''
        run = cell.paragraphs[0].add_run(str(h))
        run.bold = True
    _entete_repetee(tab.rows[0])
    for ligne in lignes:
        cells = tab.add_row().cells
        for i, v in enumerate(ligne):
            cells[i].text = str(v)
            if numeriques and numeriques[i]:
                cells[i].paragraphs[0].alignment = WD_ALIGN_PARAGRAPH.RIGHT
    for row in tab.rows:
        for cell in row.cells:
            for p in cell.paragraphs:
                for r in p.runs:
                    r.font.size = Pt(9)
    doc.add_paragraph()
    return tab


def _legende(doc, texte_legende):
    p = doc.add_paragraph(style='Caption')
    p.add_run('Figure ')
    _champ(p, ' SEQ Figure \\* ARABIC ', '#')
    p.add_run(f' — {texte_legende}')
    return p


def _texte(doc, contenu):
    for sorte, v in texte.blocs(contenu):
        if sorte == 'titre':
            doc.add_heading(v, level=3)
            continue
        items = v if sorte == 'liste' else [v]
        for it in items:
            p = doc.add_paragraph(style='List Bullet' if sorte == 'liste' else None)
            for seg, gras, ital in texte.segments(it):
                r = p.add_run(seg)
                r.bold, r.italic = gras, ital


def images_du_document(doc, largeur_px=1400, hauteur_px=800):
    """{id(bloc): octets PNG} pour chaque figure non vide et chaque vue 3D (service de rendu)."""
    figs, cles = [], []
    for s in doc['sections']:
        for b in s['blocs']:
            if b.get('indisponible'):
                continue
            if b['type'] in ('graphe', 'carte') and not b['figure'].get('vide'):
                f = theme.appliquer(b['figure'])
                f['layout']['width'], f['layout']['height'] = largeur_px, hauteur_px
                figs.append({'data': f['data'], 'layout': f['layout']})
                cles.append(id(b))
    out = dict(zip(cles, service.images(figs, largeur_px, hauteur_px, 2))) if figs else {}
    for s in doc['sections']:
        for b in s['blocs']:
            if b['type'] == 'vue3d' and not b.get('indisponible'):
                out[id(b)] = service.vue3d(b['scene'], b['grandeur'], b['camera'], 1400, 900)
    return out


def rendre(doc, sous_titre=''):
    images = images_du_document(doc)
    d = Document()
    mp = doc['mise_en_page']
    w, h = TAILLES_MM[mp['format']]
    sec = d.sections[0]
    if mp['orientation'] == 'paysage':
        w, h = h, w
        sec.orientation = WD_ORIENT.LANDSCAPE
    sec.page_width, sec.page_height = Mm(w), Mm(h)
    m = export_html.MARGES_MM
    sec.left_margin = sec.right_margin = sec.top_margin = sec.bottom_margin = Mm(m)
    largeur_utile = Mm(w - 2 * m)
    st = d.styles['Normal']
    st.font.name = 'Calibri'
    st.font.size = Pt(10.5)
    for lvl, size in ((1, 15), (2, 12.5), (3, 11)):
        hs = d.styles[f'Heading {lvl}']
        hs.font.color.rgb = RGBColor(0x1C, 0x32, 0x66)
        hs.font.size = Pt(size)
    if mp.get('numeros', True):
        fp = sec.footer.paragraphs[0]
        fp.alignment = WD_ALIGN_PARAGRAPH.CENTER
        fp.add_run('Page ')
        _champ(fp, ' PAGE ', '1')
        fp.add_run(' / ')
        _champ(fp, ' NUMPAGES ', '1')
    d.core_properties.title = doc['titre'][:200]
    d.core_properties.subject = 'Rendu bilan-thermique (simulation thermique, pas un DPE)'
    d.add_heading(doc['titre'], level=0)
    if sous_titre:
        d.add_paragraph(sous_titre)
    if mp.get('sommaire', True):
        d.add_paragraph('Sommaire', style='TOC Heading' if 'TOC Heading' in [s.name for s in d.styles] else None).runs[0].bold = True
        _champ(d.add_paragraph(), ' TOC \\o "1-3" \\h \\z \\u ',
               'Sommaire : clic droit puis « Mettre à jour les champs » (Word le propose à l’ouverture).')
        p = d.add_paragraph('Aller à : ')
        for i, s in enumerate(doc['sections']):
            if i:
                p.add_run(' · ')
            _lien_interne(p, s['ancre'], s['titre'])
        d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
    ident = 1
    for s in doc['sections']:
        hd = d.add_heading(s['titre'], level=1)
        _signet(hd, s['ancre'], ident)
        ident += 1
        for b in s['blocs']:
            t = b['type']
            if b.get('indisponible'):
                p = d.add_paragraph()
                r = p.add_run(b['indisponible'])
                r.italic = True
                continue
            if t == 'saut_de_page':
                d.add_paragraph().add_run().add_break(WD_BREAK.PAGE)
            elif t == 'texte':
                _texte(d, _contenu_texte(b))
            elif t in ('graphe', 'carte'):
                fig = b['figure']
                if fig.get('vide'):
                    d.add_paragraph(fig['layout']['meta']['resume']).runs[0].italic = True
                    continue
                d.add_picture(io.BytesIO(images[id(b)]), width=largeur_utile)
                import html as _h
                titre = ((fig.get('layout') or {}).get('title') or {}).get('text')
                _legende(d, _h.unescape(titre) if titre else ('Carte' if t == 'carte' else 'Graphe'))
                rp = d.add_paragraph().add_run(fig['layout']['meta'].get('resume') or '')
                rp.italic, rp.font.size = True, Pt(9)
            elif t == 'vue3d':
                d.add_picture(io.BytesIO(images[id(b)]), width=largeur_utile)
                _legende(d, f"{b['libelle']} ({b['unite']}) — calcul N2 n° {b['calcul_id']}")
            elif t == 'indicateurs':
                ent = ['Indicateur'] + [c['libelle'] for c in b['colonnes']]
                lignes = [[l['libelle'] + (f" ({l['unite']})" if l['unite'] else '')]
                          + [export_html._val_ecart(v, e, l['unite']) for v, e in zip(l['valeurs'], l['ecarts'])]
                          for l in b['lignes']]
                _tableau(d, ent, lignes, [False] + [True] * len(b['colonnes']))
            elif t == 'tableau':
                ent = [c['libelle'] + (f" ({c['unite']})" if c['unite'] else '') for c in b['colonnes']]
                cells = [[export_html._cellule(v) for v in row] for row in b['lignes']]
                num = [any(r[i]['n'] for r in cells) for i in range(len(ent))]
                _tableau(d, ent, [[c['t'] for c in r] for r in cells], num)
                if b['total_lignes'] > len(b['lignes']):
                    d.add_paragraph(f"{len(b['lignes'])} lignes affichées sur {b['total_lignes']}.")
            elif t == 'fiche':
                for fs in b['sections']:
                    d.add_heading(fs['titre'], level=2)
                    if not fs['elements']:
                        d.add_paragraph('Rien à afficher.')
                        continue
                    _tableau(d, ['Donnée', 'Valeur', 'Source'],
                             [[e['libelle'], f"{e['valeur']} {e['unite']}".strip(), e['source']] for e in fs['elements']])
    p = d.add_paragraph('Document produit par bilan-thermique. Simulation thermique, pas un DPE.')
    p.runs[0].font.size = Pt(8)
    # Word recalcule les champs (sommaire, numéros) à l'ouverture.
    upd = OxmlElement('w:updateFields')
    upd.set(qn('w:val'), 'true')
    d.settings.element.append(upd)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def _contenu_texte(b):
    """Le bloc résolu ne porte que le HTML assaini : on relit son texte source s'il est là, sinon le HTML dépouillé."""
    if '_contenu' in b:
        return b['_contenu']
    import re
    import html as _h
    s = re.sub(r'</(p|h4|li)>', '\n\n', b.get('html') or '')
    s = re.sub(r'<li>', '- ', s)
    s = re.sub(r'<strong>(.*?)</strong>', r'**\1**', s)
    s = re.sub(r'<em>(.*?)</em>', r'*\1*', s)
    s = re.sub(r'<h4>', '### ', s)
    s = re.sub(r'<[^>]+>', '', s)
    return _h.unescape(s)


__all__ = ['rendre', 'figures']
