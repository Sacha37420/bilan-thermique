from django.urls import path
from .views import (
    MeView, DepartmentListView, UserListView, Calcul1DView,
    ParoiModelListCreateView, ParoiModelDetailView,
)

urlpatterns = [
    path('me/',               MeView.as_view()),
    path('departments/',      DepartmentListView.as_view()),
    path('users/',            UserListView.as_view()),
    path('calcul-1d/',        Calcul1DView.as_view()),
    path('paroi-modeles/',    ParoiModelListCreateView.as_view()),
    path('paroi-modeles/<int:pk>/', ParoiModelDetailView.as_view()),
]
