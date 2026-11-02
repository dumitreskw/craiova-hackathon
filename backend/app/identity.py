"""Who is the subject? Telling a person apart from their namesakes.

A name is a weak key: the Companies House officer, the Hunter contact and the
footballer in Bucharest can all be called "Ion Popescu". This module holds the
pieces that keep them apart:

* :func:`name_key`: names compared without diacritics, case or word order, so
  "Popescu Ion", "ion popescu" and "Ión Popescu" are the same name;
* the **subject profile** of a Person target (birth year, places, employers,
  known identifiers; ``targets.profile``), which the agents read in their
  brief and the scan uses to search the name *with* context;
* :func:`profile_conflict`: a sanctions / wanted-list hit whose birth year or
  nationality contradicts the profile is rejected automatically, with the
  reason recorded (an analyst can still override it; confirming stays human);
* **who's who** (``identity_clusters``): the verification agent groups
  same-name results into distinct people; the analyst picks the subject.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

from connectors.base import EntityType

from .models import Database, new_id, now

PROFILE_CHECK = "profile_check"   # reviewer name of the automatic rejection
PROFILE_SOURCE = "subject_profile"  # finding that records the brief and ties identifiers
COMMON_NAME_AT = 3                # namesakes + list matches that make a name "common"

_WORD = re.compile(r"[a-z0-9]+")
_YEAR = re.compile(r"\b(19\d{2}|20[0-3]\d)\b")
# Letters NFKD leaves alone.
_FOLD = str.maketrans({"ß": "ss", "æ": "ae", "ø": "o", "œ": "oe", "đ": "d", "ł": "l",
                       "ı": "i", "þ": "th"})


def fold(text: str) -> str:
    """Lowercase without diacritics: "Ștefan Țurcan" -> "stefan turcan"."""
    text = unicodedata.normalize("NFKD", (text or "").lower().translate(_FOLD))
    return "".join(c for c in text if not unicodedata.combining(c)).translate(_FOLD)


def name_tokens(name: str) -> list[str]:
    return _WORD.findall(fold(name))


def name_key(name: str) -> str:
    """Order-free, diacritic-free form of a name, for comparing people."""
    return " ".join(sorted(name_tokens(name.split(" @ ")[0])))


def same_name(a: str, b: str) -> bool:
    ka = name_key(a)
    return bool(ka) and ka == name_key(b)


# --- the profile ------------------------------------------------------------------

_LIST_FIELDS = ("aliases", "nationalities", "locations", "employers", "roles", "education",
                "relatives", "emails", "usernames", "phones", "profile_urls")


def is_empty(profile: dict | None) -> bool:
    p = profile or {}
    return not (p.get("birth_year") or p.get("birth_date") or p.get("notes")
                or any(p.get(f) for f in _LIST_FIELDS))


def birth_year(profile: dict | None) -> int | None:
    p = profile or {}
    if p.get("birth_year"):
        return int(p["birth_year"])
    m = _YEAR.search(p.get("birth_date") or "")
    return int(m.group(1)) if m else None


def person_targets(db: Database, investigation_id: str) -> list[dict]:
    return db.query("SELECT * FROM targets WHERE investigation_id = ? AND type = 'Person' "
                    "ORDER BY created_at", (investigation_id,))


def profile_for_entity(db: Database, entity_id: str) -> dict:
    row = db.one("SELECT profile FROM targets WHERE entity_id = ? AND type = 'Person' "
                 "ORDER BY created_at LIMIT 1", (entity_id,))
    return (row or {}).get("profile") or {}


def subject_profile(db: Database, investigation_id: str) -> dict:
    """The profiles of every Person target merged into one (usually there is one)."""
    out: dict[str, Any] = {}
    for t in person_targets(db, investigation_id):
        p = t.get("profile") or {}
        for f in _LIST_FIELDS:
            for v in p.get(f) or []:
                if v not in out.setdefault(f, []):
                    out[f].append(v)
        for f in ("birth_year", "birth_date"):
            if p.get(f) and not out.get(f):
                out[f] = p[f]
        if p.get("notes"):
            out["notes"] = (out.get("notes", "") + "\n" + p["notes"]).strip()
    return out


def clean_profile(profile: dict) -> dict:
    """Trim and de-duplicate list values; drop empty fields."""
    out: dict[str, Any] = {}
    for f in _LIST_FIELDS:
        seen, vals = set(), []
        for v in profile.get(f) or []:
            v = " ".join(str(v).split())
            if v and v.lower() not in seen:
                seen.add(v.lower())
                vals.append(v)
        if vals:
            out[f] = vals
    for f in ("birth_year", "birth_date"):
        if profile.get(f):
            out[f] = profile[f]
    if (profile.get("notes") or "").strip():
        out["notes"] = profile["notes"].strip()
    return out


def identifiers(profile: dict) -> list[tuple[str, str]]:
    """``(value, type)`` of the known identifiers that become targets of their own."""
    out = [(e, EntityType.EMAIL.value) for e in profile.get("emails") or []]
    out += [(u.lstrip("@"), EntityType.USERNAME.value) for u in profile.get("usernames") or []]
    out += [(p, EntityType.PHONE.value) for p in profile.get("phones") or []]
    return out


def profile_lines(profile: dict) -> list[str]:
    """The profile as brief lines, for the agents."""
    p = profile or {}
    parts = []
    year = birth_year(p)
    if p.get("birth_date"):
        parts.append(f"born {p['birth_date']}")
    elif year:
        parts.append(f"born {year}")
    labels = [("nationalities", "nationality"), ("locations", "lives/works in"),
              ("employers", "works or worked at"), ("roles", "role"),
              ("education", "studied at"), ("relatives", "relatives"),
              ("aliases", "also known as"), ("emails", "emails"), ("usernames", "usernames"),
              ("phones", "phones"), ("profile_urls", "profiles")]
    for field, label in labels:
        if p.get(field):
            parts.append(f"{label}: {', '.join(map(str, p[field]))}")
    lines = ["; ".join(parts)] if parts else []
    if p.get("notes"):
        lines.append(f"Notes: {p['notes'][:1500]}")
    return lines


def brief(db: Database, investigation_id: str) -> str:
    """What the brief says about who the subject is, for the agents' first message."""
    targets = person_targets(db, investigation_id)
    if not targets:
        return ""
    profile = subject_profile(db, investigation_id)
    chosen = db.one("SELECT label, distinguishing FROM identity_clusters WHERE "
                    "investigation_id = ? AND chosen = 1", (investigation_id,))
    picked = []
    if chosen:
        picked.append(f"- The analyst identified the subject as: {chosen['label']}"
                      + (f" ({chosen['distinguishing']})" if chosen["distinguishing"] else ""))
    if is_empty(profile):
        none = ("Subject profile: none given (only the name). Treat every same-name result "
                "as a possible namesake; say in your summary which details would tell the "
                "subject apart.")
        return "\n".join([none, *picked])
    head = ("Subject profile (from the analyst's brief; judge \"is this the subject?\" "
            "against it: a detail that contradicts it means a namesake, one that matches it "
            "is a disambiguator):")
    lines = [head]
    lines += [f"- {line}" for line in profile_lines(profile)]
    return "\n".join(lines + picked)


# --- search context ---------------------------------------------------------------

def search_query(name: str, profile: dict | None) -> str:
    """A web search for the name with the most telling detail from the profile."""
    p = profile or {}
    context = (p.get("employers") or p.get("locations") or [None])[0]
    return f'"{name}" "{context}"' if context else f'"{name}"'


# --- conflicts with a screening hit ----------------------------------------------------

# ISO-3166 alpha-2 codes of the countries the lists use most, by name and demonym.
_COUNTRIES = {
    "ro": "romania, romanian, roumanie, rumanien, rumania", "md": "moldova, moldovan",
    "bg": "bulgaria, bulgarian", "hu": "hungary, hungarian", "ua": "ukraine, ukrainian",
    "ru": "russia, russian, russian federation", "by": "belarus, belarusian",
    "rs": "serbia, serbian", "hr": "croatia, croatian", "si": "slovenia, slovenian",
    "ba": "bosnia, bosnian", "me": "montenegro, montenegrin",
    "mk": "north macedonia, macedonia, macedonian", "al": "albania, albanian",
    "xk": "kosovo, kosovar", "gr": "greece, greek", "tr": "turkey, turkish, turkiye",
    "cy": "cyprus, cypriot", "it": "italy, italian", "es": "spain, spanish",
    "pt": "portugal, portuguese", "fr": "france, french", "de": "germany, german",
    "at": "austria, austrian", "ch": "switzerland, swiss", "be": "belgium, belgian",
    "nl": "netherlands, dutch, holland", "lu": "luxembourg, luxembourgish",
    "gb": "united kingdom, uk, great britain, britain, british, england, english, "
          "scotland, scottish, wales, welsh",
    "ie": "ireland, irish", "pl": "poland, polish", "cz": "czech republic, czechia, czech",
    "sk": "slovakia, slovak", "lt": "lithuania, lithuanian", "lv": "latvia, latvian",
    "ee": "estonia, estonian", "fi": "finland, finnish", "se": "sweden, swedish",
    "no": "norway, norwegian", "dk": "denmark, danish", "is": "iceland, icelandic",
    "mt": "malta, maltese", "us": "united states, usa, america, american",
    "ca": "canada, canadian", "mx": "mexico, mexican", "br": "brazil, brazilian",
    "ar": "argentina, argentinian, argentine", "co": "colombia, colombian",
    "ve": "venezuela, venezuelan", "cn": "china, chinese", "jp": "japan, japanese",
    "kr": "south korea, korea", "kp": "north korea", "in": "india, indian",
    "pk": "pakistan, pakistani", "af": "afghanistan, afghan", "ir": "iran, iranian",
    "iq": "iraq, iraqi", "sy": "syria, syrian", "lb": "lebanon, lebanese",
    "il": "israel, israeli", "ps": "palestine, palestinian", "sa": "saudi arabia, saudi",
    "ae": "united arab emirates, uae, emirati", "eg": "egypt, egyptian",
    "ly": "libya, libyan", "tn": "tunisia, tunisian", "dz": "algeria, algerian",
    "ma": "morocco, moroccan", "ng": "nigeria, nigerian", "so": "somalia, somali",
    "sd": "sudan, sudanese", "ye": "yemen, yemeni", "ge": "georgia, georgian",
    "am": "armenia, armenian", "az": "azerbaijan, azerbaijani", "kz": "kazakhstan, kazakh",
    "uz": "uzbekistan, uzbek", "au": "australia, australian",
}
_ALPHA3 = {"rou": "ro", "usa": "us", "gbr": "gb", "deu": "de", "fra": "fr", "ita": "it",
           "esp": "es", "rus": "ru", "ukr": "ua", "mda": "md", "hun": "hu", "bgr": "bg",
           "srb": "rs", "tur": "tr", "pol": "pl", "nld": "nl", "bel": "be", "aut": "at"}
# (phrase, code), longest phrase first so "north korea" wins over "korea".
_PHRASES = sorted(((p.strip(), code) for code, names in _COUNTRIES.items()
                   for p in names.split(",")), key=lambda x: -len(x[0]))


def country_codes(values: Any) -> set[str]:
    """ISO-2 codes from list values such as "RO", "ro", "Romanian", ["Romania"]."""
    if values is None:
        return set()
    items = values if isinstance(values, (list, tuple, set)) else [values]
    out: set[str] = set()
    for v in items:
        text = " ".join(_WORD.findall(fold(str(v))))
        if not text:
            continue
        if len(text) == 2 and text in _COUNTRIES:
            out.add(text)
        elif len(text) == 3 and text in _ALPHA3:
            out.add(_ALPHA3[text])
        else:
            padded = f" {text} "
            for phrase, code in _PHRASES:
                if f" {phrase} " in padded:
                    out.add(code)
                    padded = padded.replace(f" {phrase} ", "  ")
    return out


def _years(values: Any) -> set[int]:
    if values is None:
        return set()
    items = values if isinstance(values, (list, tuple, set)) else [values]
    return {int(y) for v in items for y in _YEAR.findall(str(v))}


_BIRTH_FIELDS = ("birth_date", "date_of_birth", "dates_of_birth_used", "dob", "dob_code",
                 "birth_year")
_NATIONALITY_FIELDS = ("nationality", "nationalities", "countries", "country_code",
                       "citizenship")


def profile_conflict(profile: dict | None, disambiguators: dict | None) -> str | None:
    """Why a screening hit can't be the subject, or ``None`` if nothing contradicts it.

    Only hard facts count: a birth year more than a year away from every one
    the listing gives, or listed nationalities that share none with the
    profile's. Missing data on either side is never a conflict.
    """
    d = disambiguators or {}
    p = profile or {}
    reasons = []
    year = birth_year(p)
    if year:
        listed = set().union(*(_years(d.get(f)) for f in _BIRTH_FIELDS))
        if listed and all(abs(y - year) > 1 for y in listed):
            reasons.append(f"born {', '.join(map(str, sorted(listed)))} per the listing; "
                           f"the subject was born in {year}")
    mine = country_codes(p.get("nationalities"))
    if mine:
        listed_c = set().union(*(country_codes(d.get(f)) for f in _NATIONALITY_FIELDS))
        if listed_c and not listed_c & mine:
            reasons.append(f"listed nationality/country {', '.join(sorted(c.upper() for c in listed_c))}; "
                           f"the subject is {', '.join(sorted(c.upper() for c in mine))}")
    if not reasons:
        return None
    return "Contradicts the subject profile: " + "; ".join(reasons) + "."


def disambiguators_of(entity: dict) -> dict:
    """The disambiguators a legal candidate carries, from its raw attributes."""
    out: dict[str, Any] = {}
    for v in (entity.get("attributes") or {}).get("disambiguators", []):
        if isinstance(v.get("value"), dict):
            for k, x in v["value"].items():
                out.setdefault(k, x)
    return out


def screen_candidates(db: Database, resolver: Any, bus: Any, investigation_id: str,
                      entity_ids: list[str] | None = None, user: str = "system") -> list[str]:
    """Reject the Person screening hits that contradict the subject profile.

    Skips anything an analyst has already ruled on, and anything already
    rejected. Returns the ids it rejected.
    """
    from . import review  # review imports the resolver; keep the import lazy

    profile = subject_profile(db, investigation_id)
    if is_empty(profile):
        return []
    sql = ("SELECT * FROM entities WHERE investigation_id = ? AND type = 'Person' "
           "AND status = 'candidate'")
    params: list = [investigation_id]
    if entity_ids is not None:
        if not entity_ids:
            return []
        sql += f" AND id IN ({','.join('?' * len(entity_ids))})"
        params += entity_ids
    rejected = []
    for row in db.query(sql, params):
        prev = review.latest(db, row["id"])
        if prev and (not prev["by_agent"] or prev["verdict"] == "rejected"):
            continue
        reason = profile_conflict(profile, disambiguators_of(row))
        if not reason:
            continue
        try:
            review.record(db, resolver, bus, row["id"], "rejected", reason,
                          reviewer=PROFILE_CHECK, user=user, by_agent=True)
        except review.ReviewError:
            continue
        rejected.append(row["id"])
    return rejected


# --- common names ---------------------------------------------------------------------

def name_stats(db: Database, investigation_id: str, entity_id: str) -> dict[str, int]:
    """How many namesakes and screening matches came back for a Person target."""
    target = db.get("entities", entity_id)
    if target is None:
        return {"namesakes": 0, "list_matches": 0}
    key = name_key(target["key"])
    namesakes = list_matches = 0
    for r in db.query("SELECT id, key, status, attributes FROM entities WHERE "
                      "investigation_id = ? AND type = 'Person' AND id != ?",
                      (investigation_id, entity_id)):
        if name_key(r["key"]) != key:
            continue
        if r["status"] == "candidate":
            list_matches += 1
        elif "namesake" in (r["attributes"] or {}):
            namesakes += 1
    namesakes = max(namesakes, db.one(
        "SELECT count(*) AS n FROM identity_clusters WHERE investigation_id = ? "
        "AND is_subject = 0", (investigation_id,))["n"])
    return {"namesakes": namesakes, "list_matches": list_matches}


def overview(db: Database, investigation_id: str) -> dict[str, Any]:
    """Per Person target: its profile, name statistics and the common-name warning;
    and the who's-who clusters."""
    targets = []
    for t in person_targets(db, investigation_id):
        stats = name_stats(db, investigation_id, t["entity_id"])
        profile = t.get("profile") or {}
        targets.append({
            "target_id": t["id"], "entity_id": t["entity_id"], "value": t["value"],
            "profile": profile, **stats,
            "common_name": is_empty(profile)
            and stats["namesakes"] + stats["list_matches"] >= COMMON_NAME_AT,
        })
    return {"targets": targets, "clusters": clusters(db, investigation_id)}


# --- who's who ------------------------------------------------------------------------------

def clusters(db: Database, investigation_id: str) -> list[dict]:
    return db.query("SELECT * FROM identity_clusters WHERE investigation_id = ? "
                    "ORDER BY chosen DESC, is_subject DESC, created_at, label",
                    (investigation_id,))


class IdentityError(ValueError):
    pass


def save_clusters(db: Database, investigation_id: str, items: list[dict], run_id: str) -> int:
    """Replace the who's-who with the verification agent's grouping.

    An analyst's choice survives when the chosen person is still there (same
    entities, or the same label).
    """
    chosen = db.one("SELECT * FROM identity_clusters WHERE investigation_id = ? AND chosen = 1",
                    (investigation_id,))
    known = {r["id"] for r in db.query("SELECT id FROM entities WHERE investigation_id = ?",
                                       (investigation_id,))}
    db.conn.execute("DELETE FROM identity_clusters WHERE investigation_id = ?",
                    (investigation_id,))
    ts = now()
    n = 0
    for item in items[:20]:
        label = " ".join(str(item.get("label") or "").split())[:200]
        ids = [str(e) for e in item.get("entity_ids") or [] if str(e) in known][:200]
        if not label or not ids:
            continue
        keep = bool(chosen) and (set(ids) & set(chosen["entity_ids"] or [])
                                 or label == chosen["label"])
        db.insert("identity_clusters", {
            "id": new_id("ic"), "investigation_id": investigation_id, "label": label,
            "summary": str(item.get("summary") or "")[:1000],
            "distinguishing": str(item.get("distinguishing") or "")[:500],
            "entity_ids": ids, "is_subject": bool(item.get("is_subject")),
            "chosen": bool(keep), "run_id": run_id, "created_at": ts})
        if keep:
            chosen = None   # only one can inherit the choice
        n += 1
    db.conn.commit()
    return n


def choose(db: Database, resolver: Any, bus: Any, cluster_id: str, reason: str,
           user: str) -> dict:
    """The analyst picks the subject: its entities are confirmed, the other
    people's rejected (analyst verdicts, so they stand). Screening hits are
    never confirmed this way; they keep their own review."""
    from . import review

    row = db.get("identity_clusters", cluster_id)
    if row is None:
        raise IdentityError("no such identity")
    inv_id = row["investigation_id"]
    targets = review.target_entity_ids(db, inv_id)
    db.conn.execute("UPDATE identity_clusters SET chosen = CASE id WHEN ? THEN 1 ELSE 0 END "
                    "WHERE investigation_id = ?", (cluster_id, inv_id))
    db.conn.commit()
    mine = set(row["entity_ids"] or [])
    done = {"confirmed": 0, "rejected": 0}
    for other in clusters(db, inv_id):
        verdict = "confirmed" if other["id"] == cluster_id else "rejected"
        why = (f"The analyst identified the subject as \"{row['label']}\": {reason}"
               if verdict == "confirmed" else
               f"A different person (\"{other['label']}\"); the analyst identified the "
               f"subject as \"{row['label']}\": {reason}")
        for eid in other["entity_ids"] or []:
            ent = db.get("entities", eid)
            if ent is None or eid in targets or (verdict == "rejected" and eid in mine):
                continue
            if verdict == "confirmed" and ent["status"] == "candidate":
                continue
            if ent["status"] == verdict:
                continue
            review.record(db, resolver, bus, eid, verdict, why[:600], reviewer=user,
                          user=user, by_agent=False)
            done[verdict] += 1
    db.audit(user=user, action="identity.choose", investigation_id=inv_id,
             target=row["label"], detail={"cluster_id": cluster_id, "reason": reason, **done})
    bus.publish(inv_id, "identity.chosen", {"cluster_id": cluster_id, **done})
    return {"cluster": db.get("identity_clusters", cluster_id), **done}
