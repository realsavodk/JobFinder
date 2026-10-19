from __future__ import annotations

import re

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "DC": "District of Columbia", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii",
    "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming",
}  # fmt: skip

CA_PROVINCES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories",
    "NU": "Nunavut", "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon",
}  # fmt: skip

# Postings often name only a city. shortcut: major job-market cities only; add
# more here when a posting's city isn't picked up.
_CITIES = {
    "san francisco": "CA", "sf": "CA", "bay area": "CA", "palo alto": "CA",
    "mountain view": "CA", "menlo park": "CA", "san jose": "CA", "sunnyvale": "CA",
    "oakland": "CA", "los angeles": "CA", "san diego": "CA", "irvine": "CA",
    "seattle": "WA", "bellevue": "WA", "redmond": "WA", "new york city": "NY",
    "nyc": "NY", "brooklyn": "NY", "chicago": "IL", "boston": "MA", "austin": "TX",
    "dallas": "TX", "houston": "TX",
    "denver": "CO", "boulder": "CO", "atlanta": "GA", "miami": "FL",
    "washington, dc": "DC", "washington dc": "DC", "philadelphia": "PA",
    "pittsburgh": "PA", "portland": "OR", "salt lake city": "UT", "phoenix": "AZ",
    "minneapolis": "MN", "detroit": "MI", "raleigh": "NC", "nashville": "TN",
    "toronto": "ON", "ottawa": "ON", "waterloo": "ON", "kitchener": "ON",
    "vancouver": "BC", "montreal": "QC", "montréal": "QC", "calgary": "AB",
    "edmonton": "AB", "winnipeg": "MB", "halifax": "NS",
    # Abbreviations seen in real postings ("SF, SEA, NYC, CHI").
    "sea": "WA", "chi": "IL", "atl": "GA", "tor": "ON", "new-york": "NY",
}  # fmt: skip

_ALL = {**US_STATES, **CA_PROVINCES}
_NAME_RE = {
    code: re.compile(rf"\b{re.escape(name.lower())}\b") for code, name in _ALL.items()
}
_CITY_RE = [
    (re.compile(rf"(?<!\w){re.escape(city)}(?!\w)"), code)
    for city, code in _CITIES.items()
]
# Codes only count as standalone uppercase tokens ("Seattle, WA", "USA.VA.Reston"),
# so words like "in" or "me" never match.
_CODE_RE = re.compile(r"(?<![A-Za-z])([A-Z]{2})(?![A-Za-z])")
# "Dublin OR London", "IN - Bengaluru": these two only count after a comma.
_WORD_CODE_RE = re.compile(r",\s*(OR|IN)(?![A-Za-z])")


def regions_in(location: str) -> set[str]:
    """US state / Canadian province codes a free-text location refers to."""
    lower = location.lower()
    found = {code for code, pattern in _NAME_RE.items() if pattern.search(lower)}
    found |= {code for pattern, code in _CITY_RE if pattern.search(lower)}
    codes = {
        m for m in _CODE_RE.findall(location) if m in _ALL and m not in ("OR", "IN")
    }
    codes |= set(_WORD_CODE_RE.findall(location))
    # "Toronto, CA" / "CA-Toronto" mean Canada, not California.
    if "CA" in codes and "CA" not in found and found & CA_PROVINCES.keys():
        codes.discard("CA")
    found |= codes
    # "Washington, DC" also matches the state name Washington.
    if "DC" in found and "WA" in found and "seattle" not in lower:
        found.discard("WA")
    return found
