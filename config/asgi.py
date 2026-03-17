import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from django.urls import path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django_asgi_app = get_asgi_application()

from apps.ws.consumers import RunProgressConsumer
from apps.ws.browser_consumer import BrowserSessionConsumer

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(
        URLRouter([
            path("ws/runs/<uuid:run_id>/", RunProgressConsumer.as_asgi()),
            path("ws/browser/<uuid:run_id>/", BrowserSessionConsumer.as_asgi()),
        ])
    ),
})
