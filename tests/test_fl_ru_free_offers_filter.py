from app.services.fl_ru_simple_filter import fl_ru_listing_html_is_free_for_all


def test_free_badge_detected_in_listing_html() -> None:
    html = """
    <span class="b-post__bold b-layout__txt_inline-block">Для всех</span>
    """
    assert fl_ru_listing_html_is_free_for_all(html)


def test_paid_listing_without_badge() -> None:
    html = """
    <h2><a href="/projects/1/x.html">Сделать сайт</a></h2>
    <span class="b-post__price">5000 ₽</span>
    """
    assert not fl_ru_listing_html_is_free_for_all(html)
