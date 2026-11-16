from django.urls import path

from . import views

app_name = "ai"
urlpatterns = [
    path("<int:pk>/ai/charges/", views.charges, name="charges"),
    path("<int:pk>/ai/summary/", views.summary, name="summary"),
    path("<int:pk>/ai/parties/<int:party_id>/draft/", views.draft, name="draft"),
    path("<int:pk>/ai/parties/<int:party_id>/rights/", views.rights, name="rights"),
    path("<int:pk>/ai/parties/<int:party_id>/consistency/", views.consistency, name="consistency"),
    path("<int:pk>/ai/statements/<int:statement_id>/confirm/", views.confirm, name="confirm"),
]
