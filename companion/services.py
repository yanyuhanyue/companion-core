"""Explicit, lazy composition on a caller-owned connection.

An instance belongs to one synchronous operation or one worker. Construction does
not bootstrap identity, replay erasures, clean spools, or warm semantic models.
The owner closes the Store after all work using this graph has finished.
"""

from functools import cached_property

from companion.privacy import ErasureLedger
from companion.store import utcnow


class Services:
    def __init__(self, store, settings, model=None, clock=utcnow, *, ledger=None,
                 extract_step=None):
        self.store, self.settings, self.model, self.clock = store, settings, model, clock
        self.ledger = ledger if ledger is not None else ErasureLedger(settings.erasure_ledger)
        self._extract_step = extract_step

    @cached_property
    def memory(self):
        from companion.memory import Memory

        return Memory(self.store, self.settings, self.ledger)

    @cached_property
    def memory_context(self):
        from companion.memory_context import MemoryContext

        return MemoryContext(self.memory)

    @cached_property
    def diary(self):
        from companion.diary import DiaryService

        return DiaryService(self.store, self.settings, self.model, self.ledger, self.clock)

    @cached_property
    def observations(self):
        from companion.observation import ObservationService

        return ObservationService(self.store, self.settings, self.clock)

    @cached_property
    def media(self):
        from companion.media import MediaService

        return MediaService(self.store, self.settings, self.clock, self.observations)

    @cached_property
    def affect(self):
        from companion.affect import AffectEngine

        return AffectEngine(self.store, self.settings, self.clock)

    @cached_property
    def expression_learning(self):
        from companion.growth import ExpressionLearning

        return ExpressionLearning(self.store, self.settings, self.clock)

    @cached_property
    def reflections(self):
        from companion.reflection import ReflectionService

        return ReflectionService(self.store, self.settings, self.model, self.diary.repo,
                                 self.ledger, self.clock)

    @cached_property
    def goals(self):
        from companion.goals import GoalEngine

        return GoalEngine(self.store, self.settings, self.ledger, self.clock)

    @cached_property
    def consolidation(self):
        from companion.consolidation import NightlyConsolidator

        return NightlyConsolidator(self.store, self.settings, self.diary, self.reflections,
                                  self.goals, self._extract_step, self.clock)

    @cached_property
    def wake(self):
        from companion.wake import WakePlanner

        return WakePlanner(self.store, self.settings, self.goals, self.affect, self.clock)

    @cached_property
    def life(self):
        from companion.life import LifeService

        return LifeService(self.store, self.settings, self.goals, self.reflections,
                           self.affect, self.clock)

    @cached_property
    def social(self):
        from companion.social import SocialPlanner

        return SocialPlanner(self.store, self.settings, self.clock)

    @cached_property
    def life_topics(self):
        from companion.life_topics import LifeTopics

        return LifeTopics(self.store, self.settings, self.clock)

    @cached_property
    def exploration(self):
        from companion.content import register_link_images
        from companion.exploration import ExplorationService

        return ExplorationService(self.store, self.settings, self.observations, self.goals,
            self.social, lambda owner, event, urls: register_link_images(
                self.store, self.settings, self.clock, self.settings.content, owner, event, urls,
            ), self.clock)

    @cached_property
    def reading(self):
        from companion.bili_video import BiliVideoService
        from companion.reading import ReadingService

        reading = ReadingService(self.store, self.settings, self.exploration, self.clock)
        reading.video = BiliVideoService(self.store, self.settings, reading, self.model, self.clock)
        return reading

    @property
    def bili_video(self):
        return self.reading.video

    @cached_property
    def content(self):
        from companion.content import ContentService

        content = ContentService(self.store, self.settings, self.observations,
            lambda *args, **kwargs: self.exploration.create(*args, **kwargs), self.clock,
            model=lambda: self.model)
        content.video = self.bili_video
        return content

    @cached_property
    def expression(self):
        from companion.expression import ExpressionEngine

        return ExpressionEngine(self.store, self.settings, self.clock)

    @cached_property
    def memes(self):
        from companion.meme_core import MemeCaptionService

        return MemeCaptionService(self.store, self.settings, lambda: self.model, self.clock)

    @cached_property
    def voice(self):
        from companion.voice_flow import VoiceFlow

        return VoiceFlow(self.store, self.settings, self.clock)

    @cached_property
    def executive(self):
        from companion.executive import Executive

        return Executive(self.store, self.settings, self.clock)
