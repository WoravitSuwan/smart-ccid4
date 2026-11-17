from django.urls import path

from . import public_views as v

app_name = "intake_public"
urlpatterns = [
    path("r/<str:token>/", v.start, name="start"),
    path("r/<str:token>/<str:kind>/chat/", v.chat, name="chat"),
    path("r/<str:token>/<str:kind>/chat/send/", v.chat_send, name="chat_send"),
    path("r/<str:token>/<str:kind>/chat/reset/", v.chat_reset, name="chat_reset"),
    path("r/<str:token>/<str:kind>/chat/submit/", v.chat_submit, name="chat_submit"),
    path("r/<str:token>/<str:kind>/form/", v.step_form, name="form"),
    path("sent/<str:track>/", v.done, name="done"),
    path("t/<str:track>/", v.track, name="track"),
]
