"""A model that cannot see images (a Locutus node started without its vision encoder) gets no pictures: a playtest
screenshot in the history made every request fail with HTTP 400 and ended the task."""
from sinobee.agent import Agent, Runtime
from sinobee.images import NO_VISION_MODELS
from sinobee.providers.base import ProviderError
from sinobee.types import ChatResponse, ImagePart, Message, TextPart, Usage


class Blind:
    label = "sinobee/locutus/qwen3.8-flash-next-iq3_s"
    provider_name = "sinobee"
    model = "locutus/qwen3.8-flash-next-iq3_s"

    def __init__(self):
        self.calls, self.recorder = [], None

    async def chat(self, messages, tools=None, **kw):
        pics = sum(isinstance(p, ImagePart) for m in messages if isinstance(m.content, list) for p in m.content)
        self.calls.append(pics)
        if pics:
            raise ProviderError(f"{self.label} HTTP 400: this server was started without the vision encoder (run setup "
                                "again and choose 'vision'), so it cannot read images", 400)
        return ChatResponse(Message("assistant", "完成"), Usage(10, 5), "stop")

    async def embed(self, texts):
        raise RuntimeError("no embeddings")


async def test_a_model_that_cannot_see_gets_the_text_and_the_task_goes_on(config):
    NO_VISION_MODELS.clear()
    model = Blind()
    agent = Agent(Runtime(config, provider=model))
    agent.history = [Message("user", [TextPart("看這張截圖"), ImagePart("aGk=", "image/png")])]
    out = await agent.run("圖表有沒有錯位？")
    assert out == "完成" and model.calls[0] >= 1 and model.calls[-1] == 0
    assert model.label in NO_VISION_MODELS
    assert "cannot see images" in agent.history[0].content[1].text
    NO_VISION_MODELS.clear()
