"""Run the real model adapter while replacing only its external HTTP provider."""

import json

import httpx

from companion.model import ModelClient


class NativeTestModel(ModelClient):
    def __init__(self, settings, stub, scenario):
        self.stub = stub

        async def respond(request):
            payload = json.loads(request.content)
            result = await stub.complete(payload["messages"], scenario)
            return httpx.Response(200, json={
                "choices": [{"message": {"content": result.text}}],
                "usage": {"prompt_tokens": result.input_tokens,
                          "completion_tokens": result.output_tokens},
            })

        super().__init__(settings, httpx.AsyncClient(
            transport=httpx.MockTransport(respond), trust_env=False,
        ))

    def __getattr__(self, name):
        return getattr(self.stub, name)

    def __setattr__(self, name, value):
        if name in {"callback", "error", "activity", "proposal", "text"}:
            setattr(self.stub, name, value)
        else:
            super().__setattr__(name, value)
