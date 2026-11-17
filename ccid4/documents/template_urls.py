from django.urls import path

from . import template_views as v

app_name = "doctemplates"
urlpatterns = [
    path("", v.index, name="index"),
    path("new/", v.add_custom, name="add_custom"),
    path("<str:key>/", v.manage, name="manage"),
    path("<str:key>/upload/", v.upload, name="upload"),
    path("<str:key>/builtin/", v.builtin, name="builtin"),
    path("<str:key>/download/", v.download, name="download_builtin"),
    path("<str:key>/download/<int:pk>/", v.download, name="download"),
    path("versions/<int:pk>/mapping/", v.mapping, name="mapping"),
    path("versions/<int:pk>/activate/", v.activate, name="activate"),
    path("versions/<int:pk>/try/", v.try_fill, name="try"),
]
