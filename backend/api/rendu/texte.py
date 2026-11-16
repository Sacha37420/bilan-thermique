"""Markdown restreint du bloc « texte » → HTML assaini (I6.g : `{"html": …}`), et le même texte découpé pour Word.

Permis : paragraphes (ligne vide), titres « ### », listes « - » ou « * », **gras**, *italique*, `code`. Rien
d'autre : tout le texte est **échappé d'abord**, les balises sont posées ensuite par le serveur — aucune balise,
aucun attribut, aucun lien ne peut venir de l'auteur.
"""

import html
import re

_GRAS = re.compile(r'\*\*(.+?)\*\*')
_ITAL = re.compile(r'(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])')
_CODE = re.compile(r'`([^`]+)`')


def _inline(s):
    s = html.escape(s, quote=True)
    s = _CODE.sub(r'<code>\1</code>', s)
    s = _GRAS.sub(r'<strong>\1</strong>', s)
    return _ITAL.sub(r'<em>\1</em>', s)


def blocs(contenu):
    """[(sorte, texte | [items])] — sorte : titre | paragraphe | liste. Texte brut (non échappé)."""
    out, para, liste = [], [], []

    def fin():
        if para:
            out.append(('paragraphe', ' '.join(para)))
            para.clear()
        if liste:
            out.append(('liste', list(liste)))
            liste.clear()

    for raw in (contenu or '').replace('\r\n', '\n').split('\n'):
        line = ''.join(c for c in raw if c.isprintable() or c == '\t').strip()
        if not line:
            fin()
            continue
        if line.startswith('#'):
            fin()
            out.append(('titre', line.lstrip('#').strip()))
        elif line[:2] in ('- ', '* '):
            if para:
                out.append(('paragraphe', ' '.join(para)))
                para.clear()
            liste.append(line[2:].strip())
        else:
            if liste:
                out.append(('liste', list(liste)))
                liste.clear()
            para.append(line)
    fin()
    return out


def vers_html(contenu):
    parts = []
    for sorte, v in blocs(contenu):
        if sorte == 'titre':
            parts.append(f'<h4>{_inline(v)}</h4>')
        elif sorte == 'liste':
            parts.append('<ul>' + ''.join(f'<li>{_inline(i)}</li>' for i in v) + '</ul>')
        else:
            parts.append(f'<p>{_inline(v)}</p>')
    return '\n'.join(parts)


def segments(ligne):
    """[(texte, gras, italique)] d'une ligne (pour Word)."""
    out = []
    pos = 0
    pat = re.compile(r'\*\*(.+?)\*\*|(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])|`([^`]+)`')
    for m in pat.finditer(ligne):
        if m.start() > pos:
            out.append((ligne[pos:m.start()], False, False))
        if m.group(1) is not None:
            out.append((m.group(1), True, False))
        elif m.group(2) is not None:
            out.append((m.group(2), False, True))
        else:
            out.append((m.group(3), False, False))
        pos = m.end()
    if pos < len(ligne):
        out.append((ligne[pos:], False, False))
    return out
