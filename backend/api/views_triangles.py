"""Secteur P (I4) : résultats N2 par triangle — `GET /api/calculs/<id>/triangles/`.

Chantier bt-potentiels — voir chantiers/bt-potentiels/CONTRAT.md, I4. Lecture : qui voit le bâtiment. Rend
`CalcRun.results['par_triangle']` (enregistré par `calc_service.run_n2` et par la page Calcul 3D, géométrie réellement
calculée) ; 404 fixe pour un calcul qui n'est pas un N2 terminé avec ces résultats (N2 d'avant le lot 36 compris).
"""

from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .access import visible_buildings
from .models import CalcRun, Job

ABSENT = {'detail': 'Pas de résultats par triangle pour ce calcul.'}


class TrianglesView(APIView):
    def get(self, request, pk):
        run = (CalcRun.objects.filter(pk=pk, building__in=visible_buildings(request), level=CalcRun.N2, status=Job.DONE)
               .only('results').first())
        data = (run.results or {}).get('par_triangle') if run is not None else None
        if not data:
            return Response(ABSENT, status=status.HTTP_404_NOT_FOUND)
        return Response(data)
