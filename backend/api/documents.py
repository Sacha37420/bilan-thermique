"""Documents d'un bâtiment lus en faits proposés (Lot 21, refonte — § B8).

Chaîne : dépôt → lecture **locale** d'abord (XML DPE de l'ADEME analysé sans IA ; texte des PDF par
pypdf) → avertissement avant tout envoi (fournisseur, pages, jetons estimés) → extraction par l'outil
`proposer_faits` (boucle bornée côté serveur) → **contrôle automatique** (clé connue, plage
plausible, extrait retrouvé dans le texte source, cohérence) → **validation humaine** : une
proposition acceptée devient un fait de méthode `document`, avec la page et l'extrait cités.

L'IA tourne avec la clé de la personne qui demande, pour ses propres documents, jamais une autre ;
elle ne calcule rien, elle lit.
"""

import io
import re
import time
import unicodedata
import xml.etree.ElementTree as ET

from . import facts, questionnaire

MAX_BYTES = 15 * 1024 * 1024
MAX_PAGES = 60
MAX_PAGES_SENT = 20
MAX_CALLS = 3
LOOP_SECONDS = 90
MAX_PROPOSALS = 60


class DocumentError(ValueError):
    pass


# ── Lecture locale ──────────────────────────────────────────────────────────

def _local(tag):
    return tag.rsplit('}', 1)[-1].lower()


def _num(t):
    try:
        return float(str(t).strip().replace(',', '.'))
    except (TypeError, ValueError):
        return None


# Champs du format DPE 2021 de l'ADEME (observatoire-dpe) → clés de la fiche. Les U des parois sont
# moyennés en pondérant par la surface quand elle est donnée.
XML_SCALARS = {
    'annee_construction': 'identite.annee_construction', 'surface_habitable_logement': 'identite.surface_m2',
    'surface_habitable_immeuble': 'identite.surface_m2', 'nombre_niveau_logement': 'identite.niveaux',
    'nombre_appartement': 'identite.logements', 'classe_bilan_dpe': 'dpe.etiquette',
    'ep_conso_5_usages_m2': 'dpe.conso_ep_kwh_m2', 'conso_5_usages_ep_m2': 'dpe.conso_ep_kwh_m2',
}
XML_WEIGHTED = {  # (élément parent, valeur, surface)
    'mur.u': ('mur', 'umur', 'surface_paroi_opaque'),
    'toiture.u': ('plancher_haut', 'uph', 'surface_paroi_opaque'),
    'plancher.u': ('plancher_bas', 'upb_final', 'surface_paroi_opaque'),
    'baies.uw': ('baie_vitree', 'uw', 'surface_totale_baie'),
    'baies.g': ('baie_vitree', 'sw', 'surface_totale_baie'),
}


def parse_dpe_xml(data: bytes):
    """[(clé, valeur, extrait)] tirés d'un XML DPE. Lève DocumentError si ce n'est pas un XML lisible."""
    if b'<!DOCTYPE' in data[:2000] or b'<!ENTITY' in data[:5000]:
        raise DocumentError('XML refusé (déclaration de type ou d’entité).')
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise DocumentError('XML illisible.') from None
    out, seen = [], set()
    for el in root.iter():
        name = _local(el.tag)
        key = XML_SCALARS.get(name)
        if key and key not in seen and el.text and el.text.strip():
            v = el.text.strip()
            val = v if key == 'dpe.etiquette' else _num(v)
            if key in ('identite.annee_construction', 'identite.niveaux', 'identite.logements') and val is not None:
                val = int(round(val))
            if val is not None and val != '':
                out.append((key, val, f'<{name}>{v}</{name}>'))
                seen.add(key)
    for key, (parent, field, surf) in XML_WEIGHTED.items():
        tot = w = 0.0
        n = 0
        for el in root.iter():
            if _local(el.tag) != parent:
                continue
            vals = {_local(c.tag): c.text for c in el.iter() if c.text}
            u, s = _num(vals.get(field)), _num(vals.get(surf)) or 1.0
            if u is not None:
                tot += u * s
                w += s
                n += 1
        if n:
            out.append((key, round(tot / w, 3), f'{n} élément(s) <{parent}>, <{field}> moyen pondéré par <{surf}>'))
    return out


def pdf_pages(data: bytes):
    """Texte de chaque page (au plus MAX_PAGES). Une page scannée rend un texte vide."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise DocumentError('PDF protégé par mot de passe.')
        return [(p.extract_text() or '')[:20000] for p in reader.pages[:MAX_PAGES]]
    except DocumentError:
        raise
    except (PdfReadError, ValueError, KeyError, TypeError, OSError):
        raise DocumentError('PDF illisible.') from None


def read_upload(filename, data):
    """(kind, pages, faits_locaux) pour un fichier déposé."""
    if len(data) > MAX_BYTES:
        raise DocumentError('Fichier trop volumineux (15 Mo au plus).')
    name = filename.lower()
    if name.endswith('.xml') or data.lstrip()[:5] == b'<?xml':
        local = parse_dpe_xml(data)
        return 'xml_dpe', [data.decode('utf-8', 'replace')[:200000]], local
    if name.endswith('.pdf') or data[:5] == b'%PDF-':
        return 'pdf', pdf_pages(data), []
    if name.endswith('.txt'):
        return 'texte', [data.decode('utf-8', 'replace')[:200000]], []
    raise DocumentError('Formats acceptés : PDF, XML DPE, texte.')


# ── Contrôle d'une proposition ──────────────────────────────────────────────

def _norm(s):
    s = unicodedata.normalize('NFKD', str(s or '')).encode('ascii', 'ignore').decode().lower()
    return re.sub(r'\s+', ' ', s).strip()


def check(key, value, page, extract, pages, current_values):
    """(valeur normalisée ou None, contrôle, message)."""
    if key not in facts.BY_KEY:
        return None, 'cle_inconnue', 'Clé hors de la fiche.'
    try:
        v = facts.validate_value(key, value)
    except facts.FactError as exc:
        return None, 'hors_plage', str(exc)[:300]
    src = pages[page - 1] if isinstance(page, int) and 1 <= page <= len(pages) else ' '.join(pages)
    ex = _norm(extract)
    if len(ex) < 3 or ex not in _norm(src):
        return v, 'extrait_introuvable', 'L’extrait cité ne figure pas dans le document.'
    merged = {**current_values, key: v}
    issues = [i['message'] for i in questionnaire.inconsistencies(merged) if key in i['keys']]
    before = {i['message'] for i in questionnaire.inconsistencies(current_values)}
    new = [m for m in issues if m not in before]
    if new:
        return v, 'incoherent', new[0][:300]
    return v, 'ok', ''


ACCEPTABLE = ('ok', 'incoherent')


def make_proposals(run, building, rows, pages):
    """rows : [(clé, valeur, page, extrait)] → FactProposal contrôlées."""
    from .models import FactProposal
    current = facts.effective_values(building)
    out = []
    for key, value, page, extract in rows[:MAX_PROPOSALS]:
        v, chk, msg = check(key, value, page, extract, pages, current)
        spec = facts.BY_KEY.get(key)
        out.append(FactProposal.objects.create(
            run=run, building=building, key=str(key)[:80], value=v if v is not None else value, unit=spec.unit if spec else '',
            page=page if isinstance(page, int) and page > 0 else None, extract=str(extract or '')[:500], control=chk,
            control_message=msg, decision='en_attente' if chk in ACCEPTABLE else 'rejetee'))
    return out


# ── Envoi à l'IA ────────────────────────────────────────────────────────────

SYSTEM = ("Tu lis un document technique de bâtiment (DPE, audit, plan, CCTP, facture) pour en extraire des faits. "
          "Tu ne calcules rien : tu recopies ce que le document dit. Pour chaque fait trouvé, appelle l'outil "
          "proposer_faits avec la clé exacte de la liste, la valeur dans l'unité indiquée, le numéro de page et un "
          "extrait COPIÉ MOT POUR MOT du document (20 à 200 caractères) qui contient la valeur. Si le document ne "
          "dit rien d'une clé, ne la propose pas. N'invente jamais une valeur.")


def keys_prompt():
    lines = []
    for k in facts.CATALOGUE:
        if k.kind == 'enum':
            lines.append(f"- {k.key} : {k.label} — un de : {', '.join(k.choices)}")
        else:
            lines.append(f"- {k.key} : {k.label}{' (' + k.unit + ')' if k.unit else ''} — {k.kind}")
    return 'Clés de la fiche :\n' + '\n'.join(lines)


TOOL_SCHEMA = {
    'type': 'object',
    'properties': {'faits': {'type': 'array', 'maxItems': MAX_PROPOSALS, 'items': {
        'type': 'object',
        'properties': {'cle': {'type': 'string', 'enum': [k.key for k in facts.CATALOGUE]},
                       'valeur': {'type': ['number', 'string', 'boolean']},
                       'page': {'type': 'integer'}, 'extrait': {'type': 'string'}},
        'required': ['cle', 'valeur', 'page', 'extrait']}}},
    'required': ['faits'],
}
TOOL_DESC = 'Propose des faits extraits du document (chaque valeur avec sa page et un extrait cité mot pour mot).'


def pages_to_send(pages, selected=None):
    idx = [i for i in (selected or range(1, len(pages) + 1)) if 1 <= i <= len(pages) and pages[i - 1].strip()]
    return idx[:MAX_PAGES_SENT]


def document_text(pages, idx):
    return '\n\n'.join(f'=== page {i} ===\n{pages[i - 1]}' for i in idx)


def estimate(pages, idx):
    from .ia import registre
    return registre.estimer_jetons(SYSTEM + keys_prompt() + document_text(pages, idx)) + 2000


def run_extraction(provider, key, model_id, pages, idx, clock=time.monotonic):
    """Boucle bornée (MAX_CALLS appels, LOOP_SECONDS) avec l'outil `proposer_faits`.
    Rend ([(clé, valeur, page, extrait)], usage). Lève ia.fournisseurs.ErreurFournisseur (message fixe)."""
    from .ia import fournisseurs, registre
    fiche = registre.modele(provider, model_id)
    stable = SYSTEM + '\n\n' + keys_prompt()
    variable = 'Réponds en appelant proposer_faits ; termine par une phrase courte quand tu as fini.'
    messages = [{'role': 'user', 'content': 'Document :\n\n' + document_text(pages, idx)}]
    rows, usage = [], {'input_tokens': 0, 'output_tokens': 0}
    t0 = clock()
    client = fournisseurs.client_anthropic(key) if provider == 'claude' else None
    if provider == 'claude':
        tools = [{'name': 'proposer_faits', 'description': TOOL_DESC, 'input_schema': TOOL_SCHEMA}]
    else:
        tools = [{'type': 'function', 'function': {'name': 'proposer_faits', 'description': TOOL_DESC, 'parameters': TOOL_SCHEMA}}]
    for _ in range(MAX_CALLS):
        left = LOOP_SECONDS - (clock() - t0)
        if left <= 5:
            break
        if provider == 'claude':
            r = fournisseurs.appeler_anthropic(client, fiche, stable, variable, messages, tools, registre.sortie(fiche), left)
        else:
            r = fournisseurs.appeler_openai(provider, key, fiche, stable, variable, messages, tools, registre.sortie(fiche), left)
        usage['input_tokens'] += r.usage.get('input_tokens', 0)
        usage['output_tokens'] += r.usage.get('output_tokens', 0)
        if r.arret == 'refus':
            break
        results = []
        for a in r.appels:
            if a.nom != 'proposer_faits' or a.illisible or not isinstance(a.entree, dict):
                results.append((a.id, a.nom, 'Appel illisible : renvoie {"faits": [...]}.', True))
                continue
            got = 0
            for f in a.entree.get('faits') or []:
                if isinstance(f, dict):
                    rows.append((f.get('cle'), f.get('valeur'), f.get('page'), f.get('extrait')))
                    got += 1
            results.append((a.id, a.nom, f'{got} fait(s) reçu(s).', False))
        if not r.appels or r.arret != 'outils':
            break
        messages.append(r.message_assistant)
        if provider == 'claude':
            messages.append(fournisseurs.resultats_anthropic(results))
        else:
            messages += fournisseurs.resultats_mistral(results)
    return rows, usage
