"""Comparing people's names: one place for the connectors and the app.

Two strengths of match:

* **same name** (:func:`name_key`): the same words without diacritics, case
  or order. "Popescu Ion" = "Ion Popescu" = "Ión POPESCU".
* **name variant** (:func:`name_relation` returns ``"variant"``): probably the
  same name written differently: a middle or double name ("Ion-Marian
  Popescu"), a maiden name added ("Maria Ionescu Popescu"), a nickname
  ("Ionuț", "Alex"), a Cyrillic or phonetic spelling ("Попеску Ион",
  "Popesku"). Variants are leads for a human or the verify agent, never a
  reason to merge two people.
"""

from __future__ import annotations

import re
import unicodedata

_WORD = re.compile(r"[a-z0-9]+")
# Letters NFKD leaves alone.
_FOLD = str.maketrans({"ß": "ss", "æ": "ae", "ø": "o", "œ": "oe", "đ": "d", "ł": "l",
                       "ı": "i", "þ": "th"})
_CYRILLIC = {
    "а": "a", "б": "b", "в": "v", "г": "g", "ґ": "g", "д": "d", "е": "e", "ё": "e",
    "є": "ie", "ж": "zh", "з": "z", "и": "i", "і": "i", "ї": "i", "й": "i", "к": "k",
    "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "",
    "ы": "y", "ь": "", "э": "e", "ю": "iu", "я": "ia", "ђ": "dj", "ј": "j", "љ": "lj",
    "њ": "nj", "ћ": "c", "џ": "dz",
}
_CYR = str.maketrans(_CYRILLIC)

# Diminutives and short forms -> one canonical form (Romanian and English).
_NICKNAMES: dict[str, list[str]] = {
    "ion": ["ionut", "ionel", "nelu", "ionica", "ionita"],
    "gheorghe": ["ghita", "gigi", "gica", "gheorghita"],
    "constantin": ["costel", "costica", "costi", "titi", "costin"],
    "alexandru": ["alex", "sandu", "alexandrel", "alexander", "aleksandr", "sasha"],
    "mihai": ["mihaita", "misu", "michael", "mike", "mihail", "mikhail"],
    "nicolae": ["nicu", "niculae", "nae", "nicolas", "nicholas", "nick", "nikolai"],
    "dumitru": ["dumi", "mitica", "mitu", "dimitrie", "dmitri"],
    "stefan": ["fanel", "stefanut", "stephen", "steven", "steve"],
    "cristian": ["cristi", "kristian"],
    "daniel": ["dani", "dan", "danny"],
    "gabriel": ["gabi"],
    "valentin": ["vali"],
    "vasile": ["vasilica", "vasili", "vasily"],
    "florin": ["florinel"],
    "elena": ["lena", "ela", "lenuta", "helen", "helena"],
    "ioana": ["ioanina", "joanna", "johanna"],
    "ecaterina": ["catalina", "katerina", "katherine", "catherine", "kate", "katie", "cati"],
    "maria": ["maricica", "mariuca", "mary"],
    "william": ["bill", "will", "billy", "willy"],
    "robert": ["bob", "rob", "bobby", "robbie"],
    "richard": ["rick", "dick", "rich"],
    "james": ["jim", "jimmy", "jamie"],
    "elizabeth": ["liz", "beth", "betty", "eliza", "elisabeta"],
    "thomas": ["tom", "tommy", "toma"],
    "joseph": ["joe", "joey", "iosif"],
    "anthony": ["tony", "anton", "antonio"],
    "christopher": ["chris"],
    "david": ["dave"],
    "edward": ["ed", "ted", "eddie"],
    "andrei": ["andreiut", "andrew", "andy", "andrey"],
    "petru": ["petre", "peter", "pete", "pyotr", "petrica"],
    "ivan": ["john", "johnny", "ioan", "jon", "jan", "juan", "giovanni"],
}
_CANON = {n: k for k, names in _NICKNAMES.items() for n in names}
# "ioan" is the formal "ion" in Romanian.
_CANON["ioan"] = "ion"


def fold(text: str) -> str:
    """Lowercase Latin without diacritics: "Ștefan Țurcan" -> "stefan turcan",
    "Попеску" -> "popesku"."""
    text = (text or "").lower().translate(_CYR).translate(_FOLD)
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).translate(_FOLD)


def name_tokens(name: str) -> list[str]:
    return _WORD.findall(fold((name or "").split(" @ ")[0]))


def name_key(name: str) -> str:
    """Order-free, diacritic-free form of a name, for comparing people."""
    return " ".join(sorted(name_tokens(name)))


def same_name(a: str, b: str) -> bool:
    ka = name_key(a)
    return bool(ka) and ka == name_key(b)


def _phonetic(token: str) -> str:
    """Spelling-insensitive form: c/k/q, ph/f, w/v, y/i, x/ks, doubled letters."""
    t = token
    for a, b in (("sch", "sk"), ("ph", "f"), ("ck", "k"), ("ch", "k"), ("x", "ks"),
                 ("q", "k"), ("c", "k"), ("w", "v"), ("y", "i"), ("j", "i"), ("tz", "t"),
                 ("sh", "s"), ("zh", "j"), ("ts", "t")):
        t = t.replace(a, b)
    return re.sub(r"(.)\1+", r"\1", t)


def loose_tokens(name: str) -> set[str]:
    """Tokens with nicknames canonicalised and spelling flattened."""
    out = set()
    for t in name_tokens(name):
        if len(t) < 2:
            continue   # initials are too weak to compare on
        out.add(_phonetic(_CANON.get(t, t)))
    return out


def name_relation(a: str, b: str) -> str | None:
    """``"same"``, ``"variant"`` or ``None`` for two person names."""
    if same_name(a, b):
        return "same"
    la, lb = loose_tokens(a), loose_tokens(b)
    if len(la) < 2 or len(lb) < 2:
        return None
    if la == lb:
        return "variant"
    small, big = (la, lb) if len(la) <= len(lb) else (lb, la)
    return "variant" if small <= big else None


def matches_query(query: str, candidate: str) -> bool:
    """Does a listed name contain every word of the searched one (any order,
    any spelling)? For filtering lists that the source doesn't search for us."""
    q = loose_tokens(query) or {_phonetic(t) for t in name_tokens(query)}
    return bool(q) and q <= loose_tokens(candidate) | {_phonetic(t) for t in name_tokens(candidate)}
