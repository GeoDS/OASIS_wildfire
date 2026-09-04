# Showcase data

Snapshots of public data for the Altadena, California study area — the Eaton Fire of
January 2025. Regenerate with:

```bash
cd backend && uv run python scripts/fetch_showcase_data.py
```

They are committed so the demo does not depend on network access, upstream availability, or a
rate limit at the wrong moment.

## Sources and attribution

All three are public and require no API key. Each file carries a `provenance` block recording its
source, its as-of date, and when it was retrieved.

| File | Source | Terms |
|---|---|---|
| `altadena/official_fire_perimeters.geojson` | Los Angeles County Department of Public Works — Eaton Fire perimeter as of 2025-01-21 | US local government open data |
| `altadena/satellite_hotspots.geojson` | NOAA NESDIS Hazard Mapping System, daily fire product archive, 8–9 January 2025 | US federal government work, public domain |
| `altadena/historical_fire_perimeters.geojson` | National Interagency Fire Center — InteragencyFirePerimeterHistory, 2000 onwards | US federal government work, public domain |

Basemap tiles are served at runtime by CARTO from OpenStreetMap data, attributed in the map
itself; none of it is redistributed here.

## What these snapshots are not

- **Not live.** They describe January 2025. Any answer built on them says so.
- **Not complete.** The historical layer lags national reporting by about a year, so the Eaton
  Fire itself does not appear in it. Perimeters carry no burn severity.
- **Not interchangeable.** A satellite thermal detection is a heat signature, not a confirmed
  wildfire; it may be an agricultural burn or an industrial source. The confirmed perimeter and the
  detections answer different questions, which is precisely why the system asks the user which one
  they mean.

## The TS-SatFire archive is not in this repository

Everything above is the Altadena showcase snapshot, and it is all this directory
contains. The derived analyses — fire lifecycle, burn severity (dNBR), NDVI
change, land cover, spread behaviour and fire weather — read a separate
multi-gigabyte TS-SatFire subset that is **not redistributed here**.

Without it the backend starts normally and the catalogue is simply empty:
`GET /api/local-data/fire-events` returns `event_count: 0` and any question about
a historical fire finds no match. Nothing errors, so this is worth knowing before
you conclude something is broken.

### Getting it

Download the archive from
<https://drive.google.com/drive/folders/14AgkvlPX2Mae20yv7Gm9NvKPnQEJnx4w>, then put
the `full_data/` folder anywhere outside the repository and point `LOCAL_DATA_ROOT`
at its **parent**:

```bash
# e.g. unpacked alongside the checkout
#   ~/firescope/data/full_data/...
#   ~/firescope/OASIS_wildfire/      <- this repository
echo 'LOCAL_DATA_ROOT=/absolute/path/to/firescope/data' >> .env
```

`LOCAL_DATA_ROOT` is the directory that *contains* `full_data/`, not `full_data/`
itself. Keep it outside the checkout: the archive is roughly 1.9 GB and must never
end up in a commit.

Verify it resolved with `curl -s localhost:8000/api/local-data/fire-events` — a
working setup reports `"event_count": 9`.

### Layout

Point `LOCAL_DATA_ROOT` at a directory holding your own copy, laid out as:

```
<LOCAL_DATA_ROOT>/
  full_data/
    <event_id>/                     e.g. 24461771, thomas_fire
      VIIRS_Day/                    YYYY-MM-DD_VIIRS_Day.tif    (8 bands)
      VIIRS_Night/                  YYYY-MM-DD_VIIRS_Night.tif  (2 bands)
      FirePred/                     YYYY-MM-DD_FirePred.tif     (19 bands)
      ESRI_LULC/                    YYYY-01-01_ESRI_LULC.tif    (1 band)
```

The subset this was built against holds nine Southern California events between
2017 and 2021, each a daily stack on a shared 594 × 596 grid. Band meanings that
the code depends on:

| Variable | Band | Meaning |
|---|---|---|
| `VIIRS_Day` | 1, 2, 3, 6 | red, near-infrared, SWIR 1.6, SWIR 2.2 reflectance (percent) |
| `VIIRS_Day` | 7 | active-fire label — presence, not magnitude |
| `VIIRS_Day` | 8 | burned-area label — presence, not magnitude |
| `FirePred` | 1, 2 | `NDVI_last`, `EVI2_last`, scaled by 1e-4 |
| `FirePred` | 4, 5, 7, 8, 9, 13 | wind speed and direction, max temperature, energy release component, specific humidity, PDSI |
| `FirePred` | 10, 11, 12 | slope, aspect, elevation |
| `FirePred` | 14 | `LC_Type1` — MODIS IGBP land cover |

`FirePred` bands 15–19 (forecast) use a different scale from their observed
counterparts — forecast wind direction runs −89 to 87, which is not a bearing — so
they are not read. Band 3 (precipitation) reads zero across the Bobcat Fire's dry
September but carries real values for other events, so it is sampled. Any band
added later needs those same two checks before anything reads it: that its scale
and units match the observed counterparts it will be compared against, and that a
zero is a measurement rather than an absence. Nothing downstream validates
either.
