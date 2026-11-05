"""C068: conversation history excludes current inbound and preserves order."""

from app.services.kwork_conversation import conversation_from_kwork_thread


def test_duplicate_client_text_excluded_by_message_id_not_whole_history() -> None:
    messages = [
        {"MID": "m1", "from_username": "buyer", "message": "Привет", "time": 1, "type": "text"},
        {"MID": "m2", "from_username": "buyer", "message": "Привет", "time": 2, "type": "text"},
    ]
    history = conversation_from_kwork_thread(
        messages,
        my_username="seller",
        my_user_id=1,
        exclude_client_message_id="m2",
    )
    assert history == [{"role": "client", "text": "Привет"}]


def test_thread_turns_sorted_by_timestamp() -> None:
    messages = [
        {"MID": "late", "from_username": "buyer", "message": "B", "time": 30, "type": "text"},
        {"MID": "early", "from_username": "buyer", "message": "A", "time": 10, "type": "text"},
    ]
    history = conversation_from_kwork_thread(
        messages,
        my_username="seller",
        my_user_id=1,
        exclude_client_message_id="late",
    )
    assert [t["text"] for t in history] == ["A"]
