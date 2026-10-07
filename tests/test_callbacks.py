import pytest

from app.bot.callbacks import Action, AdminRepStep, Moderation, ReportStep, ReputationModeration


@pytest.mark.parametrize(
    "callback",
    [
        Action(name="home"),
        Action(name="vote+", value="u:1"),
        Action(name="profile", value="123"),
        ReportStep(action="submit", nonce="a" * 32),
        Moderation(action="approve", reference="SC-2026-000123"),
        ReputationModeration(action="approve", reference="RP-2026-000123"),
        AdminRepStep(action="submit", nonce="a" * 16),
    ],
)
def test_callback_round_trip_and_telegram_limit(callback):
    packed = callback.pack()
    assert len(packed.encode()) <= 64
    assert type(callback).unpack(packed) == callback


@pytest.mark.parametrize(
    "value",
    ["mod:approve", "mod:approve:SC-2026-1:extra", "wrong:approve:SC-2026-1", "", "draft:submit"],
)
def test_malformed_callbacks_cannot_unpack(value):
    with pytest.raises((ValueError, TypeError)):
        Moderation.unpack(value)
