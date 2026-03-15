import json
from channels.generic.websocket import AsyncJsonWebsocketConsumer


class RunProgressConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        self.run_id = str(self.scope['url_route']['kwargs']['run_id'])
        self.group_name = f'run_{self.run_id}'

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        # Client messages are not expected, but handle gracefully
        pass

    async def run_progress(self, event):
        """Handle run.progress messages from the channel layer."""
        await self.send_json({
            'type': 'progress',
            'data': event.get('data', {}),
        })

    async def run_completed(self, event):
        """Handle run.completed messages from the channel layer."""
        await self.send_json({
            'type': 'completed',
            'data': event.get('data', {}),
        })

    async def run_failed(self, event):
        """Handle run.failed messages from the channel layer."""
        await self.send_json({
            'type': 'failed',
            'data': event.get('data', {}),
        })
