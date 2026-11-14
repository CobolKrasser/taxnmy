"""A generated dataset for the scale test of the lint (TAX-16): products and publishers that
pass the lint, with codes shared between new and old files so that signals arise."""

from __future__ import annotations

from pathlib import Path

WORDS = ("alpha", "bravo", "charlie", "delta", "echo", "foxtrot", "golf", "hotel", "india", "juliett",
         "kilo", "lima", "mike", "november", "oscar", "papa", "quebec", "romeo", "sierra", "tango")


def name_of(i: int) -> str:
    """A product name whose key follows the writing rule; no two share their words."""
    return f"{WORDS[i % 20].title()} {WORDS[(i // 20) % 20].title()} {i}"


def product_text(pub: str, i: int, shared_cpe: str | None = None) -> str:
    name = name_of(i)
    key = name.lower().replace(" ", "-")
    cpe = shared_cpe or f"cpe:2.3:a:{pub}:product_{i}"
    return (f"key: {pub}/{key}\npublisher: {pub}\nname: {name}\nkind: software\n"
            f"source: https://{pub}.example/{key}\nversions:\n  source: https://{pub}.example/{key}/releases\n"
            f"identifiers:\n- cpe: {cpe}\n  source: https://{pub}.example/{key}/cpe\naliases: []\n")


def product_rel(pub: str, i: int) -> str:
    return f"products/{pub}/{name_of(i).lower().replace(' ', '-')}.yaml"


def publisher_of(i: int, publishers: int) -> str:
    return f"publisher{i % publishers}"


def generate(root: Path, old: int, new: int, publishers: int = 1000, shared_every: int = 100) -> list[str]:
    """Writes `old` products and their publishers, then `new` products. Every `shared_every`-th new
    product shares its CPE with an old product: a signal on both, of which only the new one may be
    reported. Returns the paths of the new files, as a pull request would list them."""
    (root / "publishers").mkdir(parents=True)
    for p in range(publishers):
        (root / "publishers" / f"publisher{p}.yaml").write_text(
            f"key: publisher{p}\nname: Publisher{p}\nsource: https://publisher{p}.example/about\naliases: []\n", encoding="utf-8")
    for i in range(old):
        pub = publisher_of(i, publishers)
        path = root / product_rel(pub, i)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(product_text(pub, i), encoding="utf-8")
    changed = []
    for i in range(old, old + new):
        pub = publisher_of(i, publishers)
        shared = None
        if (i - old) % shared_every == 0:
            j = (i - old) % old
            shared = f"cpe:2.3:a:{publisher_of(j, publishers)}:product_{j}"
        rel = product_rel(pub, i)
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(product_text(pub, i, shared), encoding="utf-8")
        changed.append(rel)
    return changed
