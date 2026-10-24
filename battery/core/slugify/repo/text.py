def slugify(title: str) -> str:
    """A URL slug for `title`.

    - Accented letters become their plain ASCII letter ("Café" -> "cafe"); any other
      non-ASCII character is dropped.
    - Letters are lower-cased.
    - Every run of characters that are not ASCII letters or digits becomes one hyphen.
    - No hyphen at the start or the end.
    - At most 50 characters; if cutting to 50 leaves a hyphen at the end, it is removed.
    - A title with no letters or digits gives "".
    """
    raise NotImplementedError
