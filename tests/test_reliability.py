import pytest

from foot.reliability import Label, Report, Stance, Tier, assess
from foot.sources import identify

A, D = Stance.AFFIRME, Stance.DEMENT


def r(source: str, tier: Tier, stance: Stance = A, day: int = 1) -> Report:
    return Report(source, tier, stance, f"2026-11-{day:02d}T12:00:00Z")


def test_official_confirmation_wins() -> None:
    v = assess([r("Site inconnu", Tier.INCONNUE), r("Club", Tier.OFFICIEL)])
    assert v.label is Label.OFFICIEL


def test_official_denial_means_intox_even_if_media_insist() -> None:
    v = assess([r("Agence", Tier.TRES_FIABLE), r("Marca", Tier.FIABLE), r("Club", Tier.OFFICIEL, D)])
    assert v.label is Label.INTOX
    assert "démenti officiellement" in v.explain()


def test_latest_official_statement_counts() -> None:
    # Le club dément en octobre... puis officialise en novembre.
    v = assess([r("Club", Tier.OFFICIEL, D, day=1), r("Club", Tier.OFFICIEL, A, day=20)])
    assert v.label is Label.OFFICIEL


def test_two_very_reliable_sources_is_very_likely() -> None:
    v = assess([r("EFE", Tier.TRES_FIABLE), r("Reuters", Tier.TRES_FIABLE)])
    assert v.label is Label.TRES_PROBABLE


def test_one_big_media_is_a_rumor() -> None:
    assert assess([r("Marca", Tier.FIABLE)]).label is Label.RUMEUR


def test_many_unknown_sources_stay_intox() -> None:
    # 20 sites inconnus qui se recopient ne valent pas une source fiable.
    v = assess([r(f"blog{i}", Tier.INCONNUE) for i in range(20)])
    assert v.label is Label.INTOX
    assert "aucune source fiable" in v.explain()


def test_same_source_repeating_counts_once() -> None:
    v = assess([r("Marca", Tier.FIABLE, day=d) for d in range(1, 10)])
    assert v.label is Label.RUMEUR  # pas "très probable" malgré 9 articles
    assert v.score == pytest.approx(1.5)


def test_reliable_denial_lowers_confidence() -> None:
    v = assess([r("Marca", Tier.FIABLE), r("AS", Tier.FIABLE), r("EFE", Tier.TRES_FIABLE, D)])
    assert v.label is Label.INTOX
    assert "le dément" in v.explain()


def test_source_changing_its_mind_uses_latest_position() -> None:
    v = assess([r("EFE", Tier.TRES_FIABLE, A, day=1), r("EFE", Tier.TRES_FIABLE, D, day=5)])
    assert v.label is Label.INTOX


def test_no_report_raises() -> None:
    with pytest.raises(ValueError):
        assess([])


@pytest.mark.parametrize(
    ("url", "tier"),
    [
        ("https://www.marca.com/futbol/real-madrid/2026/11/12/x.html", Tier.FIABLE),
        ("https://www.laliga.com/noticias/abc", Tier.OFFICIEL),
        ("https://efe.com/deportes/x", Tier.TRES_FIABLE),
        ("https://blog-mercato-123.fr/scoop", Tier.INCONNUE),
        ("https://notmarca.com/x", Tier.INCONNUE),  # ne pas confondre avec marca.com
    ],
)
def test_identify_source_from_url(url: str, tier: Tier) -> None:
    assert identify(url)[1] is tier
