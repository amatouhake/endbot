"""Script source in a book and quill: pages hold whole lines, joined by newlines.

Bedrock books have a per-page character limit and a page limit, so one book
holds a bounded script. Reading only inspects the held item; writing only fills
a book and quill the player already holds, never creating items, and overwrites
written pages only when they are the ones the player confirmed.
"""

from endbot_core.i18n import LocalizedError

BOOK_PAGE_CHARS = 256
BOOK_PAGES = 50
READABLE_BOOKS = {"minecraft:writable_book", "minecraft:written_book"}
WRITABLE_BOOK = "minecraft:writable_book"


def pages_to_source(pages):
    """One line break between pages unless a page already ends with one; trailing blank pages are ignored."""
    pages = list(pages)
    while pages and not pages[-1].strip():
        pages.pop()
    if not pages:
        raise LocalizedError("The book is empty")
    text = ""
    for page in pages:
        page = page.replace("\r\n", "\n").replace("\r", "\n")
        text += page if not text or text.endswith("\n") else "\n" + page
    return text.removesuffix("\n") + "\n"


def source_to_pages(source):
    """Pack whole lines into pages; a line longer than a page or a source longer than a book is refused."""
    pages, page = [], None
    for number, line in enumerate(source.splitlines(), start=1):
        if len(line) > BOOK_PAGE_CHARS:
            raise LocalizedError("Line {line} is longer than a book page ({limit} characters)", line=number,
                                 limit=BOOK_PAGE_CHARS)
        if page is not None and len(page) + 1 + len(line) <= BOOK_PAGE_CHARS:
            page += "\n" + line
            continue
        if page is not None:
            pages.append(page)
        page = line
    if page is not None:
        pages.append(page)
    if not pages:
        raise LocalizedError("The script is empty")
    if len(pages) > BOOK_PAGES:
        raise LocalizedError("The script does not fit in one book ({pages} of {limit} pages)", pages=len(pages),
                             limit=BOOK_PAGES)
    return pages


def _value(tag):
    return str(getattr(tag, "value", tag))


def _page_list(nbt):
    """The `pages` list of a book's item tag (Bedrock keeps it at the top, older dumps under `tag`)."""
    if nbt is None:
        return None
    if "pages" in nbt:
        return nbt["pages"]
    if "tag" in nbt and "pages" in nbt["tag"]:
        return nbt["tag"]["pages"]
    return None


def book_pages(item, writable=False):
    """The page texts of a held book and quill or (unless ``writable``) written book."""
    if writable and (item is None or item.type.id != WRITABLE_BOOK):
        raise LocalizedError("Hold a book and quill in your main hand")
    if item is None or item.type.id not in READABLE_BOOKS:
        raise LocalizedError("Hold a book and quill or a written book in your main hand")
    pages = _page_list(item.nbt)
    return [_value(page["text"]) if "text" in page else "" for page in pages or ()]


def written(pages):
    return any(page.strip() for page in pages)


def fill_book(item, pages, expected=None):
    """Write pages into a book and quill; returns the updated item for the caller to put back.

    A book that already has writing is overwritten only when its pages still equal ``expected``.
    """
    current = book_pages(item, writable=True)
    if written(current) and list(current) != expected:
        raise LocalizedError("The book and quill is not empty; hold an empty one" if expected is None
                             else "The book changed; check it again")
    from endstone.nbt import CompoundTag, ListTag, StringTag

    nbt = item.nbt if item.nbt is not None else CompoundTag()
    nbt["pages"] = ListTag([CompoundTag({"photoname": StringTag(""), "text": StringTag(page)}) for page in pages])
    item.nbt = nbt
    return item
