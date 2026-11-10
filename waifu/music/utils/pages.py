"""Simple list pagination helper."""


def paginate(items: list, page: int = 1, page_size: int = 10) -> tuple[list, int, int]:
    page_size = max(1, min(int(page_size), 50))
    total_pages = max(1, (len(items) + page_size - 1) // page_size)
    page = max(1, min(int(page), total_pages))
    start = (page - 1) * page_size
    return items[start:start + page_size], page, total_pages
