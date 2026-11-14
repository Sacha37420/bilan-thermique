from django.urls import path
from .views_calc import BuildingCalcRunsView, CalcRunDetailView, CalculerView, ResultatView
from .views_pipeline import InitNewBuildingView, ReinitBuildingView
from . import views_parc as vp
from . import views_meteo as vm
from . import views_protections as vpr
from .views_geometrie import GeometryView
from . import views_mesures as vme
from .views_questionnaire import QuestionsView, QuestionnaireView
from .views_fiche import CatalogueView, FicheView, FactsView, FactDetailView
from .views import (
    MeView, Calcul1DView,
    ParoiModelListCreateView, ParoiModelDetailView,
    BuildingListCreateView, BuildingDetailView,
    EnvironmentListCreateView, EnvironmentDetailView, GenerateEnvironmentView,
    EnvironmentObjectsView, EnvironmentStudyObjectView, EnvironmentReplaceObjectView,
    EnvironmentOrthophotoView,
    JobDetailView, PrecomputeShadowsView, BuildingCalculView, BuildingRefineView,
    FetchWeatherView, SearchNearbyBuildingsView, GroundAltitudeView,
    FacadeAnalyseView, FacadeTextureView, FacadeApplyView, FacadeRecomposeView,
)

urlpatterns = [
    path('me/',               MeView.as_view()),
    path('calcul-1d/',        Calcul1DView.as_view()),
    path('paroi-modeles/',    ParoiModelListCreateView.as_view()),
    path('paroi-modeles/<int:pk>/', ParoiModelDetailView.as_view()),
    path('batiments/',        BuildingListCreateView.as_view()),
    path('batiments/rechercher/', SearchNearbyBuildingsView.as_view()),
    path('batiments/<int:pk>/', BuildingDetailView.as_view()),
    path('batiments/<int:pk>/precalcul-ombrage/', PrecomputeShadowsView.as_view()),
    path('batiments/<int:pk>/calcul-3d/', BuildingCalculView.as_view()),
    path('batiments/<int:pk>/affiner-maillage/', BuildingRefineView.as_view()),
    path('batiments/<int:pk>/facades/analyser/', FacadeAnalyseView.as_view()),
    path('batiments/<int:pk>/facades/appliquer/', FacadeApplyView.as_view()),
    path('batiments/<int:pk>/facades/recomposer/', FacadeRecomposeView.as_view()),
    path('batiments/<int:pk>/facades/<str:group>/texture/', FacadeTextureView.as_view()),
    path('environnements/',   EnvironmentListCreateView.as_view()),
    path('environnements/generer/', GenerateEnvironmentView.as_view()),
    path('environnements/<int:pk>/', EnvironmentDetailView.as_view()),
    path('environnements/<int:pk>/objets/', EnvironmentObjectsView.as_view()),
    path('environnements/<int:pk>/etudier/', EnvironmentStudyObjectView.as_view()),
    path('environnements/<int:pk>/orthophoto/', EnvironmentOrthophotoView.as_view()),
    path('environnements/<int:pk>/objets/<int:obj_id>/remplacer/', EnvironmentReplaceObjectView.as_view()),
    path('meteo/recuperer/',  FetchWeatherView.as_view()),
    path('altitude/',         GroundAltitudeView.as_view()),
    path('jobs/<int:pk>/',    JobDetailView.as_view()),
    # ── Refonte (Lot 3) : historique des calculs ──
    path('batiments/<int:pk>/calculs/', BuildingCalcRunsView.as_view()),
    path('calculs/<int:pk>/', CalcRunDetailView.as_view()),
    path('batiments/<int:pk>/calculer/', CalculerView.as_view()),
    path('batiments/<int:pk>/resultat/', ResultatView.as_view()),
    # ── Refonte (Lot 8) : fiche et provenance ──
    path('fiche/catalogue/', CatalogueView.as_view()),
    path('batiments/<int:pk>/fiche/', FicheView.as_view()),
    path('batiments/<int:pk>/faits/', FactsView.as_view()),
    path('faits/<int:pk>/', FactDetailView.as_view()),
    # ── Refonte (Lot 9) : fiche initiale automatique ──
    path('batiments/initialiser/', InitNewBuildingView.as_view()),
    path('batiments/<int:pk>/initialiser/', ReinitBuildingView.as_view()),
    path('batiments/<int:pk>/questions/', QuestionsView.as_view()),
    path('batiments/<int:pk>/questionnaire/', QuestionnaireView.as_view()),
    # ── Refonte (Lot 13) : météo ──
    path('meteo/jeux/', vm.DatasetsView.as_view()),
    path('meteo/stations/', vm.StationsView.as_view()),
    path('meteo/importer/', vm.ImportView.as_view()),
    path('batiments/<int:pk>/meteo/', vm.BuildingWeatherView.as_view()),
    path('batiments/<int:pk>/comparer-meteo/', vm.CompareWeatherView.as_view()),
    # ── Refonte (Lot 14) : protections solaires ──
    path('protections/catalogue/', vpr.CatalogueView.as_view()),
    path('batiments/<int:pk>/protections/', vpr.DevicesView.as_view()),
    path('batiments/<int:pk>/protections/effet/', vpr.EffectView.as_view()),
    path('protections/<int:pk>/', vpr.DeviceDetailView.as_view()),
    path('batiments/<int:pk>/geometrie/', GeometryView.as_view()),
    path('batiments/<int:pk>/mesures/', vme.MeasurementsView.as_view()),
    path('mesures/<int:pk>/', vme.MeasurementDetailView.as_view()),
    path('batiments/<int:pk>/sensibilite/', vme.SensitivityView.as_view()),
    path('batiments/<int:pk>/calage/', vme.CalibrateView.as_view()),
    path('batiments/<int:pk>/calage/appliquer/', vme.ApplyCalibrationView.as_view()),
    # ── Refonte (Lot 11) : parcs ──
    path('parcs/', vp.PortfoliosView.as_view()),
    path('parcs/<int:pk>/', vp.PortfolioDetailView.as_view()),
    path('parcs/<int:pk>/importer/', vp.ImportView.as_view()),
    path('parcs/<int:pk>/lancer/', vp.LaunchView.as_view()),
    path('parcs/<int:pk>/rattacher/', vp.AttachView.as_view()),
    path('parcs/<int:pk>/tableau/', vp.TableView.as_view()),
    path('parcs/<int:pk>/export.csv', vp.ExportView.as_view()),
    path('parcs/<int:pk>/membres/', vp.MembersView.as_view()),
    path('parcs/<int:pk>/membres/<int:mid>/', vp.MemberDetailView.as_view()),
    path('parcs/<int:pk>/invitations/', vp.InvitationsView.as_view()),
    path('invitations/accepter/', vp.AcceptInvitationView.as_view()),
]
