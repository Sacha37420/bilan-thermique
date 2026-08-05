from django.urls import path
from .views import (
    MeView, DepartmentListView, UserListView, Calcul1DView,
    ParoiModelListCreateView, ParoiModelDetailView,
    BuildingListCreateView, BuildingDetailView,
    EnvironmentListCreateView, EnvironmentDetailView,
    JobDetailView, PrecomputeShadowsView, BuildingCalculView,
)

urlpatterns = [
    path('me/',               MeView.as_view()),
    path('departments/',      DepartmentListView.as_view()),
    path('users/',            UserListView.as_view()),
    path('calcul-1d/',        Calcul1DView.as_view()),
    path('paroi-modeles/',    ParoiModelListCreateView.as_view()),
    path('paroi-modeles/<int:pk>/', ParoiModelDetailView.as_view()),
    path('batiments/',        BuildingListCreateView.as_view()),
    path('batiments/<int:pk>/', BuildingDetailView.as_view()),
    path('batiments/<int:pk>/precalcul-ombrage/', PrecomputeShadowsView.as_view()),
    path('batiments/<int:pk>/calcul-3d/', BuildingCalculView.as_view()),
    path('environnements/',   EnvironmentListCreateView.as_view()),
    path('environnements/<int:pk>/', EnvironmentDetailView.as_view()),
    path('jobs/<int:pk>/',    JobDetailView.as_view()),
]
