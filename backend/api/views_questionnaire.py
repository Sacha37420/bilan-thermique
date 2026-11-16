"""Questionnaire guidé (Lot 12, refonte). Routes nouvelles uniquement.

GET  /api/batiments/<id>/questions/        questions classées (influence × incertitude) + incohérences
POST /api/batiments/<id>/questionnaire/    {answers: {id: valeur}} → faits déclarés, N1 relancé, avant/après
"""

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import access, calc_service, facts, questionnaire, reglages
from .views_calc import compact
from .views_fiche import fiche_payload


def _weather(building):
    try:
        return calc_service.resolve_weather(building)[0]
    except calc_service.CalcError:
        return None


class QuestionsView(APIView):
    def get(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        return Response(questionnaire.ranked_questions(b, _weather(b)))


class QuestionnaireView(APIView):
    def post(self, request, pk):
        b = get_object_or_404(access.visible_buildings(request), pk=pk)
        if not access.can_declare(request, b):
            return Response({'detail': 'Lecture seule sur ce bâtiment.'}, status=status.HTTP_403_FORBIDDEN)
        answers = request.data.get('answers')
        if not isinstance(answers, dict) or not answers or len(answers) > 40:
            return Response({'detail': 'Réponses attendues (answers).'}, status=status.HTTP_400_BAD_REQUEST)
        before = calc_service.current_or_latest(b, 'N1')
        conf_before = b.confidence
        try:
            cfg = reglages.for_building(b, 'parametrage') or None   # réglages du parc ; None hors parc
            rows = questionnaire.answers_to_facts(answers, facts.effective_values(b), cfg)
            for key, value, src in rows:
                facts.record(b, key, value, 'declare', source=src, author_sub=access.sub_of(request),
                             author_name=access.name_of(request), refresh=False)
        except (questionnaire.QuestionError, facts.FactError) as exc:
            return Response({'detail': str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        if {'annee', 'usage'} & set(answers):
            questionnaire.refresh_archetype(
                b, facts.effective_values(b),
                lambda k, v, src: facts.record(b, k, v, 'defaut', source=src, confidence=0.15, refresh=False), cfg)
        facts.refresh_scores(b)
        after, calc_error = None, None
        try:
            after, _ = calc_service.run_level(b, 'N1', owner_sub=access.sub_of(request))
            calc_service.run_level(b, 'N0', owner_sub=access.sub_of(request))
        except calc_service.CalcError as exc:
            calc_error = str(exc)
        return Response({
            'recorded': [{'key': k, 'value': v} for k, v, _ in rows],
            'confidence_before': conf_before, 'confidence_after': b.confidence,
            'before': compact(before) if before else None, 'after': compact(after) if after else None,
            'calc_error': calc_error, 'fiche': fiche_payload(b, access.sub_of(request)),
            'inconsistencies': questionnaire.inconsistencies(facts.effective_values(b)),
        })
