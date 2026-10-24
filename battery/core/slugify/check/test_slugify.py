from text import slugify


def test_simple_title():
    assert slugify("Hello World") == "hello-world"


def test_accents_become_ascii():
    assert slugify("Café Crème Brûlée") == "cafe-creme-brulee"


def test_runs_of_punctuation_become_one_hyphen():
    assert slugify("  C++ & Rust -- a comparison!! ") == "c-rust-a-comparison"


def test_other_non_ascii_is_dropped():
    assert slugify("東京 Tokyo 2026") == "tokyo-2026"


def test_cut_to_fifty_without_a_trailing_hyphen():
    slug = slugify("word " * 30)
    assert len(slug) <= 50 and not slug.endswith("-") and slug.startswith("word-word")


def test_nothing_sluggable_gives_empty():
    assert slugify("!!! ???") == ""
