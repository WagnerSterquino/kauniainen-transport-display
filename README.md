# Kauniainen Public Transport Display

Real-time HSL public transport display built with FastAPI and the Digitransit API.

The application automatically discovers nearby public transport stops around a configurable reference location, retrieves upcoming departures, calculates walking time to each stop and combines the information into a browser-based display.

## Features

- Real-time HSL departures
- Automatic nearby stop discovery
- Configurable reference location
- Configurable search radius
- Walking time and distance to stops
- Combined departure board
- Real-time and scheduled departure indication
- Walking feasibility status
- Interactive Leaflet / OpenStreetMap map
- Selectable transport points
- Browser persistence of selected stops
- Automatic refresh
- LIVE / OFFLINE indication
- Retains last successful data if connection is lost
- Browser-based Settings page
- Address search for reference location
- HSL-inspired interface

## Current Transport Modes

Currently supported:

- Bus
- Train

Planned:

- Tram
- Metro
- Ferry

Transport modes will also become configurable through the Settings page.

## Technology

- Python 3.12
- FastAPI
- Uvicorn
- Digitransit GraphQL API
- Leaflet
- OpenStreetMap
- HTML
- CSS
- JavaScript
- YAML

## Project Structure

    kauniainen-transport-display/
    ├── config/
    │   ├── location.example.yaml
    │   └── stops.example.yaml
    ├── src/
    │   └── main.py
    ├── static/
    │   ├── index.html
    │   └── settings.html
    ├── .env.example
    ├── .gitignore
    ├── requirements.txt
    └── README.md

Private local files are intentionally excluded from Git:

    .env
    config/location.yaml
    config/stops.yaml

## Installation

Clone the repository:

    git clone YOUR_REPOSITORY_URL
    cd kauniainen-transport-display

Create a Python virtual environment:

    python3 -m venv .venv
    source .venv/bin/activate

Install dependencies:

    pip install -r requirements.txt

## Digitransit API Key

Create the local environment file:

    cp .env.example .env

Then add your Digitransit subscription key:

    DIGITRANSIT_API_KEY=your_actual_api_key

The real .env file is excluded from Git.

## Location Configuration

Create the local configuration:

    cp config/location.example.yaml config/location.yaml

Example:

    reference:
      name: "Example Location"
      latitude: 60.169900
      longitude: 24.938400
      search_radius_m: 500
      safety_margin_minutes: 1

The real location file is excluded from Git so private residential coordinates are not published.

## Stop Overrides

Create the local file:

    cp config/stops.example.yaml config/stops.yaml

The application normally discovers stops automatically.

Overrides can be used to always include or exclude specific HSL stops.

## Running

Activate the environment:

    source .venv/bin/activate

Start the application:

    uvicorn src.main:app --host 0.0.0.0 --port 8000

Main display:

    http://SERVER-IP:8000

Settings:

    http://SERVER-IP:8000/settings

## Privacy

This public repository intentionally excludes:

- Personal API keys
- Residential addresses
- Exact private coordinates
- Private stop configuration
- Virtual environments
- Local test data

## Planned Development

- Bus / Train / Tram / Metro / Ferry support
- Configurable transport modes
- systemd service
- Chromium kiosk mode
- Automatic startup after boot

## Data Sources

Public transport information is provided by Digitransit.

Map data is provided by OpenStreetMap.

## Development Status

The project is currently a functional MVP with real-time departures, automatic stop discovery, walking calculations, connection-loss handling and browser-based configuration.
