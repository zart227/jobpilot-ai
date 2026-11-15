from app.services.weblancer_listing_parse import parse_listing_html


def test_parse_nextjs_listing_links() -> None:
    html = """
    <html><body>
    <a href="/freelance/veb-programmirovanie-31/sistema-avtopostinga-1270416/">Система автопостинга</a>
    <a href="/freelance/veb-programmirovanie-31/other-1270401/">Другой заказ</a>
    </body></html>
    """
    rows = parse_listing_html(html)
    assert len(rows) == 2
    assert rows[0].title == "Система автопостинга"
