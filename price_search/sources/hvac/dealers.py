from price_search.sources.hvac.generic import GenericHvacSource


class UmClimatSource(GenericHvacSource):
    """um-climat.ru — дилер Systemair, Salda и др. (Челябинск).

    Цены и названия отдаются сразу на странице поиска.
    """

    name = "um_climat"

    def __init__(self):
        super().__init__(
            base_url="https://um-climat.ru",
            search_path="/search/",
            item_selector=".s_product_block",
            title_selector="a.s_catalog_item_title",
            price_selector="span.h4-responsive.font-weight-bolder",
            name=self.name,
        )


class AirvekSource(GenericHvacSource):
    """airvek.ru — дилер Systemair, Korf и др. (OpenCart).

    Использует стандартный роут поиска OpenCart; цена в блоке .price.
    """

    name = "airvek"

    def __init__(self):
        super().__init__(
            base_url="https://www.airvek.ru",
            search_path="/index.php?route=product/search",
            item_selector=".product-layout",
            title_selector=".caption h4 a",
            price_selector=".price",
            search_param="search",
            name=self.name,
        )
