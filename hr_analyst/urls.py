from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path, include

from jobs.views import remember_me_login

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", remember_me_login, name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("", include("jobs.urls")),
]
