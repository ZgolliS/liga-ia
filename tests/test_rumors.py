import json

import pytest

from foot.db import connect, upsert
from foot.reliability import Label
from foot.rumors import build_user_message, extract_claims, pending_articles, rumor_board
from foot.sources import identify, save_source


class FakeLLM:
    """Faux cerveau : renvoie une réponse préparée et mémorise ce qu'on lui envoie."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.received = []
        self.last_model = "fake-model"

    def chat_json(self, messages):
        self.received.append(messages)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


@pytest.fixture
def conn():
    c = connect(":memory:")
    barca, _ = upsert(c, "teams", {"fd_id": 81}, {"name": "FC Barcelona", "short_name": "Barça"})
    balde, _ = upsert(c, "players", {"fd_id": 1}, {"name": "Alejandro Balde", "team_id": barca})
    for i, (domain, day) in enumerate([("marca.com", 1), ("as.com", 2), ("efe.com", 3)]):
        url = f"https://www.{domain}/art{i}"
        save_source(c, identify(url))
        c.execute("INSERT INTO news (url, source_id, title, summary, published_at, updated_at) "
                  "VALUES (?, ?, ?, ?, ?, 'x')",
                  (url, domain, f"Balde y la Roma {i}", "…", f"2026-11-0{day}T10:00:00+00:00"))
        c.execute("INSERT INTO news_mentions VALUES (?, 'player', ?)", (url, balde))
    c.commit()
    return c


def rumor(article, stance="affirme", source=None, to="Roma"):
    return {"article": article, "joueur": "Balde", "club_actuel": "Barça",
            "club_destination": to, "position": stance, "source_originale": source}


def test_user_message_numbers_articles(conn) -> None:
    msg = build_user_message(pending_articles(conn))
    assert "Article 1\nTitre :" in msg and "Article 3" in msg


def test_extract_claims_saves_and_resolves_names(conn) -> None:
    llm = FakeLLM([{"rumeurs": [rumor(1), rumor(2)]}])
    stats = extract_claims(conn, llm)
    assert stats["rumeurs extraites"] == 2 and stats["appels IA"] == 1
    row = conn.execute("SELECT c.from_team, p.name FROM claims c "
                       "JOIN players p ON p.id = c.player_id").fetchone()
    assert (row["name"], row["from_team"]) == ("Alejandro Balde", "FC Barcelona")


def test_articles_are_never_sent_twice(conn) -> None:
    extract_claims(conn, FakeLLM([{"rumeurs": []}]))
    llm = FakeLLM([])
    assert extract_claims(conn, llm)["appels IA"] == 0
    assert llm.received == []


def test_failed_batch_is_retried_next_time(conn) -> None:
    from foot.llm import LLMError

    stats = extract_claims(conn, FakeLLM([LLMError("surcharge")]))
    assert stats["paquets en erreur"] == 1
    assert len(pending_articles(conn)) == 3  # toujours à traiter


def test_malformed_items_are_ignored(conn) -> None:
    llm = FakeLLM([{"rumeurs": [{"article": "abc"}, {"article": 9, "joueur": "X"},
                                rumor(1, stance="peut-être"), rumor(1)]}])
    stats = extract_claims(conn, llm)
    assert stats["rumeurs illisibles"] == 3 and stats["rumeurs extraites"] == 1


def test_board_groups_same_rumor_and_scores_it(conn) -> None:
    extract_claims(conn, FakeLLM([{"rumeurs": [rumor(1), rumor(2), rumor(3)]}]))
    board = rumor_board(conn)
    assert len(board) == 1
    r = board[0]
    assert (r.player, r.to_team, len(r.articles)) == ("Alejandro Balde", "Roma", 3)
    assert r.verdict.label is Label.TRES_PROBABLE  # Marca + AS + EFE l'affirment


def test_relayed_unknown_source_counts_as_unknown(conn) -> None:
    extract_claims(conn, FakeLLM([{"rumeurs": [rumor(1, source="Un journaliste italien")]}]))
    v = rumor_board(conn)[0].verdict
    assert v.label is Label.INTOX
    assert "Un journaliste italien (relayé par" in v.explain()  # la vraie source est citée


def test_neutral_claims_are_not_rumors(conn) -> None:
    extract_claims(conn, FakeLLM([{"rumeurs": [rumor(1, stance="neutre")]}]))
    assert rumor_board(conn) == []


def test_prompt_contains_articles_not_instructions_from_them(conn) -> None:
    llm = FakeLLM([{"rumeurs": []}])
    extract_claims(conn, llm)
    system, user = llm.received[0]
    assert system["role"] == "system" and "N'invente rien" in system["content"]
    assert user["role"] == "user" and "Balde y la Roma" in user["content"]
    json.dumps(llm.received)  # sérialisable


def test_article_number_written_as_text_is_understood(conn) -> None:
    item = rumor(1)
    item["article"] = "Article 2"
    assert extract_claims(conn, FakeLLM([{"rumeurs": [item]}]))["rumeurs extraites"] == 1


def test_club_official_statement_is_official(conn) -> None:
    # Cas réel : le directeur sportif du club annonce que le joueur reste.
    item = rumor(1, source="Directeur sportif du Barça")
    item["type_source"] = "officiel"
    extract_claims(conn, FakeLLM([{"rumeurs": [item]}]))
    assert rumor_board(conn)[0].verdict.label is Label.OFFICIEL


def test_pundit_opinion_is_not_a_rumor(conn) -> None:
    # Cas réel : un ancien entraîneur donne son avis à la télé.
    item = rumor(1, source="Un consultant TV")
    item["type_source"] = "opinion"
    extract_claims(conn, FakeLLM([{"rumeurs": [item]}]))
    assert rumor_board(conn) == []
