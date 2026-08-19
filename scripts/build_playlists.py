#!/usr/bin/env python3

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json
import re
import urllib.request


WORLD_SOURCE_URL = "https://iptv-org.github.io/iptv/index.country.m3u"
ASIA_SOURCE_URL = "https://iptv-org.github.io/iptv/regions/asia.m3u"

USER_AGENT = (
    "bashariptv-builder/2.0 "
    "(+https://github.com/aidatalensltd-cpu/bashariptv)"
)

TOP_PRIORITY = [
    "Bangladesh",
    "India",
    "Pakistan",
]

GROUP_RE = re.compile(r'group-title="([^"]*)"')
TVG_ID_RE = re.compile(r'tvg-id="([^"]*)"')


def download_text(url: str) -> str:

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/x-mpegURL,text/plain,*/*",
        },
    )

    with urllib.request.urlopen(
        request,
        timeout=120,
    ) as response:
        raw = response.read()

    if len(raw) < 50000:
        raise RuntimeError(
            f"Downloaded source is suspiciously small: "
            f"{len(raw):,} bytes from {url}"
        )

    return raw.decode("utf-8-sig")


def parse_m3u(
    text: str,
    require_country: bool,
):

    lines = (
        text
        .replace("\r\n", "\n")
        .replace("\r", "\n")
        .split("\n")
    )

    if not lines or not lines[0].startswith("#EXTM3U"):
        raise RuntimeError(
            "Source is not a valid Extended M3U playlist."
        )

    header = lines[0].strip()

    records = []

    current = None

    for line_no, raw in enumerate(
        lines[1:],
        start=2,
    ):

        line = raw.strip()

        if not line:
            continue

        if line.startswith("#EXTINF:"):

            if current is not None:
                raise RuntimeError(
                    f"Malformed M3U near line {line_no}: "
                    "previous channel has no URL."
                )

            current = [line]

            continue

        if current is None:
            continue

        if line.startswith("#"):

            current.append(line)

            continue

        # First non-comment line following EXTINF is the stream URL.

        current.append(line)

        extinf = current[0]

        group_match = GROUP_RE.search(extinf)

        country = (
            group_match.group(1).strip()
            if group_match
            else ""
        )

        if require_country and not country:

            raise RuntimeError(
                f"World channel near line {line_no} "
                "has no country group-title."
            )

        tvg_match = TVG_ID_RE.search(extinf)

        tvg_id = (
            tvg_match.group(1).strip()
            if tvg_match
            else ""
        )

        records.append(
            {
                "country": country,
                "tvg_id": tvg_id,
                "url": line.strip(),
                "lines": current,
            }
        )

        current = None

    if current is not None:

        raise RuntimeError(
            "Final EXTINF record has no stream URL."
        )

    return header, records


def stream_key(record):

    return (
        record["tvg_id"],
        record["url"],
    )


def priority_key(
    record,
    asia_keys,
):

    country = record["country"]

    if country == "Bangladesh":

        bucket = 0

    elif country == "India":

        bucket = 1

    elif country == "Pakistan":

        bucket = 2

    elif stream_key(record) in asia_keys:

        bucket = 3

    else:

        bucket = 4

    return (
        bucket,
        country.casefold(),
        record["lines"][0].casefold(),
        record["url"],
    )


def split_extinf_title(extinf: str):

    in_quotes = False

    for index, char in enumerate(extinf):

        if (
            char == '"'
            and (
                index == 0
                or extinf[index - 1] != "\\"
            )
        ):
            in_quotes = not in_quotes

        elif char == "," and not in_quotes:

            return (
                extinf[:index],
                extinf[index + 1:],
            )

    raise RuntimeError(
        "EXTINF record has no title separator."
    )


def render(
    header,
    records,
):

    output = [header]

    for record in records:

        lines = list(record["lines"])

        metadata, channel_name = (
            split_extinf_title(lines[0])
        )

        country = record["country"].strip()

        if country:

            visible_country = (
                "Unknown"
                if country == "Undefined"
                else country
            )

            prefix = f"{visible_country} | "

            if not channel_name.startswith(prefix):

                lines[0] = (
                    f"{metadata},"
                    f"{prefix}"
                    f"{channel_name}"
                )

        output.extend(lines)

    return "\n".join(output) + "\n"


def atomic_write(
    path: Path,
    text: str,
):

    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temp = path.with_suffix(
        path.suffix + ".tmp"
    )

    temp.write_text(
        text,
        encoding="utf-8",
        newline="\n",
    )

    temp.replace(path)


def main():

    print("")
    print("Downloading WORLD IPTV source...")

    world_text = download_text(
        WORLD_SOURCE_URL
    )

    print(
        "Downloading official ASIA IPTV region source..."
    )

    asia_text = download_text(
        ASIA_SOURCE_URL
    )

    print(
        "Parsing WORLD playlist..."
    )

    world_header, world_records = parse_m3u(
        world_text,
        require_country=True,
    )

    print(
        "Parsing official ASIA playlist..."
    )

    _, official_asia_records = parse_m3u(
        asia_text,
        require_country=False,
    )

    # --------------------------------------------------------
    # WORLD VALIDATION
    # --------------------------------------------------------

    world_country_counts = Counter(
        record["country"]
        for record in world_records
    )

    if len(world_records) < 1000:

        raise RuntimeError(
            f"World source has only "
            f"{len(world_records):,} entries. "
            "Refusing partial rebuild."
        )

    if len(world_country_counts) < 100:

        raise RuntimeError(
            f"World source has only "
            f"{len(world_country_counts):,} "
            "countries/territories. "
            "Refusing partial rebuild."
        )

    for required in TOP_PRIORITY:

        if world_country_counts[required] == 0:

            raise RuntimeError(
                f"Required priority country "
                f"is missing: {required}"
            )

    # --------------------------------------------------------
    # OFFICIAL ASIA VALIDATION
    # --------------------------------------------------------

    if len(official_asia_records) < 1000:

        raise RuntimeError(
            f"Official Asia source has only "
            f"{len(official_asia_records):,} entries. "
            "Refusing partial rebuild."
        )

    world_key_counts = Counter(
        stream_key(record)
        for record in world_records
    )

    asia_key_counts = Counter(
        stream_key(record)
        for record in official_asia_records
    )

    # Make sure every official Asia stream exists
    # in the worldwide master source.

    missing_asia = (
        asia_key_counts
        - world_key_counts
    )

    missing_asia_count = sum(
        missing_asia.values()
    )

    if missing_asia_count:

        print("")
        print(
            "WARNING: World/Asia sources are "
            "temporarily out of sync."
        )

        print(
            f"Official Asia records not found "
            f"in world source: "
            f"{missing_asia_count:,}"
        )

        print(
            "Refusing rebuild so an Asia-priority "
            "stream cannot silently be lost."
        )

        raise RuntimeError(
            "Asia/world source synchronization check failed."
        )

    asia_keys = set(
        asia_key_counts.keys()
    )

    # --------------------------------------------------------
    # PRIORITY:
    #
    # 1. Bangladesh
    # 2. India
    # 3. Pakistan
    # 4. Official Asia region
    # 5. Remaining worldwide
    # --------------------------------------------------------

    ordered_world = sorted(
        world_records,
        key=lambda record:
            priority_key(
                record,
                asia_keys,
            ),
    )

    bangladesh_records = [
        record
        for record in ordered_world
        if record["country"] == "Bangladesh"
    ]

    india_records = [
        record
        for record in ordered_world
        if record["country"] == "India"
    ]

    pakistan_records = [
        record
        for record in ordered_world
        if record["country"] == "Pakistan"
    ]

    asia_records = [
        record
        for record in ordered_world
        if stream_key(record) in asia_keys
    ]

    worldwide_remaining = [
        record
        for record in ordered_world
        if (
            record["country"]
            not in TOP_PRIORITY
            and stream_key(record)
            not in asia_keys
        )
    ]

    # --------------------------------------------------------
    # OUTPUT PLAYLISTS
    # --------------------------------------------------------

    playlists = {
        "playlist.m3u":
            ordered_world,

        "bangladesh.m3u":
            bangladesh_records,

        "india.m3u":
            india_records,

        "pakistan.m3u":
            pakistan_records,

        "asia.m3u":
            asia_records,
    }

    for filename, records in playlists.items():

        atomic_write(
            Path(filename),
            render(
                world_header,
                records,
            ),
        )

    # --------------------------------------------------------
    # VALIDATE FINAL PRIORITY
    # --------------------------------------------------------

    first_countries = []

    for record in ordered_world:

        country = record["country"]

        if country not in first_countries:

            first_countries.append(
                country
            )

        if len(first_countries) >= 3:
            break

    if first_countries != TOP_PRIORITY:

        raise RuntimeError(
            "Priority validation failed. "
            f"First countries: {first_countries}"
        )

    # --------------------------------------------------------
    # DUPLICATE INFORMATION
    # --------------------------------------------------------

    url_counts = Counter(
        record["url"]
        for record in world_records
    )

    duplicate_urls = sum(
        1
        for count
        in url_counts.values()
        if count > 1
    )

    # --------------------------------------------------------
    # COUNTRY REPORT
    # --------------------------------------------------------

    ordered_country_counts = {}

    for record in ordered_world:

        country = record["country"]

        if country not in ordered_country_counts:

            ordered_country_counts[country] = (
                world_country_counts[country]
            )

    report = {

        "world_source":
            WORLD_SOURCE_URL,

        "official_asia_source":
            ASIA_SOURCE_URL,

        "generated_at_utc":
            datetime.now(
                timezone.utc
            ).isoformat(),

        "priority_order": [
            "Bangladesh",
            "India",
            "Pakistan",
            "Official Asia Region",
            "Remaining Worldwide",
        ],

        "world_total_stream_entries":
            len(world_records),

        "countries_and_territories":
            len(world_country_counts),

        "bangladesh_stream_entries":
            len(bangladesh_records),

        "india_stream_entries":
            len(india_records),

        "pakistan_stream_entries":
            len(pakistan_records),

        "official_asia_source_entries":
            len(official_asia_records),

        "asia_entries_in_master":
            len(asia_records),

        "asia_source_unmatched_entries":
            missing_asia_count,

        "remaining_world_entries":
            len(worldwide_remaining),

        "exact_stream_urls_reused":
            duplicate_urls,

        "channels_by_country":
            ordered_country_counts,

        "validation": {

            "world_m3u_structure":
                "passed",

            "asia_m3u_structure":
                "passed",

            "world_country_guard":
                "passed",

            "official_asia_membership":
                "passed",

            "bangladesh_priority":
                "passed",

            "india_priority":
                "passed",

            "pakistan_priority":
                "passed",

            "asia_priority":
                "passed",

            "playback_note":
                (
                    "Playlist structure and official "
                    "Asia membership are validated. "
                    "Individual live streams can still "
                    "be geo-blocked, ISP-restricted, "
                    "temporarily offline or require "
                    "provider-specific headers."
                ),
        },
    }

    atomic_write(
        Path(
            "reports/world-playlist-report.json"
        ),
        json.dumps(
            report,
            ensure_ascii=False,
            indent=2,
        ) + "\n",
    )

    # --------------------------------------------------------
    # FINAL CONSOLE REPORT
    # --------------------------------------------------------

    print("")
    print(
        "=============================================="
    )

    print(
        " BASHAR IPTV WORLD BUILD COMPLETED"
    )

    print(
        "=============================================="
    )

    print(
        f"World Total          : "
        f"{len(world_records):,}"
    )

    print(
        f"Countries/Regions    : "
        f"{len(world_country_counts):,}"
    )

    print(
        f"Bangladesh           : "
        f"{len(bangladesh_records):,}"
    )

    print(
        f"India                : "
        f"{len(india_records):,}"
    )

    print(
        f"Pakistan             : "
        f"{len(pakistan_records):,}"
    )

    print(
        f"Official Asia Source : "
        f"{len(official_asia_records):,}"
    )

    print(
        f"Asia In Master       : "
        f"{len(asia_records):,}"
    )

    print(
        f"Asia Missing         : "
        f"{missing_asia_count:,}"
    )

    print(
        f"Remaining Worldwide  : "
        f"{len(worldwide_remaining):,}"
    )

    print(
        "=============================================="
    )

    print("")
    print(
        "Priority validation:"
    )

    print(
        "Bangladesh -> India -> Pakistan "
        "-> Official Asia -> Worldwide"
    )

    print("")
    print(
        "Build is structurally ready for review."
    )


if __name__ == "__main__":

    main()
