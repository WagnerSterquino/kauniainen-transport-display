import os
import math
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field


# ---------------------------------------------------------
# Paths and environment
# ---------------------------------------------------------

BASE_DIR = Path(__file__).resolve().parent.parent

load_dotenv(BASE_DIR / ".env")

API_KEY = os.getenv("DIGITRANSIT_API_KEY")

if not API_KEY:
    raise RuntimeError(
        "DIGITRANSIT_API_KEY was not found in .env"
    )


STOPS_CONFIG_FILE = BASE_DIR / "config" / "stops.yaml"
LOCATION_CONFIG_FILE = BASE_DIR / "config" / "location.yaml"

INDEX_FILE = BASE_DIR / "static" / "index.html"
SETTINGS_FILE = BASE_DIR / "static" / "settings.html"


# ---------------------------------------------------------
# External services
# ---------------------------------------------------------

DIGITRANSIT_URL = (
    "https://api.digitransit.fi/"
    "routing/v2/hsl/gtfs/v1"
)

NOMINATIM_URL = (
    "https://nominatim.openstreetmap.org/search"
)

HELSINKI_TZ = ZoneInfo(
    "Europe/Helsinki"
)


DIGITRANSIT_HEADERS = {
    "Content-Type": "application/json",
    "digitransit-subscription-key": API_KEY,
}


# ---------------------------------------------------------
# Runtime configuration
# ---------------------------------------------------------

REFERENCE_NAME = "Home"
HOME_LAT = 0.0
HOME_LON = 0.0

SEARCH_RADIUS_METERS = 500
SAFETY_MARGIN_MINUTES = 1.0


STOP_OVERRIDES = {
    "include": [],
    "exclude": [],
    "labels": {},
}


walking_cache = {}
nearby_stops_cache = None


# ---------------------------------------------------------
# Load configuration
# ---------------------------------------------------------

def load_runtime_config():

    global REFERENCE_NAME
    global HOME_LAT
    global HOME_LON
    global SEARCH_RADIUS_METERS
    global SAFETY_MARGIN_MINUTES
    global STOP_OVERRIDES

    global walking_cache
    global nearby_stops_cache


    with open(
        LOCATION_CONFIG_FILE,
        "r",
        encoding="utf-8",
    ) as file:

        location_config = yaml.safe_load(file)


    reference = location_config["reference"]


    REFERENCE_NAME = reference.get(
        "name",
        "Home",
    )


    HOME_LAT = float(
        reference["latitude"]
    )


    HOME_LON = float(
        reference["longitude"]
    )


    SEARCH_RADIUS_METERS = int(
        reference.get(
            "search_radius_m",
            500,
        )
    )


    SAFETY_MARGIN_MINUTES = float(
        reference.get(
            "safety_margin_minutes",
            1,
        )
    )


    STOP_OVERRIDES = {
        "include": [],
        "exclude": [],
        "labels": {},
    }


    if STOPS_CONFIG_FILE.exists():

        with open(
            STOPS_CONFIG_FILE,
            "r",
            encoding="utf-8",
        ) as file:

            stops_config = (
                yaml.safe_load(file)
                or {}
            )


        overrides = (
            stops_config.get(
                "overrides",
                {},
            )
            or {}
        )


        STOP_OVERRIDES["include"] = (
            overrides.get(
                "include",
                [],
            )
            or []
        )


        STOP_OVERRIDES["exclude"] = (
            overrides.get(
                "exclude",
                [],
            )
            or []
        )


        STOP_OVERRIDES["labels"] = (
            overrides.get(
                "labels",
                {},
            )
            or {}
        )


    # Configuration changed:
    # previous walking/stops cache is no longer valid.
    walking_cache = {}
    nearby_stops_cache = None


load_runtime_config()


# ---------------------------------------------------------
# API input models
# ---------------------------------------------------------

class LocationSettings(BaseModel):

    name: str = Field(
        min_length=1,
        max_length=80,
    )

    latitude: float = Field(
        ge=-90,
        le=90,
    )

    longitude: float = Field(
        ge=-180,
        le=180,
    )

    search_radius_m: int = Field(
        ge=50,
        le=2000,
    )

    safety_margin_minutes: float = Field(
        ge=0,
        le=10,
    )


class StopOverrideSettings(BaseModel):

    include: list[str] = []
    exclude: list[str] = []


class AddressSearch(BaseModel):

    address: str = Field(
        min_length=3,
        max_length=200,
    )


# ---------------------------------------------------------
# GraphQL queries
# ---------------------------------------------------------

NEARBY_STOPS_QUERY = """
query NearbyStops(
    $lat: Float!,
    $lon: Float!,
    $radius: Int!
) {

  stopsByRadius(
    lat: $lat
    lon: $lon
    radius: $radius
  ) {

    edges {

      node {

        distance

        stop {

          gtfsId
          name
          code
          lat
          lon
          platformCode

          stoptimesWithoutPatterns(
            numberOfDepartures: 20
          ) {

            headsign

            trip {
              route {
                shortName
                mode
              }
            }

          }
        }
      }
    }
  }
}
"""


STOP_INFO_QUERY = """
query StopInfo($id: String!) {

  stop(id: $id) {

    gtfsId
    name
    code
    lat
    lon
    platformCode

    stoptimesWithoutPatterns(
      numberOfDepartures: 20
    ) {

      headsign

      trip {
        route {
          shortName
          mode
        }
      }
    }
  }
}
"""


DEPARTURE_QUERY = """
query StopDepartures($id: String!) {

  stop(id: $id) {

    gtfsId
    name
    code
    lat
    lon
    platformCode

    stoptimesWithoutPatterns(
      numberOfDepartures: 12
    ) {

      scheduledDeparture
      realtimeDeparture
      realtime
      serviceDay
      headsign

      trip {
        route {
          shortName
          mode
        }
      }
    }
  }
}
"""


WALKING_QUERY = """
query WalkingRoute(
    $fromLat: Float!,
    $fromLon: Float!,
    $toLat: Float!,
    $toLon: Float!
) {

  plan(

    from: {
      lat: $fromLat
      lon: $fromLon
    }

    to: {
      lat: $toLat
      lon: $toLon
    }

    transportModes: [
      {
        mode: WALK
      }
    ]

    numItineraries: 1

  ) {

    itineraries {

      duration
      walkDistance

    }
  }
}
"""


# ---------------------------------------------------------
# FastAPI
# ---------------------------------------------------------

app = FastAPI(
    title="Kauniainen Public Transport Display",
    version="0.7.0",
)


# ---------------------------------------------------------
# Digitransit helper
# ---------------------------------------------------------

def call_digitransit(
    query,
    variables,
):

    try:

        response = requests.post(
            DIGITRANSIT_URL,
            json={
                "query": query,
                "variables": variables,
            },
            headers=DIGITRANSIT_HEADERS,
            timeout=15,
        )

        response.raise_for_status()

    except requests.exceptions.RequestException as error:

        raise HTTPException(
            status_code=503,
            detail=(
                "Digitransit connection failed: "
                f"{error}"
            ),
        )


    result = response.json()


    if "errors" in result:

        raise HTTPException(
            status_code=502,
            detail=result["errors"],
        )


    return result["data"]


# ---------------------------------------------------------
# Stop helpers
# ---------------------------------------------------------

def simplify_direction(
    destinations,
):

    if not destinations:
        return ""


    cleaned = []


    for destination in destinations:

        text = destination


        for phrase in [
            " via Kauniainen as.",
            " via Kauniainen",
            " via Kauklahti",
            " via Kirkkonummi",
        ]:

            text = text.replace(
                phrase,
                "",
            )


        text = text.strip()


        if (
            text
            and text not in cleaned
        ):

            cleaned.append(
                text
            )


    return " / ".join(
        cleaned[:2]
    )


def analyze_stop(
    stop,
    distance=None,
):

    modes = set()
    lines = set()
    destinations = set()


    for item in (
        stop.get(
            "stoptimesWithoutPatterns",
            [],
        )
        or []
    ):

        route = (
            item.get(
                "trip",
                {},
            )
            .get(
                "route",
                {},
            )
        )


        mode = route.get(
            "mode"
        )


        line = route.get(
            "shortName"
        )


        destination = item.get(
            "headsign"
        )


        if mode:
            modes.add(mode)


        if line:
            lines.add(line)


        if destination:
            destinations.add(
                destination
            )


    useful_modes = modes.intersection(
        {
            "BUS",
            "RAIL",
        }
    )


    if not useful_modes:
        return None


    mode = (
        "RAIL"
        if "RAIL" in useful_modes
        else "BUS"
    )


    stop_id = stop["gtfsId"]

    code = stop.get(
        "code"
    ) or "-"

    name = stop["name"]

    platform = stop.get(
        "platformCode"
    ) or "-"


    direction = simplify_direction(
        sorted(
            destinations
        )
    )


    custom_label = (
        STOP_OVERRIDES[
            "labels"
        ].get(
            stop_id
        )
    )


    if custom_label:

        label = custom_label


    elif mode == "RAIL":

        label = (
            f"🚆 {name}"
            f" · Platform {platform}"
        )

        if direction:
            label += (
                f" → {direction}"
            )


    else:

        label = (
            f"🚌 {name}"
            f" · {code}"
        )

        if direction:
            label += (
                f" → {direction}"
            )


    return {

        "id":
            stop_id,

        "code":
            code,

        "name":
            name,

        "label":
            label,

        "type":
            (
                "TRAIN"
                if mode == "RAIL"
                else "BUS"
            ),

        "mode":
            mode,

        "platform":
            platform,

        "lat":
            stop.get("lat"),

        "lon":
            stop.get("lon"),

        "distance_from_reference_m":
            (
                round(distance)
                if distance is not None
                else None
            ),

        "lines":
            sorted(lines),

        "directions":
            sorted(destinations),
    }


def get_single_stop_info(
    stop_id,
):

    data = call_digitransit(
        STOP_INFO_QUERY,
        {
            "id":
                stop_id,
        },
    )


    stop = data.get(
        "stop"
    )


    if not stop:
        return None


    return analyze_stop(
        stop
    )


def discover_nearby_stops():

    global nearby_stops_cache


    if nearby_stops_cache is not None:

        return nearby_stops_cache


    data = call_digitransit(
        NEARBY_STOPS_QUERY,
        {
            "lat":
                HOME_LAT,

            "lon":
                HOME_LON,

            "radius":
                SEARCH_RADIUS_METERS,
        },
    )


    edges = (
        data
        .get(
            "stopsByRadius",
            {},
        )
        .get(
            "edges",
            [],
        )
    )


    discovered = {}


    for edge in edges:

        node = edge["node"]
        stop = node["stop"]


        stop_info = analyze_stop(
            stop,
            node.get(
                "distance"
            ),
        )


        if not stop_info:
            continue


        stop_id = stop_info["id"]


        if (
            stop_id
            in STOP_OVERRIDES[
                "exclude"
            ]
        ):
            continue


        discovered[
            stop_id
        ] = stop_info


    # Force include specific stops,
    # even when slightly outside radius.
    for stop_id in (
        STOP_OVERRIDES[
            "include"
        ]
    ):

        if (
            stop_id
            in STOP_OVERRIDES[
                "exclude"
            ]
        ):
            continue


        if stop_id in discovered:
            continue


        stop_info = get_single_stop_info(
            stop_id
        )


        if stop_info:

            discovered[
                stop_id
            ] = stop_info


    result = list(
        discovered.values()
    )


    # Trains first, then distance.
    result.sort(
        key=lambda stop: (

            0
            if stop["type"] == "TRAIN"
            else 1,

            (
                stop[
                    "distance_from_reference_m"
                ]
                if stop[
                    "distance_from_reference_m"
                ]
                is not None
                else 999999
            ),

            stop["name"],

            stop["code"],
        )
    )


    for index, stop in enumerate(
        result,
        start=1,
    ):

        stop["index"] = index


    nearby_stops_cache = result


    return nearby_stops_cache


def get_configured_stop(
    stop_id,
):

    for stop in discover_nearby_stops():

        if stop["id"] == stop_id:
            return stop


    return None


# ---------------------------------------------------------
# Walking helpers
# ---------------------------------------------------------

def get_walking_info(
    stop_id,
    lat,
    lon,
):

    if stop_id in walking_cache:

        return walking_cache[
            stop_id
        ]


    data = call_digitransit(
        WALKING_QUERY,
        {
            "fromLat":
                HOME_LAT,

            "fromLon":
                HOME_LON,

            "toLat":
                lat,

            "toLon":
                lon,
        },
    )


    itineraries = (
        data
        .get(
            "plan",
            {},
        )
        .get(
            "itineraries",
            [],
        )
    )


    if not itineraries:

        walking_cache[
            stop_id
        ] = {

            "seconds":
                None,

            "minutes_exact":
                None,

            "minutes_display":
                None,

            "distance":
                None,
        }


        return walking_cache[
            stop_id
        ]


    itinerary = itineraries[0]


    walking_seconds = (
        itinerary["duration"]
    )


    walking_distance = (
        itinerary["walkDistance"]
    )


    minutes_exact = (
        walking_seconds
        / 60
    )


    minutes_display = max(
        1,
        math.ceil(
            minutes_exact
        ),
    )


    walking_cache[
        stop_id
    ] = {

        "seconds":
            walking_seconds,

        "minutes_exact":
            minutes_exact,

        "minutes_display":
            minutes_display,

        "distance":
            round(
                walking_distance
            ),
    }


    return walking_cache[
        stop_id
    ]


# ---------------------------------------------------------
# Departure helpers
# ---------------------------------------------------------

def create_departure_time(
    service_day,
    departure_seconds,
):

    timestamp = (
        service_day
        + departure_seconds
    )


    return datetime.fromtimestamp(
        timestamp,
        tz=HELSINKI_TZ,
    )


def calculate_status(
    minutes_until,
    walking_minutes_exact,
):

    if walking_minutes_exact is None:

        return {
            "code":
                "UNKNOWN",

            "emoji":
                "❔",
        }


    remaining_after_walk = (
        minutes_until
        - walking_minutes_exact
    )


    if remaining_after_walk < 0:

        return {
            "code":
                "TOO_LATE",

            "emoji":
                "❌",
        }


    if (
        remaining_after_walk
        <= SAFETY_MARGIN_MINUTES
    ):

        return {
            "code":
                "TIGHT",

            "emoji":
                "⚠️",
        }


    return {
        "code":
            "COMFORTABLE",

        "emoji":
            "✅",
    }


# ---------------------------------------------------------
# HTML pages
# ---------------------------------------------------------

@app.get("/")
def index():

    return FileResponse(
        INDEX_FILE
    )


@app.get("/settings")
def settings():

    return FileResponse(
        SETTINGS_FILE
    )


# ---------------------------------------------------------
# General configuration API
# ---------------------------------------------------------

@app.get("/api/config")
def get_app_config():

    return {

        "reference": {

            "name":
                REFERENCE_NAME,

            "lat":
                HOME_LAT,

            "lon":
                HOME_LON,

            "search_radius_m":
                SEARCH_RADIUS_METERS,

            "safety_margin_minutes":
                SAFETY_MARGIN_MINUTES,
        }

    }


# ---------------------------------------------------------
# Stop API
# ---------------------------------------------------------

@app.get("/api/stops")
def get_stops():

    return discover_nearby_stops()


@app.get("/api/nearby-stops")
def get_nearby_stops():

    return discover_nearby_stops()


# ---------------------------------------------------------
# Settings API
# ---------------------------------------------------------

@app.get("/api/settings")
def get_settings():

    return {

        "location": {

            "name":
                REFERENCE_NAME,

            "latitude":
                HOME_LAT,

            "longitude":
                HOME_LON,

            "search_radius_m":
                SEARCH_RADIUS_METERS,

            "safety_margin_minutes":
                SAFETY_MARGIN_MINUTES,
        },


        "stops": {

            "include":
                STOP_OVERRIDES[
                    "include"
                ],

            "exclude":
                STOP_OVERRIDES[
                    "exclude"
                ],
        },


        "discovered_stop_count":
            len(
                discover_nearby_stops()
            ),
    }


@app.post("/api/settings/location")
def save_location_settings(
    settings: LocationSettings,
):

    data = {

        "reference": {

            "name":
                settings.name,

            "latitude":
                settings.latitude,

            "longitude":
                settings.longitude,

            "search_radius_m":
                settings.search_radius_m,

            "safety_margin_minutes":
                settings.safety_margin_minutes,
        }

    }


    with open(
        LOCATION_CONFIG_FILE,
        "w",
        encoding="utf-8",
    ) as file:

        yaml.safe_dump(
            data,
            file,
            sort_keys=False,
            allow_unicode=True,
        )


    load_runtime_config()


    return {
        "status":
            "ok",

        "message":
            "Location settings saved.",
    }


@app.post("/api/settings/stops")
def save_stop_settings(
    settings: StopOverrideSettings,
):

    data = {

        "overrides": {

            "include":
                settings.include,

            "exclude":
                settings.exclude,

            "labels":
                STOP_OVERRIDES[
                    "labels"
                ],
        }

    }


    with open(
        STOPS_CONFIG_FILE,
        "w",
        encoding="utf-8",
    ) as file:

        yaml.safe_dump(
            data,
            file,
            sort_keys=False,
            allow_unicode=True,
        )


    load_runtime_config()


    return {
        "status":
            "ok",

        "message":
            "Stop overrides saved.",
    }


# ---------------------------------------------------------
# Address geocoding API
# ---------------------------------------------------------

@app.post("/api/geocode")
def geocode_address(
    search: AddressSearch,
):

    address = search.address.strip()


    try:

        response = requests.get(
            NOMINATIM_URL,
            params={
                "q":
                    address,

                "format":
                    "jsonv2",

                "limit":
                    5,

                # HSL project: keep results in Finland.
                "countrycodes":
                    "fi",

                "addressdetails":
                    1,
            },
            headers={
                "User-Agent":
                    (
                        "KauniainenPublicTransportDisplay/"
                        "1.0"
                    )
            },
            timeout=10,
        )


        response.raise_for_status()


    except requests.exceptions.RequestException as error:

        raise HTTPException(
            status_code=503,
            detail=(
                "Geocoding service unavailable: "
                f"{error}"
            ),
        )


    results = response.json()


    formatted = []


    for item in results:

        try:

            latitude = float(
                item["lat"]
            )

            longitude = float(
                item["lon"]
            )

        except (
            KeyError,
            TypeError,
            ValueError,
        ):

            continue


        formatted.append(
            {

                "display_name":
                    item.get(
                        "display_name",
                        address,
                    ),

                "latitude":
                    latitude,

                "longitude":
                    longitude,

                "type":
                    item.get(
                        "type"
                    ),

                "category":
                    item.get(
                        "category"
                    ),
            }
        )


    return formatted


# ---------------------------------------------------------
# Departures API
# ---------------------------------------------------------

@app.get("/api/departures")
def get_departures(
    stop_id: str = Query(...),
):

    configured_stop = get_configured_stop(
        stop_id
    )


    if not configured_stop:

        raise HTTPException(
            status_code=400,
            detail="Unknown stop ID",
        )


    data = call_digitransit(
        DEPARTURE_QUERY,
        {
            "id":
                stop_id,
        },
    )


    stop = data.get(
        "stop"
    )


    if not stop:

        raise HTTPException(
            status_code=404,
            detail="Stop not found",
        )


    walking = get_walking_info(
        stop_id,
        stop["lat"],
        stop["lon"],
    )


    now = datetime.now(
        HELSINKI_TZ
    )


    departures = []


    for item in (
        stop[
            "stoptimesWithoutPatterns"
        ]
    ):

        if item["realtime"]:

            departure_seconds = (
                item[
                    "realtimeDeparture"
                ]
            )

            source = "RT"


        else:

            departure_seconds = (
                item[
                    "scheduledDeparture"
                ]
            )

            source = "SCHED"


        departure_time = (
            create_departure_time(
                item["serviceDay"],
                departure_seconds,
            )
        )


        seconds_until = (
            departure_time
            - now
        ).total_seconds()


        if seconds_until < -30:
            continue


        minutes_until = max(
            0,
            int(
                seconds_until
                / 60
            ),
        )


        route = (
            item[
                "trip"
            ][
                "route"
            ]
        )


        status = calculate_status(
            minutes_until,
            walking[
                "minutes_exact"
            ],
        )


        departures.append(
            {

                "line":
                    route.get(
                        "shortName"
                    )
                    or "-",

                "mode":
                    route.get(
                        "mode"
                    )
                    or "-",

                "destination":
                    item.get(
                        "headsign"
                    )
                    or "-",

                "time":
                    departure_time.strftime(
                        "%H:%M"
                    ),

                "minutes":
                    minutes_until,

                "source":
                    source,

                "status":
                    status,
            }
        )


    return {

        "stop": {

            "id":
                stop["gtfsId"],

            "code":
                stop.get(
                    "code"
                ),

            "name":
                stop["name"],

            "platform":
                stop.get(
                    "platformCode"
                ),

            "lat":
                stop.get(
                    "lat"
                ),

            "lon":
                stop.get(
                    "lon"
                ),
        },


        "walking":
            walking,


        "updated":
            now.strftime(
                "%H:%M:%S"
            ),


        "departures":
            departures,
    }
