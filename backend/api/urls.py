from django.urls import path
from .views import MeView, DepartmentListView, UserListView

urlpatterns = [
    path('me/',          MeView.as_view()),
    path('departments/', DepartmentListView.as_view()),
    path('users/',       UserListView.as_view()),
]
