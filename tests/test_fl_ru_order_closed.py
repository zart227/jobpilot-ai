from app.services.fl_ru_browser import FL_RU_ORDER_CLOSED_ERROR, is_fl_ru_order_closed_error


def test_is_fl_ru_order_closed_error() -> None:
    assert is_fl_ru_order_closed_error(FL_RU_ORDER_CLOSED_ERROR)
    assert is_fl_ru_order_closed_error("проект закрыт на fl.ru")
    assert not is_fl_ru_order_closed_error("Кнопка «Откликнуться» не найдена")
    assert not is_fl_ru_order_closed_error(None)
